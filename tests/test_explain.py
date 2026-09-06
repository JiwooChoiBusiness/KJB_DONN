"""설명 문장(슬롯 필링, SPEC 2.8) 단위/API 테스트.

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처(신용대출 상품
"가상은행A"(5.2%)/"가상은행B"(6.8%)/"가상저축은행A"(8.5%)/"가상저축은행B"(9.1%))를
그대로 재사용한다(패턴은 tests/test_api.py 헤더 참고).
"""
from __future__ import annotations

import json

import pytest

from app.api import routes as routes_module
from app.llm import slotfill
from app.llm.provider import LLMResult, LLMUnavailable
from tests.test_api import _FIXTURE_COMPANIES, _FakeUnavailableProvider, _clear_session, client

# ---------------------------------------------------------------------------
# (a) slotfill 단위 테스트
# ---------------------------------------------------------------------------


def test_slotfill_validate_catches_digit_outside_placeholder():
    problems = slotfill.validate("이 상품은 금리 5%로 유리해요.", {"rate_a"}, [], max_chars=100, max_sentences=3)
    assert "digit_outside_placeholder" in problems


def test_slotfill_validate_allows_digits_inside_placeholder_only():
    problems = slotfill.validate("금리는 {rate_a}예요.", {"rate_a"}, [], max_chars=100, max_sentences=3)
    assert problems == []


def test_slotfill_validate_catches_unknown_placeholder():
    problems = slotfill.validate("금리는 {rate_zzz}예요.", {"rate_a"}, [], max_chars=100, max_sentences=3)
    assert "unknown_placeholder:rate_zzz" in problems


def test_slotfill_validate_catches_banned_term():
    problems = slotfill.validate("가상은행A를 확인해보세요.", set(), ["가상은행A"], max_chars=100, max_sentences=3)
    assert "banned_term:가상은행A" in problems


def test_slotfill_validate_catches_forbidden_phrase():
    problems = slotfill.validate("이 상품을 추천합니다.", set(), [], max_chars=100, max_sentences=3)
    assert "forbidden_phrase:추천" in problems


def test_slotfill_validate_catches_too_long_and_too_many_sentences():
    text = "괜찮아요. 확인해보세요. 다음에 또 봐요."  # 3문장
    problems = slotfill.validate(text, set(), [], max_chars=10, max_sentences=1)
    assert "too_long" in problems
    assert "too_many_sentences" in problems


def test_slotfill_validate_empty_text():
    assert slotfill.validate("   ", set(), [], max_chars=100, max_sentences=3) == ["empty"]


def test_slotfill_sanitize_removes_em_dash_and_markdown():
    text = "이건 — 중요해요.\n- 목록 항목\n**강조**와 `코드`"
    out = slotfill.sanitize(text)
    assert "—" not in out
    assert "*" not in out
    assert "`" not in out
    assert "목록 항목" in out  # 줄머리 "- "는 제거되지만 내용은 남는다


def test_slotfill_fill_substitutes_and_raises_on_missing_value():
    assert slotfill.fill("금리는 {rate_a}예요.", {"rate_a": "5.2%"}) == "금리는 5.2%예요."
    with pytest.raises(KeyError):
        slotfill.fill("금리는 {rate_a}예요.", {})


def test_slotfill_fill_corrects_josa_after_placeholder_to_match_value():
    """SPEC 2.9 조사 보정: fill()이 플레이스홀더를 채운 뒤, 템플릿이 써둔 조사가 실제
    값의 마지막 글자 받침과 맞지 않으면 고쳐 쓴다."""
    assert slotfill.fill("{total_a}로 정리했어요.", {"total_a": "71,703원"}) == "71,703원으로 정리했어요."
    assert slotfill.fill("{rate_a}은 낮아요.", {"rate_a": "5.47%"}) == "5.47%는 낮아요."
    assert slotfill.fill("{term_months}이 남았어요.", {"term_months": "36개월"}) == "36개월이 남았어요."


def test_slotfill_fill_josa_correction_covers_gwa_ira_ieyo_groups():
    # 과/와: 받침 있는 값("원") 뒤에 "와"가 쓰였으면 "과"로, 받침 없는 값("%") 뒤에
    # "과"가 쓰였으면 "와"로 고친다.
    assert slotfill.fill("{a}와 비교했어요.", {"a": "1,000원"}) == "1,000원과 비교했어요."
    assert slotfill.fill("{b}과 비교했어요.", {"b": "2%"}) == "2%와 비교했어요."
    # 이라/라: "개"(받침 없음) -> "라", "원"(받침 있음) -> "이라"(이미 맞는 형태 유지)
    assert slotfill.fill("{n}이라 좋아요.", {"n": "3개"}) == "3개라 좋아요."
    assert slotfill.fill("{n}이라 좋아요.", {"n": "1,000원"}) == "1,000원이라 좋아요."
    # 이에요/예요: 이미 맞는 형태면 그대로 유지되고, 안 맞으면 고친다.
    assert slotfill.fill("총이자는 {total_a}이에요.", {"total_a": "500원"}) == "총이자는 500원이에요."
    assert slotfill.fill("총이자는 {total_a}이에요.", {"total_a": "3%"}) == "총이자는 3%예요."


