"""브라우저별 세션 분리 + 호출 횟수 제한 테스트 (SPEC 2.3, app.main 미들웨어).

Render 무료 플랜에 공개 배포되면서 접속자가 여러 명일 수 있다. `TestClient(app)`
인스턴스를 여러 개 만들면 각자 독립된 쿠키 저장소를 가지므로, 첫 요청에서 서로 다른
`donn_sid`가 발급되어 별개의 브라우저를 흉내 낼 수 있다. 이 파일은 그렇게 만든
클라이언트들이 프로필/대화/결정 기록/소비 분석을 서로 보지 못하는지, 쿠키가 한 번만
발급되는지, sid별 호출 횟수 제한이 걸리는지 확인한다.

tests/test_api.py가 이미 DONN_DB_PATH를 임시 파일로, DONN_RATE_LIMIT_PER_5MIN /
DONN_RATE_LIMIT_GLOBAL_PER_5MIN을 "0"(무제한)으로 맞춰뒀으므로(app.main을 최초
임포트하기 전에 설정해야 한다), 이 파일도 그 설정 위에서 동작하도록 test_api를 먼저
임포트한다.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api import _FakeUnavailableProvider

from app.api import routes as routes_module
from app.main import app


def test_browser_session_isolation(monkeypatch):
    """서로 다른 브라우저(sid)는 프로필·대화·결정 기록·소비 분석을 공유하지 않는다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())

    client_a = TestClient(app)
    client_b = TestClient(app)

    try:
        # (a) A가 P1을 선택해도 B는 아직 프로필이 없다(독립된 브라우저 세션).
        assert client_a.post("/api/session/persona/P1").status_code == 200
        assert client_b.get("/api/profile").status_code == 404

        # 이후 검사는 "같은 페르소나를 고른 서로 다른 브라우저"로 확인한다(배경:
        # 여러 접속자가 같은 데모 페르소나를 고를 수 있고, 이때도 섞이면 안 된다).
        assert client_b.post("/api/session/persona/P1").status_code == 200
        assert client_a.get("/api/profile").json()["id"] == "P1"
        assert client_b.get("/api/profile").json()["id"] == "P1"

        # (b) A의 대화가 B의 /api/chats에 없고, B가 A의 chat_id로 메시지를 요청하면 404.
        r = client_a.post("/api/chat", json={"message": "상환표 보여줘"})
        assert r.status_code == 200
        chat_id = r.json()["chat_id"]
        assert chat_id

        assert all(c["id"] != chat_id for c in client_b.get("/api/chats").json())
        assert client_b.get(f"/api/chats/{chat_id}/messages").status_code == 404
        assert client_b.delete(f"/api/chats/{chat_id}").status_code == 404
        # A 자신은 정상적으로 볼 수 있다(회귀 방지).
        a_msgs = client_a.get(f"/api/chats/{chat_id}/messages").json()
        assert [m["role"] for m in a_msgs] == ["user", "reply"]

        # (c) A의 비교 결정이 B의 /api/decisions에 없고, B의 조회/재현은 404.
        ctx = client_a.post("/api/compare/prepare", json={"intent": "compare", "params": {}}).json()
        ctx["user_confirmed"] = True
        result = client_a.post("/api/compare/run", json=ctx)
        assert result.status_code == 200
        decision_id = result.json()["decision_id"]

        assert all(d["decision_id"] != decision_id for d in client_b.get("/api/decisions").json())
        assert client_b.get(f"/api/decisions/{decision_id}").status_code == 404
        assert client_b.post(f"/api/decisions/{decision_id}/replay").status_code == 404
        # A 자신의 재현은 여전히 일치한다(회귀 방지).
        replay = client_a.post(f"/api/decisions/{decision_id}/replay")
        assert replay.status_code == 200
        assert replay.json()["match"] is True

        # (d) 소비 분석 저장이 서로 안 보인다(같은 페르소나로 분석해도 섞이지 않는다).
        assert client_a.post("/api/spending/analyze-synthetic", json={}).status_code == 200
        assert client_b.get("/api/spending").status_code == 404
        assert client_b.post("/api/spending/analyze-synthetic", json={}).status_code == 200
        assert client_a.get("/api/spending").status_code == 200
        assert client_b.get("/api/spending").status_code == 200
        assert client_a.delete("/api/spending").json()["ok"] is True
        assert client_b.get("/api/spending").status_code == 200  # A가 지워도 B는 그대로

        # (e) 첫 응답에 Set-Cookie: donn_sid가 있고, 두 번째 요청부터는 새로 발급되지 않는다.
        # (/api/meta는 세션·LLM 제공자에 의존하지 않는 정적 엔드포인트라 이 확인에 적합하다.)
        fresh = TestClient(app)
        r1 = fresh.get("/api/meta")
        assert "donn_sid" in r1.headers.get("set-cookie", "")
        r2 = fresh.get("/api/meta")
        assert r2.headers.get("set-cookie") is None
    finally:
        for c in (client_a, client_b):
            c.delete("/api/spending")
            c.delete("/api/session")


def test_chat_rate_limit_is_per_sid(monkeypatch):
    """DONN_RATE_LIMIT_PER_5MIN을 낮게 걸면 그 sid만 초과 시 429이고, 다른 sid는 영향받지 않는다."""
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    monkeypatch.setenv("DONN_RATE_LIMIT_PER_5MIN", "3")

    limited_client = TestClient(app)
    other_client = TestClient(app)

    try:
        for _ in range(3):
            r = limited_client.post("/api/chat", json={"message": "상환표 보여줘"})
            assert r.status_code == 200

        r4 = limited_client.post("/api/chat", json={"message": "상환표 보여줘"})
        assert r4.status_code == 429
        assert r4.json() == {"detail": "요청이 너무 많아요. 잠시 후 다시 시도해 주세요."}

        r_other = other_client.post("/api/chat", json={"message": "상환표 보여줘"})
        assert r_other.status_code == 200
    finally:
        limited_client.delete("/api/session")
        other_client.delete("/api/session")
