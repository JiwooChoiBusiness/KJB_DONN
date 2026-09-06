"""2026-09-06 리뷰 지적 사항 회귀 테스트 모음 (builder 작업).

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처를 재사용한다(패턴은
tests/test_answer_routes.py, tests/test_explain.py와 같다). 파일명이 알파벳순으로
"test_api"보다 뒤라 별도의 임포트 순서 트릭은 필요 없다(tests/test_answer_routes.py
헤더 주석 참고 - 그 파일은 이름이 test_api보다 앞서서 트릭이 필요했다).

번호는 리뷰 항목 번호(1~13)를 그대로 따른다.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from tests.test_api import _FakeUnavailableProvider, _clear_session, client
from tests.test_chat_stream import _parse_sse_events

from app.api import routes as routes_module
from app.data import db as db_module
from app.data import policy, synthetic
from app.data import products as products_module
from app.llm.gemini import GeminiProvider
from app.llm.provider import LLMResult, LLMUnavailable
from app.models import (
    Capacity,
    CapacityBand,
    ExplainResult,
    LenderGroup,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    ProductSource,
    RateSemantics,
)
from app.services import actions as actions_service
from app.services import answer as answer_service
from app.services import compare as compare_service
from app.services import explain as explain_service
from app.services import insights as insights_service


def _clear_action_explanations_cache() -> None:
    """`explanations` 테이블(kind="action")을 비운다(다른 테스트 파일이 같은 P1 최우선
    행동 카드로 fresh한 explain 호출을 기대할 수 있어, 이 파일이 만든 캐시를 남기지
    않는다. tests/test_answer_routes.py의 같은 이름 헬퍼와 동일한 목적)."""
    conn = db_module.get_conn()
    try:
        conn.execute("DELETE FROM explanations WHERE kind = 'action'")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 1) 금칙어 캐시(SEV5): 빈 결과는 캐시하지 않고, 상품이 들어오면 다음 호출이 반영한다.
# ---------------------------------------------------------------------------


def _make_credit_snapshot(snapshot_id: str, company: str, rate: float) -> ProductSnapshot:
    return ProductSnapshot(
        id=f"{snapshot_id}:CREDIT:1",
        snapshot_id=snapshot_id,
        source=ProductSource.CURATED,
        category=ProductCategory.CREDIT,
        lender_group=LenderGroup.BANK,
        company_code="C1",
        company_name=company,
        product_code="CR1",
        product_name=f"{company} 신용대출 1호",
        rate_semantics=RateSemantics.OFFER_RATE,
        options=[ProductOption(rate=rate, rate_kind="base", term_months=36, rate_type="fixed")],
        disclosure_month="202608",
        disclosure_url="https://finlife.fss.or.kr/",
    )


def test_get_banned_terms_does_not_cache_empty_result_then_picks_up_new_product(tmp_path):
    """SEV5 #1: products 테이블이 빈 상태에서 첫 호출은 빈 목록을 캐시하지 않아야
    하고, 이후 상품을 넣으면 다음 호출이 회사명을 포함해야 한다."""
    temp_db = str(tmp_path / "banned_terms_test.db")
    prev_db_path = db_module.DB_PATH
    prev_cache = insights_service._banned_cache
    db_module.DB_PATH = temp_db
    insights_service._banned_cache = None
    try:
        db_module.init_db(db_module.get_conn())

        first = insights_service.get_banned_terms()
        assert first == []
        assert insights_service._banned_cache is None  # 빈 결과는 캐시하지 않는다

        conn = db_module.get_conn()
        try:
            products_module._save_snapshot(
                conn, "20260906-0000-unittest", "curated", "2026-09-06T00:00:00",
                [_make_credit_snapshot("20260906-0000-unittest", "가상특판은행Z", 5.0)],
            )
        finally:
            conn.close()

        second = insights_service.get_banned_terms()
        assert any("가상특판은행Z" in name for name in second)
    finally:
        db_module.DB_PATH = prev_db_path
        insights_service._banned_cache = prev_cache


def test_import_seed_invalidates_banned_terms_cache(tmp_path):
    """SEV5 #1: `import_seed`가 성공적으로 끝나면 금칙어 캐시가 무효화되어야 한다."""
    import gzip

    temp_db = str(tmp_path / "import_seed_test.db")
    prev_db_path = db_module.DB_PATH
    prev_cache = insights_service._banned_cache
    db_module.DB_PATH = temp_db
    try:
        db_module.init_db(db_module.get_conn())
        insights_service._banned_cache = ["이미 캐시된 값"]  # 무효화되어야 할 오래된 캐시

        payload = {
            "snapshots": [{"snapshot_id": "seed-1", "source": "finlife", "fetched_at": "2026-09-06T00:00:00",
                           "count": 1, "note": ""}],
            "products": [{
                "id": "seed-1:CREDIT:1", "snapshot_id": "seed-1", "source": "finlife", "category": "credit",
                "lender_group": "bank", "company_code": "C1", "company_name": "가상시드은행",
                "product_code": "CR1", "product_name": "가상시드은행 신용대출 1호",
                "rate_semantics": "offer_rate", "disclosure_month": "202608", "disclosure_url": "https://x",
                "json": _make_credit_snapshot("seed-1", "가상시드은행", 5.0).model_dump_json(),
            }],
        }
        seed_path = tmp_path / "seed.json.gz"
        with gzip.open(seed_path, "wt", encoding="utf-8") as f:
            json.dump(payload, f)

        products_module.import_seed(str(seed_path))
        assert insights_service._banned_cache is None
        assert any("가상시드은행" in n for n in insights_service.get_banned_terms())
    finally:
        db_module.DB_PATH = prev_db_path
        insights_service._banned_cache = prev_cache