def test_slotfill_josa_picks_by_batchim():
    assert slotfill.josa("사과", "은/는") == "는"   # 받침 없음
    assert slotfill.josa("가방", "은/는") == "은"   # 받침 있음
    assert slotfill.josa("PC", "은/는") == "는"      # 한글 아님 -> 받침 없는 쪽
    assert slotfill.josa("사과", "이/가") == "가"
    assert slotfill.josa("가방", "이/가") == "이"
    assert slotfill.josa("사과", "을/를") == "를"
    assert slotfill.josa("가방", "을/를") == "을"


def test_slotfill_assert_no_digits_recurses_dict_list_str():
    slotfill.assert_no_digits({"a": ["텍스트", {"b": "또 다른 텍스트"}]})  # 통과(예외 없음)
    with pytest.raises(ValueError):
        slotfill.assert_no_digits({"a": "숫자 1 포함"})
    with pytest.raises(ValueError):
        slotfill.assert_no_digits(["ok", "5% 포함"])
    with pytest.raises(ValueError):
        slotfill.assert_no_digits({"금액": "괜찮음"} | {"키에숫자1": "값"})


# ---------------------------------------------------------------------------
# 가짜 provider (explain 전용)
# ---------------------------------------------------------------------------


class _FakeExplainProvider:
    """slots를 캡처하고, 실제로 받은 placeholders 이름만 참조하는 유효한 JSON을 돌려준다."""

    def __init__(self):
        self.calls = 0
        self.last_slots = None

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        self.calls += 1
        self.last_slots = slots
        placeholders = slots.get("placeholders", {})
        if template_id == "action_card_v1":
            data = {"summary": "이번 안내를 확인했어요. 지금 상황에 맞게 살펴보세요."}
        elif "label_a" not in placeholders:
            data = {"summary": "지금은 참고할 상품이 없어요.", "reasons": []}
        else:
            summary = "{label_a} 조건을 확인했어요. 금리는 {rate_a}이고 총이자는 {total_a}예요."
            reasons = []
            for letter in ("a", "b", "c"):
                if f"label_{letter}" in placeholders:
                    reasons.append(
                        f"{{label_{letter}}} 조건은 금리 {{rate_{letter}}}, "
                        f"총이자 {{total_{letter}}}이에요."
                    )
            data = {"summary": summary, "reasons": reasons}
        return LLMResult(
            data=data, text=json.dumps(data, ensure_ascii=False),
            model="fake-explain-model", key_index=0, latency_ms=7, usage={},
        )


class _FakeDigitLeakProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        data = {"summary": "이 상품은 금리 5%로 유리해요.", "reasons": []}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake", key_index=0, latency_ms=1, usage={})


class _FakeUnknownPlaceholderProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        data = {"summary": "{rate_zzz} 정도예요.", "reasons": []}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake", key_index=0, latency_ms=1, usage={})


class _FakeRecommendProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        data = {"summary": "이 상품을 추천합니다.", "reasons": []}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake", key_index=0, latency_ms=1, usage={})


class _FakeCompanyNameProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        data = {"summary": "가상은행A가 좋아요.", "reasons": []}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake", key_index=0, latency_ms=1, usage={})


class _FakeRaisingProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None):  # noqa: ANN001
        raise LLMUnavailable("강제 실패(테스트)")


# ---------------------------------------------------------------------------
# (b)(c)(e) 공시 비교 설명: 성공 -> 캐시 -> 재현(replay)
# ---------------------------------------------------------------------------


def _fresh_compare_decision() -> tuple[str, dict]:
    """P1 페르소나로 신용대출 비교를 실행해 (decision_id, result)를 돌려준다.

    픽스처 상품의 옵션에는 신용점수 구간별 티어가 없으므로(base 금리만 있음)
    credit_band를 None으로 되돌려야 `ranking.eligible`이 픽스처 4종을 모두 통과시킨다
    (P1은 credit_band가 있어 prepare가 이를 그대로 추정해 넣는다).
    """
    r = client.post("/api/session/persona/P1")
    assert r.status_code == 200

    ctx = client.post(
        "/api/compare/prepare", json={"intent": "compare", "params": {"category": "credit"}}
    ).json()
    ctx["user_confirmed"] = True
    ctx["lender_groups"] = ["bank", "savings_bank"]
    ctx["credit_band"] = None

    r2 = client.post("/api/compare/run", json=ctx)
    assert r2.status_code == 200, r2.text
    result = r2.json()
    assert len(result["items"]) >= 1
    return result["decision_id"], result


