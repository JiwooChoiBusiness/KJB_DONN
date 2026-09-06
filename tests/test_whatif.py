"""계산형 자유 질의(what-if) 테스트 (SPEC 2.13).

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처를 재사용한다(패턴은
tests/test_chat_stream.py, tests/test_answer_routes.py와 같다).
"""
from __future__ import annotations

import json
from datetime import date

from tests.test_api import _FakeUnavailableProvider, _FakeSuccessProvider, _clear_session, client

from app.api import routes as routes_module  # noqa: E402
from app.core import loan as loan_core  # noqa: E402
from app.core import retirement as retirement_core  # noqa: E402
from app.data import policy, synthetic  # noqa: E402
from app.llm import guardrails  # noqa: E402
from app.llm.provider import LLMResult  # noqa: E402
from app.services import explain as explain_service  # noqa: E402
from app.services import whatif as whatif_service  # noqa: E402

# ---------------------------------------------------------------------------
# (a) 규칙 파서 슬롯
# ---------------------------------------------------------------------------


def test_parse_message_extra_monthly_slot():
    result = guardrails.parse_message("매달 30만 원 더 갚으면 얼마나 빨리 끝나?")
    assert result["intent"] == "whatif"
    assert result["extra_monthly"] == 300_000
    assert "lump_sum" not in result


def test_parse_message_lump_sum_slot():
    result = guardrails.parse_message("1,000만원 한번에 갚으면 얼마나 빨리 끝나?")
    assert result["intent"] == "whatif"
    assert result["lump_sum"] == 10_000_000
    assert "extra_monthly" not in result


def test_parse_message_new_rate_requires_galatamyeon_combo():
    result = guardrails.parse_message("신용대출을 금리 4%로 갈아타면?")
    assert result["intent"] == "whatif"
    assert result["new_rate"] == 4.0
    assert result["loan_hint"] == "credit"


def test_parse_message_galata_without_rate_stays_compare():
    """"갈아타"만 있고 "갈아타면 + 금리 N%로" 조합이 아니면 여전히 compare다."""
    result = guardrails.parse_message("신용대출로 갈아타려는데 500만원, 12개월로 공시 비교해줘")
    assert result["intent"] == "compare"


def test_parse_message_retirement_age_slot():
    result = guardrails.parse_message("은퇴를 60세로 하면 노후 부족액은?")
    assert result["intent"] == "whatif"
    assert result["retirement_age"] == 60


def test_parse_message_retirement_age_alt_phrasing():
    result = guardrails.parse_message("60세에 은퇴하면 어떻게 될까?")
    assert result["intent"] == "whatif"
    assert result["retirement_age"] == 60


def test_parse_message_loan_hint_without_amount_still_detected():
    result = guardrails.parse_message("카드론을 한번에 갚으면 어떻게 되나요?")
    assert result["intent"] == "whatif"
    assert result["loan_hint"] == "card_loan"
    assert "lump_sum" not in result  # 금액이 없어 슬롯 자체는 비어 있다


# ---------------------------------------------------------------------------
# (b) 각 도구의 수치가 core 함수와 일치
# ---------------------------------------------------------------------------


def test_extra_payment_tool_matches_core_function():
    profile = synthetic.get_persona("P1")
    params = policy.load_policy_params()
    result = whatif_service.run_whatif(profile, params, {"extra_monthly": 300_000}, today=date.today())
    assert result is not None
    assert result.tool == "extra_payment"

    target = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
    assert target.id == "P1-L3"  # 카드 리볼빙, 17.5% (최고금리)
    expected = loan_core.extra_payment_effect(target, 300_000)
    assert result.deltas["months_saved"] == expected["months_saved"]
    assert result.deltas["interest_saved"] == expected["interest_saved"]
    assert result.after["months"] == expected["new_months"]


def test_lump_sum_tool_matches_core_function():
    profile = synthetic.get_persona("P2")
    params = policy.load_policy_params()
    today = date.today()
    result = whatif_service.run_whatif(profile, params, {"lump_sum": 5_000_000}, today=today)
    assert result is not None
    assert result.tool == "lump_sum"

    target = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
    assert target.id == "P2-L2"  # 신용대출, 5.8% (최고금리)
    fee = loan_core.prepay_fee(target, min(5_000_000, target.balance), today, params)
    expected = loan_core.lump_sum_effect(target, 5_000_000, fee)
    assert result.deltas["months_saved"] == expected["months_saved"]
    assert result.deltas["interest_saved"] == expected["interest_saved"]
    assert result.after["fee"] == fee


