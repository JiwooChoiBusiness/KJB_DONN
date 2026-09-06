"""대화 스트리밍과 생각 과정(SPEC 2.9) 테스트.

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처를 그대로 재사용한다
(패턴은 tests/test_explain.py, tests/test_crisis_followup.py와 같다). `_llm_provider`는
항상 `monkeypatch.setattr`로 바꿔 테스트가 끝나면 자동으로 원래대로 돌아가게 한다(다른
테스트 파일의 순서에 영향을 주지 않기 위해).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.api import routes as routes_module
from app.llm import slotfill
from app.llm.provider import LLMResult
from tests.test_api import _FakeUnavailableProvider, _clear_session, client

# SEV2 2026-09-06 리뷰: schedule/scenario 의도는 이제 "compute" 대신 "debt_data"(내 부채
# 자료) 노드를 실제로 방출한다(app/api/routes.py의 _STAGE_LABELS 항목이 그동안 emit되지
# 않던 죽은 라벨이었다).
_STAGE_IDS = ("guard", "intent", "debt_data", "explain", "check")


def _parse_sse_events(text: str) -> list[tuple[str, Any]]:
    """`event: <name>\\ndata: <json>\\n\\n` 프레임을 순서대로 (name, payload) 목록으로 판다."""
    events: list[tuple[str, Any]] = []
    for block in text.strip("\n").split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        name: Optional[str] = None
        data_line: Optional[str] = None
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_line = line[len("data:"):].strip()
        if name is not None and data_line is not None:
            events.append((name, json.loads(data_line)))
    return events


def _terminal_stage_map(events: list[tuple[str, Any]]) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for name, p in events if name == "stage" and p["status"] != "start"}


class _FakeChatProvider:
    """POST /api/chat[/stream] 테스트용 가짜 provider. extract는 고정 슬롯을, explain은
    template_id별로 미리 정한 요약 문장을 돌려준다. 호출 횟수를 세어 위기 분기에서
    LLM이 전혀 호출되지 않는지 확인하는 데 쓴다."""

    def __init__(self, extract_data: dict[str, Any], explain_summary: Any = None):
        self.extract_data = extract_data
        self.explain_summary = explain_summary
        self.extract_calls = 0
        self.explain_calls = 0

    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        self.extract_calls += 1
        return LLMResult(data=dict(self.extract_data), text="{}", model="fake-extract-model",
                          key_index=0, latency_ms=5, usage={})

    def explain(self, slots, template_id, system, schema=None, deadline_seconds=None):  # noqa: ANN001
        self.explain_calls += 1
        summary = self.explain_summary
        if isinstance(summary, dict):
            summary = summary.get(template_id, "")
        data = {"summary": summary or ""}
        return LLMResult(data=data, text=json.dumps(data, ensure_ascii=False),
                          model="fake-explain-model", key_index=0, latency_ms=7, usage={})


# ---------------------------------------------------------------------------
# (a) stage 순서 + reply.trace가 저장된 메시지의 trace와 같은지
# ---------------------------------------------------------------------------


def test_chat_stream_stage_order_and_reply_trace_matches_stored_message(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()

    r = client.post("/api/chat/stream", json={"message": "상환표 보여줘"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = _parse_sse_events(r.text)
    stage_events = [(name, p) for name, p in events if name == "stage"]

    for stage_id in _STAGE_IDS:
        occurrences = [p for _, p in stage_events if p["id"] == stage_id]
        assert len(occurrences) == 2, f"{stage_id}: start+종료 2개여야 함, 실제 {len(occurrences)}"
        assert occurrences[0]["status"] == "start"
        assert occurrences[1]["status"] in ("done", "fallback", "skip")

    terminal_ids_in_order = [p["id"] for name, p in stage_events if p["status"] != "start"]
    assert terminal_ids_in_order == list(_STAGE_IDS)

    assert events[-1][0] == "reply"
    reply = events[-1][1]
    assert reply["chat_id"]
    terminal_events_in_order = [p for name, p in stage_events if p["status"] != "start"]
    assert reply["trace"] == terminal_events_in_order

    msgs = client.get(f"/api/chats/{reply['chat_id']}/messages").json()
    reply_msg = next(m for m in msgs if m["role"] == "reply")
    assert reply_msg["trace"] == terminal_events_in_order

    client.delete(f"/api/chats/{reply['chat_id']}")
    _clear_session()


# ---------------------------------------------------------------------------
# (b) compare 의도: LLM 설명 문장에 숫자가 채워지는지 / 숫자를 직접 쓰면 템플릿 폴백인지
# ---------------------------------------------------------------------------


def test_chat_stream_compare_intent_llm_explain_fills_numbers_into_reply(monkeypatch):
    fake = _FakeChatProvider(
        extract_data={"intent": "compare", "category": "credit", "amount": 20_000_000, "term_months": 36},
        explain_summary="신용대출을 {amount}, {term_months} 조건으로 준비했어요. 기간은 추정값이에요.",
    )
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    _clear_session()

    r = client.post("/api/chat/stream", json={"message": "신용대출 2천만원 36개월로 비교해줘"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    assert events[-1][0] == "reply"
    reply = events[-1][1]

    assert reply["llm_used"] is True
    assert "20,000,000원" in reply["reply_text"]
    assert "36개월" in reply["reply_text"]
    assert "아래 버튼으로" in reply["reply_text"]
    assert "{" not in reply["reply_text"]

    terminal = _terminal_stage_map(events)
    assert terminal["explain"]["status"] == "done"
    assert "fake-explain-model" in terminal["explain"]["detail"]

    client.delete(f"/api/chats/{reply['chat_id']}")
    _clear_session()


def test_chat_stream_compare_intent_llm_digit_leak_falls_back_to_template(monkeypatch):
    fake = _FakeChatProvider(
        extract_data={"intent": "compare", "category": "credit", "amount": 20_000_000, "term_months": 36},
        explain_summary="신용대출을 20000000원 조건으로 준비했어요.",  # 숫자를 직접 씀 -> 검증 실패
    )
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    _clear_session()

    r = client.post("/api/chat/stream", json={"message": "신용대출 2천만원 36개월로 비교해줘"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    reply = events[-1][1]

    assert "20,000,000원" in reply["reply_text"]
    assert "아래 버튼으로" in reply["reply_text"]
    assert "{" not in reply["reply_text"]

    terminal = _terminal_stage_map(events)
    assert terminal["explain"]["status"] == "fallback"
    assert "템플릿 문장 사용" in terminal["explain"]["detail"]

    client.delete(f"/api/chats/{reply['chat_id']}")
    _clear_session()


# ---------------------------------------------------------------------------
# (c) action 의도: explain_action 경로에서 LLM 문장이 응답에 쓰이는지
# ---------------------------------------------------------------------------


def test_chat_stream_action_intent_uses_llm_explain_sentence(monkeypatch):
    assert client.post("/api/session/persona/P1").status_code == 200
    fake = _FakeChatProvider(
        extract_data={"intent": "action"},
        explain_summary={"action_card_v1": "이번 상황을 확인했어요. 지금 여건에 맞게 살펴보세요."},
    )
    monkeypatch.setattr(routes_module, "_llm_provider", fake)

    r = client.post("/api/chat/stream", json={"message": "이번 달에 뭐부터 갚아야 해?"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    reply = events[-1][1]

    assert reply["reply_text"] == "이번 상황을 확인했어요. 지금 여건에 맞게 살펴보세요."
    assert reply["llm_used"] is True

    terminal = _terminal_stage_map(events)
    assert terminal["explain"]["status"] == "done"
    assert "fake-explain-model" in terminal["explain"]["detail"]

    client.delete(f"/api/chats/{reply['chat_id']}")
    _clear_session()


# ---------------------------------------------------------------------------
# (d) 위기 발화: explain skip이고 LLM이 전혀 호출되지 않는지
# ---------------------------------------------------------------------------


def test_chat_stream_crisis_message_skips_explain_and_never_calls_llm(monkeypatch):
    fake = _FakeChatProvider(extract_data={"intent": "faq"})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    _clear_session()

    r = client.post("/api/chat/stream", json={"message": "빚 때문에 죽고 싶어요"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    reply = events[-1][1]
    assert reply["llm_used"] is False

    terminal = _terminal_stage_map(events)
    assert terminal["guard"]["status"] == "done"
    assert "위기" in terminal["guard"]["detail"]
    assert terminal["explain"]["status"] == "skip"

    assert fake.extract_calls == 0
    assert fake.explain_calls == 0

    client.delete(f"/api/chats/{reply['chat_id']}")


# ---------------------------------------------------------------------------
# (e) POST /api/chat에도 trace가 실리는지
# ---------------------------------------------------------------------------


def test_post_chat_non_streaming_also_carries_trace(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    _clear_session()

    r = client.post("/api/chat", json={"message": "시나리오 보여줘"})
    assert r.status_code == 200
    body = r.json()
    assert body["trace"]
    assert [s["id"] for s in body["trace"]] == list(_STAGE_IDS)

    client.delete(f"/api/chats/{body['chat_id']}")


# ---------------------------------------------------------------------------
# (f) _build_chat_reply가 예외를 던지면 event: error 프레임이 오는지
# ---------------------------------------------------------------------------


def test_chat_stream_emits_error_event_when_build_chat_reply_raises(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    def _boom(message, base_params=None, emit=None):  # noqa: ANN001
        raise RuntimeError("강제 실패(테스트)")

    monkeypatch.setattr(routes_module, "_build_chat_reply", _boom)

    r = client.post("/api/chat/stream", json={"message": "상환표 보여줘"})
    assert r.status_code == 200
    events = _parse_sse_events(r.text)
    assert events, "빈 스트림이면 안 됨"
    assert events[-1][0] == "error"
    # SEV3 2026-09-06 리뷰: 원문 예외 메시지를 사용자에게 노출하지 않는다(원문은
    # logger.exception으로만 남는다). 항상 고정된 일반 안내 문장이어야 한다.
    assert "강제 실패" not in events[-1][1]["message"]
    assert events[-1][1]["message"] == "응답을 만들지 못했어요. 잠시 후 다시 시도해 주세요."


# ---------------------------------------------------------------------------
# (g) 조사 보정 단위 테스트 (자세한 케이스는 tests/test_explain.py)
# ---------------------------------------------------------------------------


def test_slotfill_fill_josa_correction_smoke():
    assert slotfill.fill("{total_a}로 정리했어요.", {"total_a": "71,703원"}) == "71,703원으로 정리했어요."
    assert slotfill.fill("{rate_a}은 낮아요.", {"rate_a": "5.47%"}) == "5.47%는 낮아요."
    assert slotfill.fill("{term_months}이 남았어요.", {"term_months": "36개월"}) == "36개월이 남았어요."