def test_compare_explain_llm_success_then_cached_then_replay_still_matches(monkeypatch):
    decision_id, _ = _fresh_compare_decision()

    # GET은 아직 저장된 설명이 없으므로 404
    r0 = client.get(f"/api/compare/{decision_id}/explain")
    assert r0.status_code == 404

    fake = _FakeExplainProvider()
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r1 = client.post(f"/api/compare/{decision_id}/explain")
    assert r1.status_code == 200
    body = r1.json()
    assert body["kind"] == "compare"
    assert body["source"] == "llm"
    assert body["llm_used"] is True
    assert body["cached"] is False
    assert body["model"] == "fake-explain-model"
    assert body["problems"] == []
    assert "{" not in body["summary"]  # 플레이스홀더가 남지 않음
    assert "원" in body["summary"]
    assert "%" in body["summary"]
    assert set(body["item_reasons"].keys()) == {"1", "2", "3"}
    for reason in body["item_reasons"].values():
        assert "{" not in reason

    # D4: LLM으로 보낸 slots(facts+placeholders)에 숫자와 픽스처 회사명/상품명이 없어야 한다
    assert fake.last_slots is not None
    slots_text = json.dumps(fake.last_slots, ensure_ascii=False)
    assert not any(ch.isdigit() for ch in slots_text)
    for company in _FIXTURE_COMPANIES:
        assert company not in slots_text
        assert f"{company} 신용대출" not in slots_text

    # GET은 이제 저장된 설명을 돌려준다
    r_get = client.get(f"/api/compare/{decision_id}/explain")
    assert r_get.status_code == 200
    assert r_get.json()["summary"] == body["summary"]

    # 같은 decision_id로 다시 POST하면 캐시를 그대로 돌려주고 LLM을 다시 부르지 않는다
    r2 = client.post(f"/api/compare/{decision_id}/explain")
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["cached"] is True
    assert body2["summary"] == body["summary"]
    assert fake.calls == 1

    # 설명 생성 뒤에도 result_hash는 그대로 재현된다(설명은 별도 테이블, 결정 기록 불변)
    r_replay = client.post(f"/api/decisions/{decision_id}/replay")
    assert r_replay.status_code == 200
    assert r_replay.json()["match"] is True

    _clear_session()


def test_compare_explain_unknown_decision_404():
    r = client.post("/api/compare/UNKNOWN-DECISION/explain")
    assert r.status_code == 404
    r2 = client.get("/api/compare/UNKNOWN-DECISION/explain")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# (d) 검증 실패/LLM 불가 -> 템플릿 폴백 (부분 혼합 없음)
# ---------------------------------------------------------------------------


def test_compare_explain_falls_back_to_template_on_any_validation_failure(monkeypatch):
    decision_id, _ = _fresh_compare_decision()

    cases = [
        (_FakeDigitLeakProvider(), "summary:digit_outside_placeholder"),
        (_FakeUnknownPlaceholderProvider(), "summary:unknown_placeholder:rate_zzz"),
        (_FakeRecommendProvider(), "summary:forbidden_phrase:추천"),
        (_FakeCompanyNameProvider(), "summary:banned_term:가상은행A"),
        (_FakeRaisingProvider(), "llm_unavailable"),
        (_FakeUnavailableProvider(), "llm_unavailable"),  # explain 메서드 자체가 없음
    ]
    for provider, expected_problem in cases:
        monkeypatch.setattr(routes_module, "_llm_provider", provider)
        r = client.post(f"/api/compare/{decision_id}/explain", json={"refresh": True})
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "template", (provider.__class__.__name__, body)
        assert body["llm_used"] is False
        assert body["model"] is None
        assert expected_problem in body["problems"], (provider.__class__.__name__, body["problems"])
        assert "추천" not in body["summary"]
        for company in _FIXTURE_COMPANIES:
            assert company not in body["summary"]

    _clear_session()


# ---------------------------------------------------------------------------
# (f) 행동 카드 설명
# ---------------------------------------------------------------------------


def test_action_explain_success_unknown_action_and_after_session_clear(monkeypatch):
    r = client.post("/api/session/persona/P1")
    assert r.status_code == 200

    actions = client.get("/api/actions").json()
    assert actions
    action_id = actions[0]["id"]

    fake = _FakeExplainProvider()
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r1 = client.post(f"/api/actions/{action_id}/explain")
    assert r1.status_code == 200
    body = r1.json()
    assert body["kind"] == "action"
    assert body["summary"]
    assert "추천" not in body["summary"]
    assert "{" not in body["summary"]

    r2 = client.post("/api/actions/UNKNOWN-ACTION-ID/explain")
    assert r2.status_code == 404

    _clear_session()

    r3 = client.post(f"/api/actions/{action_id}/explain")
    assert r3.status_code == 404


def test_fill_drops_duplicated_unit_after_placeholder():
    """LLM이 값에 이미 붙은 단위를 한 번 더 쓰면("{n}개" + "75개") 단위를 중복시키지 않는다."""
    from app.llm import slotfill

    assert slotfill.fill("상품 {n}개 중 {k}개를 골랐어요", {"n": "75개", "k": "3개"}) == "상품 75개 중 3개를 골랐어요"
    assert slotfill.fill("금액 {a}원과 기간 {t}개월", {"a": "20,000,000원", "t": "36개월"}) == "금액 20,000,000원과 기간 36개월"
    assert slotfill.fill("금리 {r}%로", {"r": "5.47%"}) == "금리 5.47%로"
    # 단위가 아닌 글자는 건드리지 않는다
    assert slotfill.fill("{n} 개월치", {"n": "3개"}) == "3개 개월치"

