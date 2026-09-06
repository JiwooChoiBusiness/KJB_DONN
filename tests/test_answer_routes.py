"""답변 경로(direct/internal/external), 노드 카드, 리소스 패널, KB 답변 형식, 외부 검색
테스트 (SPEC 2.11).

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처를 재사용한다(패턴은
tests/test_chat_stream.py, tests/test_explain.py와 같다). 실 네트워크 호출은 하지 않는다
(그라운딩 응답은 전부 모킹). `_llm_provider`는 항상 `monkeypatch.setattr`로 바꾸거나
직접 대입 후 원상복구한다.
"""
from __future__ import annotations

import json
from typing import Any, Optional

# 주의(임포트 순서 고정): `tests.test_api`를 다른 `app.*` 임포트보다 먼저 들여와야 한다.
# 이 파일명("test_answer_routes")이 알파벳순으로 "test_api"보다 앞서기 때문에, pytest가
# 파일을 이름순으로 수집할 때 이 모듈이 test_api.py보다 먼저 임포트된다. tests/test_api.py는
# 자기 자신을 임포트하는 시점에 DONN_DB_PATH/GEMINI_API_KEYS/GEMINI_MODEL_CHAIN 환경변수를
# 먼저 설정한 "뒤에" app.data.db/app.api.routes 등을 임포트해 테스트 격리를 보장한다
# (그 모듈들은 임포트 시점에 딱 한 번 환경변수를 읽어 전역 상수/전역 provider 인스턴스를
# 고정하므로, 나중에 환경변수를 바꿔도 소용없다). 만약 이 파일이 `app.api.routes` 등을
# tests.test_api보다 먼저 임포트해버리면, 그 임포트가 실제 `.env`(운영 키, 운영
# data/donn.db)로 전역 상태를 고정시켜버려 다른 테스트 파일들까지 오염시킨다(2026-09-06
# 실측: test_health의 model_chain이 실제 Gemini 모델 목록으로 나오고, 상품 비교 테스트가
# 실제 DB의 공시 상품과 섞여 개수가 어긋났다). 그래서 이 import를 파일의 첫 줄에 둔다.
from tests.test_api import _FakeUnavailableProvider, _clear_session, client

from app.api import routes as routes_module  # noqa: E402
from app.llm.provider import LLMResult  # noqa: E402
from app.services import actions as actions_service  # noqa: E402
from app.services import answer as answer_service  # noqa: E402
from tests.test_chat_stream import _parse_sse_events  # noqa: E402

# 주의: `from app.kb import search as x`나 `import app.kb.search as x`는 둘 다
# app/kb/__init__.py가 함수 `search`를 재노출해서(`from .search import (..., search, ...)`)
# 이름 `x`가 서브모듈이 아니라 그 함수로 재바인딩된다(app/services/answer.py 상단 주석
# 참고. "as" 바인딩은 항상 최상위 패키지에서 속성 접근으로 값을 구하기 때문이다).
# `from app.kb.search import 이름1, 이름2`(구체적 이름을 나열하는 형태)만
# `sys.modules["app.kb.search"]`를 직접 참조해 이 문제를 피해간다.
from app.kb.search import ANSWER_THRESHOLD, MIN_SCORE, load_docs as _kb_load_docs


def _reply_route_body(message: str, provider: Any = None) -> dict[str, Any]:
    if provider is not None:
        routes_module._llm_provider = provider
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    return r.json()


def _clear_action_explanations_cache() -> None:
    """`explanations` 테이블(kind="action")을 비운다.

    action 의도 채팅은 항상 `explain_service.explain_action(..., refresh=False)`를 타므로
    (SPEC 2.8/2.9), `_FakeUnavailableProvider`처럼 LLM이 없는 더미로 한 번 호출하면 그
    persona+action_id 조합의 템플릿 문장이 캐시에 영구히 남는다. 다른 테스트 파일
    (tests/test_chat_stream.py)이 같은 persona(P1)로 실제 LLM 설명 문장을 기대하는 테스트를
    갖고 있어(파일명 알파벳순으로 이 파일이 먼저 수집되어 먼저 실행된다), 캐시를 남겨두면
    그 테스트가 이 파일이 만든 캐시를 대신 읽어 실패한다(2026-09-06 실측). 테스트 자체가
    끝나면 반드시 정리한다."""
    from app.data import db as db_module

    conn = db_module.get_conn()
    try:
        conn.execute("DELETE FROM explanations WHERE kind = 'action'")
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# (a) 경로 결정: direct / internal(KB 히트) / external(KB 미히트)
# ---------------------------------------------------------------------------