# ---------------------------------------------------------------------------
# 2) 평범한 발화가 500(SEV5): term_months 클램프, amount 양수 검증, compare 분기 예외 처리
# ---------------------------------------------------------------------------


def test_prepare_context_clamps_huge_term_months_and_rejects_nonpositive_amount():
    ctx = compare_service.prepare_context(None, {"category": "credit", "term_months": 9999, "amount": 0})
    assert ctx.term_months == 600
    assert ctx.amount > 0
    assert "amount" in ctx.estimated_fields

    ctx2 = compare_service.prepare_context(None, {"category": "credit", "term_months": 700, "amount": -500})
    assert ctx2.term_months == 600
    assert ctx2.amount > 0
    assert "amount" in ctx2.estimated_fields


def test_clean_compare_params_drops_zero_negative_and_non_numeric():
    cleaned = routes_module._clean_compare_params({
        "category": "credit", "amount": 0, "term_months": -5, "max_rate": "abc", "sort_key": "rate",
    })
    assert "amount" not in cleaned
    assert "term_months" not in cleaned
    assert "max_rate" not in cleaned
    assert cleaned["category"] == "credit"
    assert cleaned["sort_key"] == "rate"


_ORDINARY_COMPARE_MESSAGES = ["9999개월 신용대출 비교해줘", "700개월로 비교해줘", "0원 신용대출 비교해줘"]


@pytest.mark.parametrize("message", _ORDINARY_COMPARE_MESSAGES)
def test_ordinary_compare_messages_do_not_500_via_chat(monkeypatch, message):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200, r.text
    client.delete(f"/api/chats/{r.json()['chat_id']}")


@pytest.mark.parametrize("message", _ORDINARY_COMPARE_MESSAGES)
def test_ordinary_compare_messages_do_not_500_via_chat_stream(monkeypatch, message):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    r = client.post("/api/chat/stream", json={"message": message})
    assert r.status_code == 200, r.text
    events = _parse_sse_events(r.text)
    assert events[-1][0] == "reply", events
    client.delete(f"/api/chats/{events[-1][1]['chat_id']}")


