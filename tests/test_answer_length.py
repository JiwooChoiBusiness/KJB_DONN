"""답변 길이 "자세히" 기본값 테스트 (SPEC 2.16, PMO 요청 2026-09-06).

`tests.test_api`가 모듈 임포트 시 DONN_DB_PATH/GEMINI_API_KEYS/GEMINI_MODEL_CHAIN
환경변수를 app.* 모듈이 임포트되기 전에 먼저 설정한다. 이 파일명("test_answer_length")이
알파벳순으로 "test_api"보다 앞서(비교: "test_answer" < "test_api") pytest가 이 모듈을 먼저
수집하므로, tests/test_answer_routes.py 헤더 주석과 같은 이유로 이 import를 파일의 첫
줄에 둔다.
"""
from __future__ import annotations

import json

from tests.test_api import _FakeUnavailableProvider, _clear_session, client

from app.api import routes as routes_module  # noqa: E402
from app.core.capacity import compute_capacity  # noqa: E402
from app.core.schedule import build_schedule  # noqa: E402
from app.data import synthetic  # noqa: E402
from app import kb as kb_search  # noqa: E402
from app.llm.provider import LLMResult, LLMUnavailable  # noqa: E402
from app.models import ActionCard, CompareResult, UserProfile  # noqa: E402
from app.services import answer as answer_service  # noqa: E402
from app.services import explain as explain_service  # noqa: E402

# ---------------------------------------------------------------------------
# 가짜 provider
# ---------------------------------------------------------------------------


class _FakeCompareExplainProvider:
    """compare_summary_v1용 가짜 provider. summary 문장 수를 테스트가 직접 조절한다."""

    def __init__(self, summary: str, reasons: "list[str] | None" = None):
        self._summary = summary
        self._reasons = reasons
        self.calls = 0

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        self.calls += 1
        placeholders = slots.get("placeholders", {})
        reasons = self._reasons
        if reasons is None:
            reasons = []
            for letter in ("a", "b", "c"):
                if f"label_{letter}" in placeholders:
                    reasons.append(f"{{label_{letter}}} 조건은 금리 {{rate_{letter}}}이에요.")
        data = {"summary": self._summary, "reasons": reasons}
        return LLMResult(
            data=data, text=json.dumps(data, ensure_ascii=False),
            model="fake-length-model", key_index=0, latency_ms=1, usage={},
        )


class _FakeDirectProvider:
    """direct_answer_v1용 가짜 provider. 의도 추출은 규칙 파서로 폴백시킨다."""

    def __init__(self, text: str):
        self._text = text

    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        raise LLMUnavailable("테스트: 규칙 파서로 의도 추출")

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        data = {"summary": self._text}
        return LLMResult(
            data=data, text=json.dumps(data, ensure_ascii=False),
            model="fake-direct-model", key_index=0, latency_ms=1, usage={},
        )


def _fresh_compare_decision(persona_id: str = "P1") -> str:
    """P1 페르소나로 신용대출 비교를 실행해 decision_id를 돌려준다(tests/test_explain.py의
    _fresh_compare_decision과 같은 패턴 - 픽스처 상품엔 신용점수 구간별 티어가 없어
    credit_band를 None으로 되돌려야 픽스처 4종을 모두 통과시킨다)."""
    r = client.post(f"/api/session/persona/{persona_id}")
    assert r.status_code == 200

    ctx = client.post(
        "/api/compare/prepare", json={"intent": "compare", "params": {"category": "credit"}}
    ).json()
    ctx["user_confirmed"] = True
    ctx["lender_groups"] = ["bank", "savings_bank"]
    ctx["credit_band"] = None

    r2 = client.post("/api/compare/run", json=ctx)
    assert r2.status_code == 200, r2.text
    return r2.json()["decision_id"]


# ---------------------------------------------------------------------------
# (a) full 모드 길이 검증: 짧은 응답 -> too_short -> 템플릿, 5문장 -> 통과
# ---------------------------------------------------------------------------


def test_full_detail_short_two_sentence_summary_falls_back_to_template(monkeypatch):
    """full(기본)의 compare_summary는 최소 4문장이라 2문장 응답은 too_short로 템플릿이 된다."""
    decision_id = _fresh_compare_decision()
    short_summary = "{label_a} 조건이 금리 {rate_a}라서 좋아요. 확인해 보세요."
    fake = _FakeCompareExplainProvider(short_summary)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r = client.post(f"/api/compare/{decision_id}/explain", json={"refresh": True})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "template"
    assert "summary:too_short" in body["problems"], body["problems"]
    _clear_session()


def test_full_detail_five_sentence_summary_passes_as_llm(monkeypatch):
    """full의 compare_summary 상한(4~6문장, 700자) 안에 드는 5문장 응답은 그대로 통과한다."""
    decision_id = _fresh_compare_decision()
    long_summary = (
        "공시 상품 {candidates_total} 중 상위 {shown_count}를 비교했어요. "
        "정렬 기준으로 보면 {label_a}이 금리 {rate_a}라서 가장 먼저 확인해 볼 만해요. "
        "이번 비교는 금액 {amount} 조건으로 진행했어요. "
        "기간은 {term_months}으로 계산했어요. "
        "실제 승인 여부는 심사에 따라 달라질 수 있어요."
    )
    fake = _FakeCompareExplainProvider(long_summary)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r = client.post(f"/api/compare/{decision_id}/explain", json={"refresh": True})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "llm", body["problems"]
    assert body["problems"] == []
    assert "{" not in body["summary"]
    _clear_session()