def test_refinance_tool_matches_core_function():
    profile = synthetic.get_persona("P7")
    params = policy.load_policy_params()
    today = date.today()
    result = whatif_service.run_whatif(
        profile, params, {"new_rate": 5.0, "loan_hint": "credit"}, today=today,
    )
    assert result is not None
    assert result.tool == "refinance"

    target = next(l for l in profile.loans if l.loan_type.value == "credit")
    assert target.id == "P7-L1"
    fee = loan_core.prepay_fee(target, target.balance, today, params)
    expected = loan_core.refinance_compare(target, 5.0, target.remaining_months, fee)
    assert result.deltas["monthly_delta"] == expected["monthly_after"] - expected["monthly_before"]
    assert result.deltas["total_cost_delta"] == expected["new_total_interest"] - expected["current_total_interest"]
    assert result.deltas["breakeven_months"] == expected["breakeven_months"]


def test_retirement_age_tool_matches_core_function():
    profile = synthetic.get_persona("P6")
    params = policy.load_policy_params()
    today = date.today()
    result = whatif_service.run_whatif(profile, params, {"retirement_age": 60}, today=today)
    assert result is not None
    assert result.tool == "retirement_age"

    before_list = retirement_core.retirement_gap_projection(profile, params, today=today)
    before = next(p for p in before_list if p.scenario == "기준")
    new_profile = profile.model_copy(update={"retirement_age": 60})
    after_list = retirement_core.retirement_gap_projection(new_profile, params, today=today)
    after = next(p for p in after_list if p.scenario == "기준")

    assert result.before["shortfall"] == before.shortfall
    assert result.after["shortfall"] == after.shortfall
    assert result.after["retirement_age"] == 60
    assert result.deltas["shortfall_delta"] == after.shortfall - before.shortfall
    assert result.deltas["required_monthly_saving_delta"] == (
        after.required_monthly_saving - before.required_monthly_saving
    )


# ---------------------------------------------------------------------------
# (c) 대상 대출 선택(hint 우선, 없으면 최고금리)
# ---------------------------------------------------------------------------


def test_target_loan_selection_prefers_hint_over_highest_rate():
    profile = synthetic.get_persona("P1")
    params = policy.load_policy_params()
    today = date.today()

    result_default = whatif_service.run_whatif(profile, params, {"extra_monthly": 100_000}, today=today)
    assert result_default is not None
    assert "카드론" in result_default.target_loan_label  # 최고금리(17.5%) 대출

    result_hint = whatif_service.run_whatif(
        profile, params, {"extra_monthly": 100_000, "loan_hint": "student"}, today=today,
    )
    assert result_hint is not None
    assert "학자금대출" in result_hint.target_loan_label


def test_run_whatif_none_without_profile_or_slots():
    params = policy.load_policy_params()
    profile = synthetic.get_persona("P1")
    assert whatif_service.run_whatif(None, params, {"extra_monthly": 100_000}, today=date.today()) is None
    assert whatif_service.run_whatif(profile, params, {}, today=date.today()) is None


# ---------------------------------------------------------------------------
# (d) /api/chat으로 4가지 질문이 200 · markdown · 리소스 calc · 칩 포함
# ---------------------------------------------------------------------------


