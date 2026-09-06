"""제도 안내 답변이 질문에 맞는 문단을 먼저 찾아 답하는지(app/kb/focus.py, SPEC 2.11 보강)."""
from __future__ import annotations

from app import kb
from app.kb import focus
from app.llm.provider import LLMResult
from tests.test_api import _FakeUnavailableProvider, client


def _doc(slug: str):
    return next(d for d in kb.load_docs() if d.slug == slug)


def _rule_mode(monkeypatch):
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())


def test_focus_finds_repayment_period_sentence():
    doc = _doc("court-rehabilitation")
    best = focus.best_answer_sentence(doc, "개인회생의 변제 기간은?")
    assert best is not None
    section, text = best
    assert section == "절차"
    assert "3년" in text and "5년" in text


def test_focus_synonym_question_finds_same_chunk():
    doc = _doc("court-rehabilitation")
    best = focus.best_answer_sentence(doc, "개인회생 얼마나 오래 갚아야 해?")
    assert best is not None and "3년" in best[1]


def test_focus_requirements_question_prefers_requirements_section():
    doc = _doc("court-rehabilitation")
    best = focus.best_answer_sentence(doc, "개인회생 신청 요건이 뭐야")
    assert best is not None and best[0] == "대상과 요건"


def test_focus_keyword_only_question_has_no_direct_answer():
    doc = _doc("court-rehabilitation")
    assert focus.best_answer_sentence(doc, "개인회생 알려줘") is None


def test_chat_repayment_period_answer_starts_with_direct_line(monkeypatch):
    _rule_mode(monkeypatch)
    r = client.post("/api/chat", json={"message": "개인회생의 변제 기간은?"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"] == "internal"
    assert body["reply_text"].startswith("**바로 답하면**")
    assert "3년" in body["reply_text"] and "5년" in body["reply_text"]
    assert any("가장 가까운 문단" in step for stage in body["trace"] for step in (stage.get("steps") or []))
    client.delete(f"/api/chats/{body['chat_id']}")


def test_chat_keyword_only_question_keeps_overview_format(monkeypatch):
    _rule_mode(monkeypatch)
    r = client.post("/api/chat", json={"message": "개인회생 알려줘"})
    body = r.json()
    assert body["route"] == "internal"
    assert not body["reply_text"].startswith("**바로 답하면**")
    assert "**핵심**" in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")


class _FakeKbProvider:
    """문단에 없는 숫자를 쓰는 LLM 흉내: 규칙 경로로 떨어져야 한다."""

    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        return LLMResult(data={"intent": "faq"}, text="{}", model="fake", key_index=0, latency_ms=1)

    def explain(self, slots, template_id, system, **kwargs):  # noqa: ANN001
        assert "question" in slots
        data = {"summary": "개인회생 변제 기간은 7년이에요. 법원이 정해요.",
                "points": ["절차: 7년 동안 갚아요"] * len([s for s in slots["sections"].split("[") if s.strip()])}
        return LLMResult(data=data, text="", model="fake", key_index=0, latency_ms=1)


def test_llm_answer_with_ungrounded_number_falls_back_to_focus_rule(monkeypatch):
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeKbProvider())
    r = client.post("/api/chat", json={"message": "개인회생의 변제 기간은?"})
    body = r.json()
    assert "7년" not in body["reply_text"]
    assert body["reply_text"].startswith("**바로 답하면**") and "3년" in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")