def test_brief_detail_two_sentence_summary_passes(monkeypatch):
    """detail="brief"는 기존(2.8) 상한을 쓰므로 2문장 응답도 통과한다."""
    decision_id = _fresh_compare_decision()
    short_summary = "{label_a} 조건이 금리 {rate_a}라서 좋아요. 확인해 보세요."
    fake = _FakeCompareExplainProvider(short_summary)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r = client.post(f"/api/compare/{decision_id}/explain", json={"refresh": True, "detail": "brief"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "llm", body["problems"]
    assert body["problems"] == []
    _clear_session()


def test_repetitive_sentences_detected_and_falls_back_to_template(monkeypatch):
    """문장 두 개의 어절이 70% 이상 겹치면 repetitive로 걸려 템플릿이 된다."""
    decision_id = _fresh_compare_decision()
    repetitive_summary = (
        "{label_a} 조건이 금리 {rate_a}라서 확인해 볼 만해요. "
        "{label_a} 조건이 금리 {rate_a}라서 다시 확인해 볼 만해요. "
        "이번 비교는 금액 {amount} 조건으로 진행했어요. "
        "기간은 {term_months}으로 계산했어요."
    )
    fake = _FakeCompareExplainProvider(repetitive_summary)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r = client.post(f"/api/compare/{decision_id}/explain", json={"refresh": True})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "template"
    assert "summary:repetitive" in body["problems"], body["problems"]
    _clear_session()


# ---------------------------------------------------------------------------
# (b) 비교 facts 보강: caveats·next_steps·rate_vs_current_loan, 숫자 없음(D4)
# ---------------------------------------------------------------------------


def test_compare_slots_facts_include_caveats_next_steps_and_rate_vs_current_loan():
    """decisions_service.get()/session_service.get_profile()은 요청 sid에 묶여 있어(SPEC
    "공개 데모 보강" 항목) 이 테스트 프로세스에서 직접 부르면 방금 TestClient로 만든
    결정을 찾지 못한다(tests/test_review_fixes.py의 같은 주석 참고). 그래서 HTTP
    엔드포인트로 결정과 프로필을 다시 읽어 온다."""
    decision_id = _fresh_compare_decision()
    record_json = client.get(f"/api/decisions/{decision_id}").json()
    result = CompareResult.model_validate(record_json["result"])
    profile = UserProfile.model_validate(client.get("/api/profile").json())

    facts, placeholders, values = explain_service.compare_slots(result, profile)

    assert facts["caveats"], "caveats가 비어 있으면 안 된다"
    assert facts["next_steps"], "next_steps가 비어 있으면 안 된다"
    assert facts["items"], "items가 비어 있으면 안 된다"
    for item in facts["items"]:
        assert "rate_vs_current_loan" in item
        assert item["rate_vs_current_loan"] in (
            "현재 대출 금리보다 낮음", "현재 대출 금리보다 높음",
            "현재 대출 금리와 같음", "비교 대상 없음",
        )
        assert "institution_kind" in item

    # D4: LLM에 실제로 보내는 페이로드(facts+placeholders)에는 숫자가 하나도 없어야 한다.
    explain_service.slotfill.assert_no_digits({"facts": facts, "placeholders": placeholders})

    if "current_loan_type" in facts:
        assert "current_rate" in placeholders

    _clear_session()


def test_ref_suffix_differs_only_for_non_full_detail():
    assert explain_service.ref_suffix("full") == ""
    assert explain_service.ref_suffix("brief") == "#brief"


# ---------------------------------------------------------------------------
# (c) 행동 카드 facts 보강: next_actions/caveats는 숫자 있는 문장을 제외
# ---------------------------------------------------------------------------


def test_action_slots_next_actions_excludes_steps_and_caveats_with_digits():
    profile = synthetic.get_persona("P1")
    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)

    card = ActionCard(
        id="test-next-actions",
        rule_id="R2",
        title="테스트 카드",
        summary="테스트용 요약입니다.",
        steps=["1. 서류를 준비하세요.", "2. 300000원을 입금하세요.", "여유 자금을 확인하세요."],
        caveats=["실제 조건은 다를 수 있어요.", "한도는 5000만원까지예요."],
    )

    facts, placeholders, values = explain_service.action_slots(card, profile, capacity)

    assert "서류를 준비하세요." in facts["next_actions"]
    assert "여유 자금을 확인하세요." in facts["next_actions"]
    assert not any("300000" in s for s in facts["next_actions"])
    assert len(facts["next_actions"]) == 2

    assert facts["caveats"] == ["실제 조건은 다를 수 있어요."]

    explain_service.slotfill.assert_no_digits({"facts": facts, "placeholders": placeholders})


# ---------------------------------------------------------------------------
# (d) 캐시 키가 detail별로 다른지
# ---------------------------------------------------------------------------


def test_explain_cache_key_differs_by_detail(monkeypatch):
    decision_id = _fresh_compare_decision()
    long_summary = (
        "공시 상품 {candidates_total} 중 상위 {shown_count}를 비교했어요. "
        "정렬 기준으로 보면 {label_a}이 금리 {rate_a}라서 가장 먼저 확인해 볼 만해요. "
        "이번 비교는 금액 {amount} 조건으로 진행했어요. "
        "기간은 {term_months}으로 계산했어요."
    )
    fake = _FakeCompareExplainProvider(long_summary)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r_full = client.post(f"/api/compare/{decision_id}/explain")
    assert r_full.status_code == 200
    assert r_full.json()["cached"] is False
    assert fake.calls == 1

    # brief는 별도 ref_id("...#brief")를 쓰므로 방금 만든 full 캐시를 재사용하지 않고
    # LLM을 다시 부른다.
    r_brief = client.post(f"/api/compare/{decision_id}/explain", json={"detail": "brief"})
    assert r_brief.status_code == 200
    assert r_brief.json()["cached"] is False
    assert fake.calls == 2

    # 같은 detail(full)로 다시 요청하면 그제서야 캐시를 재사용한다.
    r_full_again = client.post(f"/api/compare/{decision_id}/explain")
    assert r_full_again.status_code == 200
    assert r_full_again.json()["cached"] is True
    assert fake.calls == 2

    # GET도 같은 detail 쿼리로 같은 ref_id(캐시 키)를 찾는다(full은 접미사 없음, brief는 "#brief").
    r_get_full = client.get(f"/api/compare/{decision_id}/explain")
    assert r_get_full.status_code == 200
    assert r_get_full.json()["ref_id"] == decision_id
    r_get_brief = client.get(f"/api/compare/{decision_id}/explain", params={"detail": "brief"})
    assert r_get_brief.status_code == 200
    assert r_get_brief.json()["ref_id"] == f"{decision_id}#brief"

    _clear_session()


def test_get_compare_explain_falls_back_to_other_detail_when_missing(monkeypatch):
    """web/app.js의 getCompareExplain()은 detail 쿼리를 붙이지 않는다. 사용자가 "간단히"로
    설정해 brief로만 저장된 설명이 있어도 detail 쿼리 없는 GET(기본값 full)이 그 brief
    설명을 찾아 돌려줘야 한다(둘 다 없을 때만 404)."""
    decision_id = _fresh_compare_decision()
    fake = _FakeCompareExplainProvider("{label_a} 조건이 금리 {rate_a}라서 좋아요. 확인해 보세요.")
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r_brief = client.post(f"/api/compare/{decision_id}/explain", json={"detail": "brief"})
    assert r_brief.status_code == 200

    r_get_default = client.get(f"/api/compare/{decision_id}/explain")  # detail 쿼리 없음
    assert r_get_default.status_code == 200
    assert r_get_default.json()["ref_id"] == f"{decision_id}#brief"

    _clear_session()


# ---------------------------------------------------------------------------
# (e) POST /api/chat body의 detail이 실제로 전달되는지
# ---------------------------------------------------------------------------


def test_chat_detail_field_propagates_to_direct_answer_length_rule(monkeypatch):
    """direct 의도에서 detail="full"(기본)은 최소 2문장을 요구해 1문장 응답을 템플릿으로
    되돌리고, detail="brief"를 명시하면 같은 1문장 응답이 그대로 통과한다."""
    one_sentence = "안녕하세요, 무엇이든 물어보세요."
    fake = _FakeDirectProvider(one_sentence)
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    _clear_session()

    r_full = client.post("/api/chat", json={"message": "안녕, 뭐 할 수 있어?"})
    assert r_full.status_code == 200
    body_full = r_full.json()
    assert body_full["llm_used"] is False
    client.delete(f"/api/chats/{body_full['chat_id']}")

    r_brief = client.post("/api/chat", json={"message": "안녕, 뭐 할 수 있어?", "detail": "brief"})
    assert r_brief.status_code == 200
    body_brief = r_brief.json()
    assert body_brief["llm_used"] is True
    assert body_brief["reply_text"] == one_sentence
    client.delete(f"/api/chats/{body_brief['chat_id']}")


# ---------------------------------------------------------------------------
# (f) 제도 안내(KB) 답변: 핵심 최대 5개, "이렇게 활용하세요" 줄
# ---------------------------------------------------------------------------


def test_kb_answer_full_detail_has_usage_line_and_at_most_five_points():
    doc = next(d for d in kb_search.load_docs() if d.slug == "rate-cut-request")
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(
        doc, None, _FakeUnavailableProvider(), [],
    )  # LLM 불가 -> 규칙 렌더링(detail 기본값 full)
    assert llm_used is False
    assert "이렇게 활용하세요:" in markdown
    bullet_lines = [line for line in markdown.split("\n") if line.startswith("- ")]
    assert 1 <= len(bullet_lines) <= 5