def test_chat_whatif_extra_payment_returns_markdown_and_resources(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "매달 30만 원 더 갚으면 얼마나 빨리 끝나?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"
        assert body["route"] == "internal"
        assert "**계산 근거**" in body["reply_text"]
        kinds = {res["kind"] for res in body["resources"]}
        assert "calc" in kinds
        calc_res = next(res for res in body["resources"] if res["kind"] == "calc")
        assert calc_res["ref"] == "whatif:extra_payment"
        chip_intents = {c["intent"] for c in body["chips"]}
        assert {"scenario", "schedule"} <= chip_intents
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


def test_chat_whatif_lump_sum_returns_markdown_and_calc_resource(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P3").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "1,000만원 한번에 갚으면 얼마나 빨리 끝나?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"
        calc_res = next(res for res in body["resources"] if res["kind"] == "calc")
        assert calc_res["ref"] == "whatif:lump_sum"
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


def test_chat_whatif_refinance_has_compare_chip_with_grounded_params(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P7").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "은행 신용대출을 금리 5%로 갈아타면?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"
        calc_res = next(res for res in body["resources"] if res["kind"] == "calc")
        assert calc_res["ref"] == "whatif:refinance"
        compare_chip = next(c for c in body["chips"] if c["intent"] == "compare")
        assert compare_chip["params"]["target_loan_id"] == "P7-L1"
        assert compare_chip["params"]["amount"] == 20_000_000
        assert compare_chip["params"]["term_months"] == 6
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


def test_chat_whatif_retirement_age_returns_markdown(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P6").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "은퇴를 60세로 하면 노후 부족액은?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"
        calc_res = next(res for res in body["resources"] if res["kind"] == "calc")
        assert calc_res["ref"] == "whatif:retirement_age"
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


# ---------------------------------------------------------------------------
# (e) 슬롯 없음 되묻기 / 프로필 없음 안내
# ---------------------------------------------------------------------------


def test_chat_whatif_without_slots_asks_again(monkeypatch):
    """LLM이 whatif로 분류했지만 계산 슬롯이 전혀 없으면 되묻는다."""
    fake = _FakeSuccessProvider({"intent": "whatif"})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "은퇴 계획을 다시 세우고 싶어요"})
        assert r.status_code == 200
        body = r.json()
        assert "매달 얼마를 더 갚을지" in body["reply_text"]
        assert body["route"] == "internal"
        assert body["action"] is None
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


def test_chat_whatif_without_profile_prompts_onboarding(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    r = client.post("/api/chat", json={"message": "매달 30만 원 더 갚으면 얼마나 빨리 끝나?"})
    assert r.status_code == 200
    body = r.json()
    assert "프로필이 없어요" in body["reply_text"]
    assert any(c["intent"] == "onboarding" for c in body["chips"])
    client.delete(f"/api/chats/{body['chat_id']}")


# ---------------------------------------------------------------------------
# (f) LLM이 지어낸 숫자(발화에 없음) 폐기
# ---------------------------------------------------------------------------


def test_chat_whatif_llm_invented_number_is_discarded(monkeypatch):
    fake = _FakeSuccessProvider({"intent": "whatif", "extra_monthly": 99_000_000})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "더 갚으면 얼마나 빨리 끝날지 궁금해요"})
        assert r.status_code == 200
        body = r.json()
        # 발화에 없는 9천9백만원은 폐기되어 슬롯이 비었으므로 되묻는 문장이 나와야 한다.
        assert "매달 얼마를 더 갚을지" in body["reply_text"]
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


def test_chat_whatif_llm_number_present_in_utterance_is_kept(monkeypatch):
    fake = _FakeSuccessProvider({"intent": "whatif", "extra_monthly": 300_000})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "30만 원 더 갚으면 얼마나 빨리 끝나요?"})
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"
        assert "300,000원" in body["reply_text"]
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")


# ---------------------------------------------------------------------------
# (g) LLM 문장 검증 실패 시 템플릿
# ---------------------------------------------------------------------------


class _FakeWhatifDigitLeakProvider:
    """whatif_v1 슬롯 필링에 숫자를 직접 써서 검증 실패를 유도하는 가짜 provider."""

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        assert template_id == "whatif_v1"
        data = {"summary": "6개월 빨리 끝나요."}  # 플레이스홀더 없이 숫자를 직접 씀 -> 검증 실패
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-whatif-model", key_index=0, latency_ms=3, usage={})


def test_explain_chat_whatif_digit_leak_falls_back_to_template():
    profile = synthetic.get_persona("P1")
    params = policy.load_policy_params()
    result = whatif_service.run_whatif(profile, params, {"extra_monthly": 300_000}, today=date.today())
    assert result is not None

    text, llm_used, model, latency_ms, problems = explain_service.explain_chat_whatif(
        result, _FakeWhatifDigitLeakProvider(),
    )
    assert llm_used is False
    assert model is None
    assert any("digit" in p for p in problems)
    assert text == explain_service._template_whatif_conclusion(result)
    assert "원" in text


def test_chat_whatif_llm_digit_leak_falls_back_to_template_in_chat_reply(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeWhatifDigitLeakProvider())
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        r = client.post("/api/chat", json={"message": "매달 30만 원 더 갚으면 얼마나 빨리 끝나?"})
        assert r.status_code == 200
        body = r.json()
        assert body["llm_used"] is False
        assert "300,000원" in body["reply_text"]
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")