def test_direct_route_for_greeting(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("안녕, 뭘 도와줄 수 있어?")
    assert body["route"] == "direct"
    assert body["answer_format"] == "text"
    assert body["resources"] == []
    assert body["llm_used"] is False
    assert "추천" not in body["reply_text"]
    assert "{" not in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")


def test_internal_route_for_kb_hit_uses_markdown_and_kb_resource(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("금리인하요구권 요건이 뭐야")
    assert body["route"] == "internal"
    assert body["answer_format"] == "markdown"
    assert "**핵심**" in body["reply_text"]
    assert body["reply_text"].split("\n")[0]  # 한 줄 요약이 비어 있지 않다
    kinds = {r["kind"] for r in body["resources"]}
    assert "kb" in kinds
    kb_res = next(r for r in body["resources"] if r["kind"] == "kb")
    assert kb_res["ref"].startswith("rate-cut-request")
    assert kb_res["url"]
    action = body.get("action") or {}
    assert action.get("type") == "open_kb"
    assert action.get("payload", {}).get("slug") == "rate-cut-request"
    client.delete(f"/api/chats/{body['chat_id']}")


def test_external_route_for_kb_miss_falls_back_to_official_links(monkeypatch):
    """--llm rule 등가(provider에 search_answer가 없음): 네트워크 없이 kb/*.md의 공식
    기관 링크로 폴백해야 한다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("요즘 뉴스에 나오는 경제 소식 알려줘")
    assert body["route"] == "external"
    assert body["llm_used"] is False
    assert body["resources"], "그라운딩 불가 시에도 공식 안내 링크 리소스는 있어야 한다"
    assert all(r["kind"] == "external" for r in body["resources"])
    assert all(r["url"] for r in body["resources"])
    client.delete(f"/api/chats/{body['chat_id']}")


def test_crisis_reply_has_safety_route(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    body = _reply_route_body("빚 때문에 죽고 싶어요")
    assert body["route"] == "safety"
    client.delete(f"/api/chats/{body['chat_id']}")


# ---------------------------------------------------------------------------
# (b) 노드 steps/resource_refs 존재, resources kind 구성
# ---------------------------------------------------------------------------


def test_kb_node_trace_has_steps_and_matching_resource_refs(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("중도상환수수료 언제까지 내야 해")
    kb_stage = next(s for s in body["trace"] if s["id"] == "kb")
    assert kb_stage["steps"], "kb 노드는 사람이 읽는 단계 문장을 가져야 한다"
    assert kb_stage["resource_refs"], "kb 노드는 참조 리소스 ref 목록을 가져야 한다"
    resource_refs_in_panel = {r["ref"] for r in body["resources"]}
    assert set(kb_stage["resource_refs"]).issubset(resource_refs_in_panel)
    # SPEC 2.9 계약 유지 확인: start/종료 이벤트 모두 steps/resource_refs 키를 가진다.
    for stage in body["trace"]:
        assert "steps" in stage and "resource_refs" in stage


def test_compare_intent_resources_include_products_and_profile(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P1").status_code == 200
    body = _reply_route_body("신용대출 500만원 12개월로 공시 비교해줘")
    assert body["route"] == "internal"
    kinds = {r["kind"] for r in body["resources"]}
    assert "products" in kinds
    assert "profile" in kinds
    assert "loan" in kinds
    products_res = next(r for r in body["resources"] if r["kind"] == "products")
    assert "건" in products_res["title"]
    # SEV2 2026-09-06 리뷰: compare 의도는 이제 "compute" 대신 "products"(공시 자료)
    # 노드를 실제로 방출한다.
    products_stage = next(s for s in body["trace"] if s["id"] == "products")
    assert products_stage["steps"]
    client.delete(f"/api/chats/{body['chat_id']}")
    client.delete("/api/session")


def test_action_intent_resources_include_calc_card(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        body = _reply_route_body("이번 달에 나 뭐부터 갚아야 해?")
        kinds = {r["kind"] for r in body["resources"]}
        assert "calc" in kinds
        assert "profile" in kinds
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")
        _clear_action_explanations_cache()


def test_multi_node_kb_attachment_when_institutional_keyword_present(monkeypatch):
    """SPEC 2.11 "여러 노드": action 의도인데 발화에 제도 키워드(금리인하요구권)가 뚜렷하면
    kb 노드도 함께 실행되고 리소스·칩이 합쳐진다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P2").status_code == 200
    try:
        body = _reply_route_body("금리인하요구권 관련해서 이번 달에 뭐부터 해야 해?")
        assert any(s["id"] == "kb" for s in body["trace"])
        assert any(r["kind"] == "kb" for r in body["resources"])
        assert any(c["params"].get("slug") == "rate-cut-request" for c in body["chips"])
        # 주 의도(action)의 응답 자체는 그대로 internal 경로를 유지한다.
        assert body["route"] == "internal"
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/session")
        _clear_action_explanations_cache()


def test_multi_node_not_attached_for_generic_schedule_message(monkeypatch):
    """제도 키워드가 뚜렷하지 않은 평범한 발화("상환표 보여줘")는 kb 노드를 추가로 붙이지
    않는다(바이그램 점수만으로는 학자금 문서와 우연히 크게 겹칠 수 있어 이 정밀 신호를
    쓰지 않는다, app.services.answer.find_institutional_keyword_doc 참고)."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("상환표 보여줘")
    # SEV2 2026-09-06 리뷰: schedule 의도는 이제 "debt_data" 노드를 방출한다.
    assert [s["id"] for s in body["trace"]] == ["guard", "intent", "debt_data", "explain", "check"]


# ---------------------------------------------------------------------------
# (c) KB LLM 답변의 숫자 검증(문단에 없는 숫자 -> 규칙 폴백)
# ---------------------------------------------------------------------------


class _FakeKbExplainProvider:
    def __init__(self, summary: str, points: list[str]):
        self._summary = summary
        self._points = points

    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        data = {"summary": self._summary, "points": self._points}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-kb-model", key_index=0, latency_ms=4, usage={})


def _rate_cut_doc():
    return next(d for d in _kb_load_docs() if d.slug == "rate-cut-request")


def test_format_kb_answer_rejects_ungrounded_number_and_falls_back_to_rule():
    doc = _rate_cut_doc()
    sections = answer_service.kb_reference_sections(doc)
    fake = _FakeKbExplainProvider(
        summary="지난달 신청 건수가 9999건으로 늘었어요.",  # 문단에 없는 숫자를 지어냄
        points=[f"**{name}**: 확인해볼 만해요." for name in sections],
    )
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(doc, None, fake, [])
    assert llm_used is False
    assert "ungrounded_number" in problems
    assert "**핵심**" in markdown  # 규칙 렌더링도 같은 마크다운 구조를 쓴다


def test_format_kb_answer_accepts_text_with_no_new_numbers():
    doc = _rate_cut_doc()
    sections = answer_service.kb_reference_sections(doc)
    fake = _FakeKbExplainProvider(
        summary="신용 상태가 좋아졌을 때 기존 대출 금리를 낮춰달라고 요구할 수 있는 권리예요.",
        points=[f"**{name}**: 참고할 내용이에요." for name in sections],
    )
    # SPEC 2.16: 기본값(detail="full")은 요약 2문장·섹션 최대 5개를 요구한다. 이 테스트는
    # 그라운딩(숫자 검사) 자체를 보는 단위 테스트라 길이 계약이 기존(2.8) 그대로인
    # detail="brief"로 호출해 1문장 요약을 그대로 쓴다.
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(
        doc, None, fake, [], detail="brief",
    )
    assert llm_used is True
    assert problems == []
    assert model == "fake-kb-model"
    assert "**핵심**" in markdown


def test_format_kb_answer_rejects_banned_company_name():
    doc = _rate_cut_doc()
    sections = answer_service.kb_reference_sections(doc)
    fake = _FakeKbExplainProvider(
        summary="가상은행A에 문의하면 도와줘요.",
        points=[f"**{name}**: 확인이 필요해요." for name in sections],
    )
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(
        doc, None, fake, ["가상은행A"],
    )
    assert llm_used is False
    assert any(p.startswith("banned_term:") for p in problems)


def test_format_kb_answer_rule_path_when_llm_unavailable():
    doc = _rate_cut_doc()
    markdown, llm_used, model, latency_ms, problems = answer_service.format_kb_answer(
        doc, None, _FakeUnavailableProvider(), [],
    )
    assert llm_used is False
    assert model is None
    assert "llm_unavailable" in problems
    assert "**핵심**" in markdown


# ---------------------------------------------------------------------------
# (d) 외부 검색 모킹: 그라운딩 응답 파싱, 금칙어 회사명 -> 요약 제거·링크 유지
# ---------------------------------------------------------------------------


class _FakeSearchProvider:
    def __init__(self, text: str, sources: list[dict[str, str]], search_html: Optional[str] = None,
                 available_flag: bool = True):
        self._text = text
        self._sources = sources
        self._search_html = search_html
        self._available = available_flag
        self.external_search_enabled = True

    def available(self) -> bool:
        return self._available

    def search_answer(self, question, system, *, deadline_seconds=None):  # noqa: ANN001
        data = {"sources": self._sources, "queries": ["기준금리"],
                "search_entry_point_html": self._search_html or ""}
        return LLMResult(data=data, text=self._text, model="fake-search-model",
                          key_index=0, latency_ms=11, usage={})


def test_external_answer_parses_grounding_sources_and_search_entry_point():
    provider = _FakeSearchProvider(
        text="한국은행 기준금리는 금융통화위원회가 결정합니다.",
        sources=[{"uri": "https://www.bok.or.kr/x", "title": "한국은행 기준금리"}],
        search_html="<div class=\"google-search\">제안</div>",
    )
    text, resources, llm_used, model, problems, search_html = answer_service.external_answer(
        "기준금리 알려줘", provider, [],
    )
    assert llm_used is True
    assert model == "fake-search-model"
    assert len(resources) == 1
    assert resources[0].kind == "external"
    assert resources[0].url == "https://www.bok.or.kr/x"
    assert "(외부 검색 요약이라" in text
    assert search_html == "<div class=\"google-search\">제안</div>"


def test_external_answer_drops_summary_when_banned_company_name_present():
    provider = _FakeSearchProvider(
        text="가상은행A에서 확인해보세요.",
        sources=[{"uri": "https://www.fss.or.kr/x", "title": "금감원 안내"}],
    )
    text, resources, llm_used, model, problems, _ = answer_service.external_answer(
        "기준금리 알려줘", provider, ["가상은행A"],
    )
    assert "가상은행A" not in text
    assert "banned_term_removed" in problems
    assert resources, "요약은 버려도 리소스(출처 링크)는 유지해야 한다"
    assert resources[0].url == "https://www.fss.or.kr/x"


def test_external_answer_falls_back_to_official_links_without_search_answer_method():
    """provider에 search_answer 자체가 없으면(테스트 더블 호환) 네트워크 없이 폴백한다."""
    text, resources, llm_used, model, problems, search_html = answer_service.external_answer(
        "기준금리 알려줘", _FakeUnavailableProvider(), [],
    )
    assert llm_used is False
    assert model is None
    assert search_html is None
    assert resources
    assert all(r.kind == "external" for r in resources)


def test_external_answer_empty_grounding_still_backfills_official_links():
    provider = _FakeSearchProvider(text="답변을 준비하지 못했어요.", sources=[])
    text, resources, llm_used, model, problems, _ = answer_service.external_answer(
        "기준금리 알려줘", provider, [],
    )
    assert llm_used is True
    assert "no_grounding_sources" in problems
    assert resources  # 공식 링크로 보강됨


# ---------------------------------------------------------------------------
# (e) meta_json 저장과 GET /api/chats/{id}/messages 반환
# ---------------------------------------------------------------------------


def test_chat_message_meta_round_trips_through_get_messages(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()
    body = _reply_route_body("금리인하요구권 요건이 뭐야")
    chat_id = body["chat_id"]

    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    reply_msg = next(m for m in msgs if m["role"] == "reply")
    user_msg = next(m for m in msgs if m["role"] == "user")

    assert reply_msg["route"] == body["route"] == "internal"
    assert reply_msg["answer_format"] == body["answer_format"] == "markdown"
    assert reply_msg["resources"] == body["resources"]
    assert reply_msg["model"] == body["model"]

    # 사용자 메시지에는 route 개념이 없다(기본값으로 채워진다).
    assert user_msg["route"] is None
    assert user_msg["resources"] == []
    assert user_msg["answer_format"] == "text"

    client.delete(f"/api/chats/{chat_id}")


# ---------------------------------------------------------------------------
# (f) 스트림에서도 route/resources/answer_format이 reply에 실리는지
# ---------------------------------------------------------------------------


def test_chat_stream_reply_event_carries_route_and_resources(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    assert client.post("/api/session/persona/P1").status_code == 200

    r = client.post("/api/chat/stream", json={"message": "금리인하요구권 요건이 뭐야"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    assert events[-1][0] == "reply"
    reply = events[-1][1]

    assert reply["route"] == "internal"
    assert reply["answer_format"] == "markdown"
    assert reply["resources"]
    kb_stage = next(p for name, p in events if name == "stage" and p["id"] == "kb" and p["status"] != "start")
    assert kb_stage["steps"]

    client.delete(f"/api/chats/{reply['chat_id']}")
    client.delete("/api/session")


# ---------------------------------------------------------------------------
# (g) 기타: MIN_SCORE 별칭, R9 라벨, direct 슬롯필링 숫자 거부
# ---------------------------------------------------------------------------


def test_kb_min_score_is_alias_of_answer_threshold():
    assert MIN_SCORE == ANSWER_THRESHOLD


def test_action_ratio_label_for_max_debt_service_ratio_present():
    assert actions_service._RATIO_PCT_LABELS["max_debt_service_ratio"] == "원리금상환비율 기준"


class _FakeDirectDigitProvider:
    def available(self) -> bool:
        return True

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        data = {"summary": "부채 상환표 계산 등 3가지를 도와드려요."}  # 숫자 포함 -> 실패해야 함
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-direct-model", key_index=0, latency_ms=2, usage={})


def test_build_direct_answer_falls_back_when_llm_outputs_digit():
    text, llm_used, model, latency_ms, problems = answer_service.build_direct_answer(
        _FakeDirectDigitProvider(), [],
    )
    assert llm_used is False
    assert text == answer_service.DIRECT_ANSWER_FALLBACK_TEXT
    assert any("digit" in p for p in problems)


def test_build_direct_answer_unavailable_provider_uses_fallback():
    text, llm_used, model, latency_ms, problems = answer_service.build_direct_answer(
        _FakeUnavailableProvider(), [],
    )
    assert llm_used is False
    assert model is None
    assert text == answer_service.DIRECT_ANSWER_FALLBACK_TEXT


def test_time_sensitive_question_goes_external_even_if_kb_bigrams_overlap(monkeypatch):
    """"요즘 기준금리 얼마야?"는 "금리" 바이그램 때문에 금리인하요구권 문서와 점수가 겹치지만
    시점성 질문이고 문서 키워드가 발화에 없으므로 external(규칙 모드에서는 공식 링크 폴백)이어야 한다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    r = client.post("/api/chat", json={"message": "요즘 기준금리 얼마야?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "external"
    assert "금리인하요구권" not in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")


def test_keyword_doc_wins_over_time_sensitive_words(monkeypatch):
    """문서 키워드("금리인하요구권")가 발화에 그대로 있으면 "지금" 같은 시점성 단어가 있어도 internal이다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    r = client.post("/api/chat", json={"message": "지금 금리인하요구권 신청하면 돼?"})
    body = r.json()
    assert body["route"] == "internal"
    assert any(res["kind"] == "kb" and "금리인하요구권" in res["title"] for res in body["resources"])
    client.delete(f"/api/chats/{body['chat_id']}")


def test_greeting_stays_direct_even_if_llm_says_faq(monkeypatch):
    """LLM 추출이 인사를 faq로 분류해도 규칙 파서가 direct면 direct 경로로 답한다(KB 오매칭 방지)."""
    from tests.test_api import _FakeSuccessProvider

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeSuccessProvider({"intent": "faq"}))
    r = client.post("/api/chat", json={"message": "안녕, 뭐 할 수 있어?"})
    body = r.json()
    assert body["route"] == "direct"
    assert not any(res["kind"] == "kb" for res in body["resources"])
    client.delete(f"/api/chats/{body['chat_id']}")

