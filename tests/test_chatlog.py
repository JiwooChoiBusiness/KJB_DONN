"""페르소나별 대화 로그: 세션 프로필에 종속되고, 프로필이 바뀌면 다른 프로필의 대화는 보이지 않는다."""
from tests.test_api import _FakeUnavailableProvider, client


def test_chat_log_is_scoped_to_persona(monkeypatch):
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    assert client.post("/api/session/persona/P1").status_code == 200
    r = client.post("/api/chat", json={"message": "상환표 보여줘"})
    assert r.status_code == 200
    chat_id = r.json()["chat_id"]
    assert chat_id
    chats = client.get("/api/chats").json()
    assert any(c["id"] == chat_id for c in chats)
    msgs = client.get(f"/api/chats/{chat_id}/messages").json()
    assert [m["role"] for m in msgs] == ["user", "reply"]

    # 같은 대화에 이어서 보내면 같은 chat_id, 메시지 4개
    r2 = client.post("/api/chat", json={"message": "시나리오도 보여줘", "chat_id": chat_id})
    assert r2.json()["chat_id"] == chat_id
    assert len(client.get(f"/api/chats/{chat_id}/messages").json()) == 4

    # 다른 페르소나로 바꾸면 이전 대화는 보이지 않고, 다른 프로필의 chat_id는 404
    assert client.post("/api/session/persona/P2").status_code == 200
    assert all(c["id"] != chat_id for c in client.get("/api/chats").json())
    assert client.get(f"/api/chats/{chat_id}/messages").status_code == 404

    # 새 대화 만들기와 삭제
    assert client.post("/api/session/persona/P1").status_code == 200
    created = client.post("/api/chats", json={"title": "테스트 대화"}).json()
    assert created["profile_id"] == "P1" and created["message_count"] == 0
    assert client.delete(f"/api/chats/{chat_id}").json()["ok"] is True
    assert client.delete(f"/api/chats/{created['id']}").json()["ok"] is True
    client.delete("/api/session")


def test_new_chat_title_masks_pii_from_first_message(monkeypatch):
    """SEV5 #1: 자동 생성되는 대화 제목은 원문 발화가 아니라 mask_pii를 거친 문자열의
    앞 30자여야 한다(연락처 등 개인정보가 대화 목록 제목에 그대로 남으면 안 된다)."""
    from app.api import routes as routes_module

    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    assert client.post("/api/session/persona/P2").status_code == 200
    r = client.post("/api/chat", json={"message": "010-1234-5678로 연락주세요 상환표 보여줘"})
    assert r.status_code == 200
    chat_id = r.json()["chat_id"]

    chats = client.get("/api/chats").json()
    chat = next(c for c in chats if c["id"] == chat_id)
    assert "010-1234-5678" not in chat["title"]
    assert chat["title"].startswith("[전화번호]")
    assert len(chat["title"]) <= 30

    client.delete(f"/api/chats/{chat_id}")
    client.delete("/api/session")