def test_compare_intent_falls_back_to_guidance_when_prepare_context_raises(monkeypatch):
    """방어적 경로: prepare_context/CompareContext.model_validate 둘 다 실패해도 500이
    아니라 안내 문장으로 답해야 한다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    def _raise_value_error(profile, params):  # noqa: ANN001
        raise ValueError("강제 실패(테스트)")

    monkeypatch.setattr(compare_service, "prepare_context", _raise_value_error)
    _clear_session()
    r = client.post("/api/chat", json={"message": "신용대출 비교해줘"})
    assert r.status_code == 200
    body = r.json()
    assert "조건을 이해하지 못했어요" in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")


# ---------------------------------------------------------------------------
# 3) 개인 수치가 외부 검색으로(SEV4): 검색 질의에서 숫자 토큰 제거
# ---------------------------------------------------------------------------


class _CapturingSearchProvider:
    def __init__(self):
        self.received_question: Optional[str] = None
        self.external_search_enabled = True

    def available(self) -> bool:
        return True

    def search_answer(self, question, system, *, deadline_seconds=None):  # noqa: ANN001
        self.received_question = question
        data = {"sources": [{"uri": "https://www.bok.or.kr/x", "title": "한국은행"}],
                "queries": [], "search_entry_point_html": ""}
        return LLMResult(data=data, text="기준금리는 금융통화위원회가 결정합니다.",
                          model="fake-model", key_index=0, latency_ms=5, usage={})


def test_external_answer_strips_digit_tokens_from_search_query():
    provider = _CapturingSearchProvider()
    text, resources, llm_used, model, problems, _ = answer_service.external_answer(
        "연봉 4800만원에 빚 3천만원인데 요즘 금리 어때", provider, [],
    )
    if provider.received_question is not None:
        assert not any(ch.isdigit() for ch in provider.received_question)
    else:
        # 숫자 제거 후 질의가 너무 짧거나 여전히 숫자가 남아 그라운딩 자체를 건너뛴
        # 경우(공식 링크 폴백)도 허용한다.
        assert llm_used is False


def test_strip_numeric_tokens_removes_amount_and_unit():
    stripped = answer_service._strip_numeric_tokens("3천만원 대출 이자가 5% 넘는데 괜찮을까요")
    assert not any(ch.isdigit() for ch in stripped)


# ---------------------------------------------------------------------------
# 4) external 요약 회사명 화이트리스트(SEV4)
# ---------------------------------------------------------------------------


class _FakeSearchProviderText:
    def __init__(self, text: str):
        self._text = text
        self.external_search_enabled = True

    def available(self) -> bool:
        return True

    def search_answer(self, question, system, *, deadline_seconds=None):  # noqa: ANN001
        data = {"sources": [{"uri": "https://www.fss.or.kr/x", "title": "금감원"}],
                "queries": [], "search_entry_point_html": ""}
        return LLMResult(data=data, text=self._text, model="fake-model", key_index=0, latency_ms=5, usage={})


def test_external_answer_drops_summary_for_ungrounded_company_name_pattern():
    provider = _FakeSearchProviderText("가상특판은행Z에서 확인하시면 도움이 될 거예요.")
    text, resources, llm_used, model, problems, _ = answer_service.external_answer(
        "예금자보호 한도가 얼마야", provider, [],
    )
    assert "가상특판은행Z" not in text
    assert "company_pattern" in problems
    assert resources  # 링크는 유지된다


def test_external_answer_keeps_generic_sector_word_and_flags_numbers_in_summary():
    provider = _FakeSearchProviderText("시중은행 정기예금 금리가 3%대에 형성되어 있어요.")
    text, resources, llm_used, model, problems, _ = answer_service.external_answer(
        "예금 금리 동향 알려줘", provider, [],
    )
    assert "company_pattern" not in problems
    assert "(수치는 확인 필요)" in text


# ---------------------------------------------------------------------------
# 5) resp.json() 예외 누수(SEV4)
# ---------------------------------------------------------------------------


def _ok_payload(text: str = '{"intent": "compare"}') -> dict:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"totalTokenCount": 12},
        "modelVersion": "gemini-test-version",
    }


def test_gemini_run_chain_treats_200_bad_json_as_retryable_and_tries_next_key():
    provider = GeminiProvider(
        keys=["k1", "k2"], model_chain=["model-a"],
        cfg={"retry_on_status": [429, 500, 503], "skip_model_on_status": [404],
             "fail_fast_on_status": [400, 401, 403], "key_cooldown_seconds": 60,
             "temperature": 0, "timeout_seconds": 5, "endpoint": "https://example.invalid/v1beta"},
    )
    bad_resp = MagicMock()
    bad_resp.status_code = 200
    bad_resp.json.side_effect = ValueError("not json")
    good_resp = MagicMock()
    good_resp.status_code = 200
    good_resp.json.return_value = _ok_payload()

    with patch("app.llm.gemini.requests.post") as mock_post:
        mock_post.side_effect = [bad_resp, good_resp]
        result = provider.extract("문의", {"type": "OBJECT"}, "system")
    assert mock_post.call_count == 2
    assert result.data == {"intent": "compare"}
    assert result.key_index == 1


class _FakeRuntimeErrorExtractProvider:
    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        raise RuntimeError("강제 실패(테스트)")


def test_chat_extract_generic_exception_falls_back_to_rule_parser(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeRuntimeErrorExtractProvider())
    _clear_session()
    r = client.post("/api/chat", json={"message": "신용대출 공시 비교하고 싶어요"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is False
    client.delete(f"/api/chats/{body['chat_id']}")


class _FakeRuntimeErrorExplainProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        raise RuntimeError("강제 실패(테스트)")


def test_call_llm_explain_generic_exception_falls_back_to_template():
    data, model, latency_ms, problems = explain_service._call_llm_explain(
        _FakeRuntimeErrorExplainProvider(), {"a": "b"}, {"x": "y"}, "t1", {"type": "OBJECT"},
    )
    assert data is None
    assert "llm_error" in problems


# ---------------------------------------------------------------------------
# 6) explain 캐시 키(SEV3)
# ---------------------------------------------------------------------------


def test_explain_action_ref_id_changes_when_capacity_band_changes_with_same_numbers(monkeypatch):
    profile = synthetic.get_persona("P1")
    params = policy.load_policy_params()
    today = date.today()
    cards = actions_service._raw_actions(profile, params, today=today)
    assert cards
    card = cards[0]

    def _fake_capacity_factory(band: CapacityBand):
        def _inner(_profile, _schedules):
            return Capacity(
                band=band, net_monthly=123_000, debt_service=200_000,
                debt_service_ratio=0.3, explanation="테스트용 설명", assumptions=[],
            )
        return _inner

    monkeypatch.setattr(explain_service, "compute_capacity", _fake_capacity_factory(CapacityBand.OK))
    result_ok = explain_service.explain_action(
        card.id, _FakeUnavailableProvider(), profile, params, today=today,
    )
    monkeypatch.setattr(explain_service, "compute_capacity", _fake_capacity_factory(CapacityBand.TIGHT))
    result_tight = explain_service.explain_action(
        card.id, _FakeUnavailableProvider(), profile, params, today=today,
    )

    assert result_ok is not None and result_tight is not None
    assert result_ok.ref_id != result_tight.ref_id


class _CountingCompareExplainProvider:
    def __init__(self):
        self.calls = 0

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        self.calls += 1
        placeholders = slots.get("placeholders", {})
        if "label_a" not in placeholders:
            data = {"summary": "지금은 참고할 상품이 없어요.", "reasons": []}
        else:
            summary = "{label_a} 조건이 금리 {rate_a}이라서 총이자 {total_a}로 유리해요. 조건을 확인해 보세요."
            reasons = []
            for letter in ("a", "b", "c"):
                if f"label_{letter}" in placeholders:
                    reasons.append(
                        f"{{label_{letter}}} 조건은 금리 {{rate_{letter}}}, 총이자 {{total_{letter}}}이에요."
                    )
            data = {"summary": summary, "reasons": reasons}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-compare-explain", key_index=0, latency_ms=3, usage={})


def test_explain_compare_regenerates_after_profile_switch_but_caches_within_same_profile(monkeypatch):
    """`decisions.get`은 결정을 만든 세션(sid)에서만 보인다(app/services/decisions.py).
    `explain_compare`를 직접 호출하면(HTTP 요청 밖) sid가 "local"로 평가되어 방금
    TestClient로 만든 결정을 찾지 못하므로, 이 테스트는 HTTP 엔드포인트로 호출해
    tests/test_explain.py의 `_fresh_compare_decision` 패턴과 같은 방식으로 sid를
    맞춘다."""
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        ctx = client.post(
            "/api/compare/prepare", json={"intent": "compare", "params": {"category": "credit"}},
        ).json()
        ctx["user_confirmed"] = True
        ctx["credit_band"] = None
        ctx["lender_groups"] = ["bank", "savings_bank"]
        r_run = client.post("/api/compare/run", json=ctx)
        assert r_run.status_code == 200, r_run.text
        decision_id = r_run.json()["decision_id"]

        fake = _CountingCompareExplainProvider()
        monkeypatch.setattr(routes_module, "_llm_provider", fake)

        r1 = client.post(f"/api/compare/{decision_id}/explain")
        assert r1.status_code == 200, r1.text
        assert r1.json()["cached"] is False
        assert fake.calls == 1

        r2 = client.post(f"/api/compare/{decision_id}/explain")  # 같은 프로필: 캐시 재사용
        assert r2.status_code == 200
        assert r2.json()["cached"] is True
        assert fake.calls == 1

        assert client.post("/api/session/persona/P2").status_code == 200
        r3 = client.post(f"/api/compare/{decision_id}/explain")  # 프로필이 바뀌었으니 재생성
        assert r3.status_code == 200
        assert r3.json()["cached"] is False
        assert fake.calls == 2
    finally:
        client.delete("/api/session")


# ---------------------------------------------------------------------------
# 7) 권유 표현 최종 검사(SEV3)
# ---------------------------------------------------------------------------


def test_final_check_replaces_recommend_word_leaking_through_action_explain(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        monkeypatch.setattr(
            explain_service, "explain_action",
            lambda *a, **k: ExplainResult(
                kind="action", ref_id="test:forced", summary="이 상품을 추천합니다.",
                item_reasons={}, source="llm", llm_used=True, model="fake", latency_ms=1,
                template_id="action_card_v1", prompt_version="test", problems=[], cached=False,
                created_at=datetime.now(),
            ),
        )
        r = client.post("/api/chat", json={"message": "이번 달에 나 뭐부터 갚아야 해?"})
        assert r.status_code == 200
        body = r.json()
        assert "추천" not in body["reply_text"]
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")
        _clear_action_explanations_cache()


def test_rule_based_kb_answer_skips_sentence_with_forbidden_phrase_substring():
    """SEV3 #7: kb/consumer-rights.md "대상과 요건" 섹션의 첫 문장은 "보장성 상품"이라는
    표현 때문에 "보장"이 부분 문자열로 걸린다. 그 문장을 건너뛰고 다음 문장을 써야
    check 단계(guardrails 외 slotfill.FORBIDDEN_PHRASES)를 통과할 수 있다."""
    from app.llm import slotfill

    doc = next(d for d in answer_service.kb_search.load_docs() if d.slug == "consumer-rights")
    sections = answer_service.kb_reference_sections(doc)
    summary, points = answer_service._rule_based_kb_answer(doc, sections)
    full_text = summary + " " + " ".join(points)
    assert not any(phrase in full_text for phrase in slotfill.FORBIDDEN_PHRASES)


# ---------------------------------------------------------------------------
# 8) external 빈 응답 trace(SEV3)
# ---------------------------------------------------------------------------


class _FakeSearchProviderEmpty:
    def __init__(self):
        self.external_search_enabled = True

    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        raise LLMUnavailable("테스트: 규칙 파서 사용")

    def search_answer(self, question, system, *, deadline_seconds=None):  # noqa: ANN001
        data = {"sources": [{"uri": "https://www.fss.or.kr/x", "title": "금감원"}],
                "queries": [], "search_entry_point_html": ""}
        return LLMResult(data=data, text="", model="fake-model", key_index=0, latency_ms=5, usage={})


def test_external_empty_summary_is_not_marked_llm_used_and_trace_shows_fallback(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeSearchProviderEmpty())
    _clear_session()
    r = client.post("/api/chat", json={"message": "요즘 뉴스에 나오는 경제 소식 알려줘"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "external"
    assert body["llm_used"] is False
    external_stage = next(s for s in body["trace"] if s["id"] == "external")
    assert external_stage["status"] == "fallback"
    client.delete(f"/api/chats/{body['chat_id']}")


# ---------------------------------------------------------------------------
# 9) KB 숫자 근거 검사 강화(SEV3)
# ---------------------------------------------------------------------------


class _FakeDoc:
    def __init__(self, title, sections, needs_verification=False):
        self.title = title
        self.sections = sections
        self.needs_verification = needs_verification


class _FakeKbExplainProviderText:
    def __init__(self, summary, points):
        self._summary = summary
        self._points = points

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        data = {"summary": self._summary, "points": self._points}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-kb-model", key_index=0, latency_ms=4, usage={})


def _kb_test_doc() -> _FakeDoc:
    return _FakeDoc(
        title="테스트 제도",
        sections={
            "개요": "한도는 2000만원입니다.",
            "대상과 요건": "누구나 신청할 수 있습니다.",
            "절차": "온라인으로 신청합니다.",
        },
    )


def test_number_tokens_normalizes_commas_across_representations():
    ref = answer_service._number_tokens("한도는 2000만원까지입니다.")
    out = answer_service._number_tokens("2,000만원 정도예요.")
    assert out & ref


def test_format_kb_answer_treats_comma_formatted_number_as_grounded():
    doc = _kb_test_doc()
    sections = answer_service.kb_reference_sections(doc)
    fake = _FakeKbExplainProviderText(
        summary="한도는 2,000만원이에요.",
        points=[f"**{name}**: 확인해보세요." for name in sections],
    )
    # SPEC 2.16: 이 테스트는 숫자 그라운딩(콤마 표기 정규화) 자체를 보는 단위 테스트라
    # 기존(2.8) 길이 계약인 detail="brief"(요약 1문장)로 호출한다.
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(
        doc, None, fake, [], detail="brief",
    )
    assert "ungrounded_number" not in problems
    assert llm_used is True


def test_format_kb_answer_rejects_hangul_numeral_bypass():
    doc = _kb_test_doc()
    sections = answer_service.kb_reference_sections(doc)
    fake = _FakeKbExplainProviderText(
        summary="한도는 이천만원이에요.",
        points=[f"**{name}**: 확인해보세요." for name in sections],
    )
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(doc, None, fake, [])
    assert llm_used is False
    assert "hangul_numeral" in problems


# ---------------------------------------------------------------------------
# 10) 오류 메시지 노출(SEV3)
# ---------------------------------------------------------------------------

_GENERIC_ERROR_TEXT = "응답을 만들지 못했어요. 잠시 후 다시 시도해 주세요."


def test_chat_stream_error_payload_hides_internal_details(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    def _boom(message, base_params=None, emit=None, cancel_event=None, detail="full"):  # noqa: ANN001
        raise RuntimeError(
            "pydantic.ValidationError: 3 validation errors for CompareContext\n"
            "Traceback (most recent call last):\n"
            '  File "app/services/compare.py", line 1, in prepare_context'
        )

    monkeypatch.setattr(routes_module, "_build_chat_reply", _boom)
    r = client.post("/api/chat/stream", json={"message": "상환표 보여줘"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    assert events[-1][0] == "error"
    error_message = events[-1][1]["message"]
    for leaked in ("pydantic", "Traceback", "app/services/compare.py", "app.services"):
        assert leaked not in error_message
    assert error_message == _GENERIC_ERROR_TEXT


def test_post_chat_500_path_hides_internal_details(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    def _boom(body, emit=None, cancel_event=None):  # noqa: ANN001
        raise RuntimeError("Traceback (most recent call last): pydantic.ValidationError in app.services.compare")

    monkeypatch.setattr(routes_module, "run_chat", _boom)
    r = client.post("/api/chat", json={"message": "상환표 보여줘"})
    assert r.status_code == 500
    detail = r.json().get("detail", "")
    for leaked in ("pydantic", "Traceback", "app.services"):
        assert leaked not in detail
    assert detail == _GENERIC_ERROR_TEXT


# ---------------------------------------------------------------------------
# 11) SSE 스레드 정리(SEV3)
# ---------------------------------------------------------------------------


def test_stage_emitter_stops_after_cancel_event_set():
    cancel_event = threading.Event()
    received: list[dict[str, Any]] = []
    emitter = routes_module._StageEmitter(lambda e: received.append(dict(e)), cancel_event)
    emitter.start("guard")
    emitter.finish("guard", "done", "완료")
    cancel_event.set()
    emitter.start("intent")
    emitter.finish("intent", "done", "완료")
    assert [e["id"] for e in received] == ["guard", "guard"]
    assert [e["id"] for e in emitter.trace] == ["guard"]


def test_chat_stream_closing_early_lets_worker_thread_finish_soon(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    baseline = threading.active_count()

    with client.stream("POST", "/api/chat/stream", json={"message": "상환표 보여줘"}) as response:
        assert response.status_code == 200
        lines = response.iter_lines()
        first_line = next(lines)
        assert first_line

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and threading.active_count() > baseline:
        time.sleep(0.05)
    assert threading.active_count() <= baseline


# ---------------------------------------------------------------------------
# 12) SEV2 묶음
# ---------------------------------------------------------------------------


def test_chat_message_exceeds_max_length_returns_422():
    r = client.post("/api/chat", json={"message": "가" * 2001})
    assert r.status_code == 422


def test_merge_followup_wraps_string_exclude_companies_into_list():
    base = {"category": "credit", "amount": 10_000_000, "term_months": 36, "exclude_companies": ["기존은행"]}
    merged, changed = routes_module._merge_followup(base, {"exclude_companies": "새은행"})
    assert isinstance(merged["exclude_companies"], list)
    assert set(merged["exclude_companies"]) == {"기존은행", "새은행"}


def test_official_domain_check_rejects_hostname_spoofing():
    assert answer_service._is_official_domain("https://www.fss.or.kr/path") is True
    assert answer_service._is_official_domain("https://sub.fss.or.kr/x") is True
    assert answer_service._is_official_domain("https://fss.or.kr.evil.example/") is False
    assert answer_service._is_official_domain("https://evil.example/fss.or.kr") is False


def test_various_intents_use_new_stage_node_ids_not_generic_compute(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    cases = [
        ("상환표 보여줘", "debt_data"),
        ("소비 패턴 보여줘", "debt_data"),
        ("노후 자금 얼마나 필요할까", "calc"),
    ]
    for message, expected_id in cases:
        body = client.post("/api/chat", json={"message": message}).json()
        ids = [s["id"] for s in body["trace"]]
        assert expected_id in ids, (message, ids)
        assert "compute" not in ids, (message, ids)
        client.delete(f"/api/chats/{body['chat_id']}")

    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        body = client.post("/api/chat", json={"message": "이번 달에 뭐부터 갚아야 해?"}).json()
        ids = [s["id"] for s in body["trace"]]
        assert "calc" in ids
        assert "compute" not in ids
        client.delete(f"/api/chats/{body['chat_id']}")

        body2 = client.post(
            "/api/chat", json={"message": "신용대출 500만원 12개월로 공시 비교해줘"},
        ).json()
        ids2 = [s["id"] for s in body2["trace"]]
        assert "products" in ids2
        assert "compute" not in ids2
        client.delete(f"/api/chats/{body2['chat_id']}")
    finally:
        client.delete("/api/session")
        _clear_action_explanations_cache()


# ---------------------------------------------------------------------------
# 13) 테스트 공백: _ground_numeric_slots
# ---------------------------------------------------------------------------


def test_ground_numeric_slots_discards_numbers_not_in_utterance():
    slots = {"intent": "compare", "amount": 99_000_000, "term_months": 36}
    grounded = routes_module._ground_numeric_slots(dict(slots), "신용대출 비교해줘")
    assert "amount" not in grounded
    assert "term_months" not in grounded


def test_ground_numeric_slots_prefers_rule_parser_value_over_llm_value():
    slots = {"intent": "compare", "amount": 99_000_000}
    grounded = routes_module._ground_numeric_slots(dict(slots), "3천만원 신용대출 비교해줘")
    assert grounded["amount"] == 30_000_000


def test_ground_numeric_slots_keeps_value_present_literally_in_utterance():
    slots = {"intent": "compare", "term_months": 36}
    grounded = routes_module._ground_numeric_slots(dict(slots), "36개월로 신용대출 비교해줘")
    assert grounded["term_months"] == 36
