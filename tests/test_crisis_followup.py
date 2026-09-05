"""위기 발화 응답 분기와 후속 질의 조건 기억(같은 대화의 직전 비교 조건 위에 델타만 적용)."""
from tests.test_api import _FakeUnavailableProvider, client


def _rule_mode(monkeypatch):
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())


def test_financial_crisis_reply_points_to_public_counseling(monkeypatch):
    _rule_mode(monkeypatch)
    assert client.post("/api/session/persona/P5").status_code == 200
    r = client.post("/api/chat", json={"message": "연체돼서 독촉 전화가 계속 와요. 너무 힘들어요"})
    assert r.status_code == 200
    body = r.json()
    assert "1600-5500" in body["reply_text"]
    assert "1397" in body["reply_text"]
    assert body["llm_used"] is False
    assert (body.get("action") or {}).get("type") != "prepare_compare"
    assert any(c["intent"] == "faq" and c["params"].get("slug") == "ccrs-debt-adjustment" for c in body["chips"])
    assert "추천" not in body["reply_text"]
    client.delete(f"/api/chats/{body['chat_id']}")
    client.delete("/api/session")


def test_self_harm_signal_reply_gives_hotline_first(monkeypatch):
    _rule_mode(monkeypatch)
    r = client.post("/api/chat", json={"message": "빚 때문에 죽고 싶어요"})
    body = r.json()
    assert "109" in body["reply_text"]
    assert body["action"] is None
    assert body["llm_used"] is False
    client.delete(f"/api/chats/{body['chat_id']}")


def test_followup_keeps_previous_compare_conditions(monkeypatch):
    _rule_mode(monkeypatch)
    assert client.post("/api/session/persona/P1").status_code == 200

    r1 = client.post("/api/chat", json={"message": "2천만원 신용대출 36개월 비교해줘"}).json()
    chat_id = r1["chat_id"]
    p1 = r1["action"]["payload"]["params"]
    assert p1["amount"] == 20_000_000 and p1["term_months"] == 36

    # 조건만 말해도 같은 대화면 후속 질의: 금액·기간 유지, 금리 상한만 추가
    r2 = client.post("/api/chat", json={"message": "금리 5% 이하만", "chat_id": chat_id}).json()
    p2 = r2["action"]["payload"]["params"]
    assert p2["amount"] == 20_000_000 and p2["term_months"] == 36 and p2["max_rate"] == 5.0
    assert "금리 상한" in r2["reply_text"]

    # 기간만 바꾸면 금리 상한은 남는다
    r3 = client.post("/api/chat", json={"message": "기간은 24개월로", "chat_id": chat_id}).json()
    p3 = r3["action"]["payload"]["params"]
    assert p3["term_months"] == 24 and p3["max_rate"] == 5.0 and p3["amount"] == 20_000_000
    assert p3["user_confirmed"] is False

    # 다른 카테고리를 말하면 새 비교로 시작한다(이전 금리 상한을 끌고 가지 않음)
    r4 = client.post("/api/chat", json={"message": "주택담보대출 비교해줘", "chat_id": chat_id}).json()
    p4 = r4["action"]["payload"]["params"]
    assert p4["category"] == "mortgage" and p4.get("max_rate") is None

    client.delete(f"/api/chats/{chat_id}")
    client.delete("/api/session")
