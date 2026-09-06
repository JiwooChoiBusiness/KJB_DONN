"""POST /api/chat/attach 테스트 (SPEC 2.12): 대화창 파일 첨부 -> 소비 패턴 요약 답변.

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB를 재사용한다(패턴은
tests/test_chat_stream.py, tests/test_crisis_followup.py와 같다). 이 경로는 LLM을
전혀 호출하지 않으므로(SPEC 2.12) `_llm_provider`를 monkeypatch할 필요가 없다.
"""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient

from app.data import synthetic
from app.main import app
from tests.test_api import client

EM_DASH = "—"


def _tx(id_: str, amount: int, merchant: str, kind: str = "card") -> dict:
    return {"id": id_, "date": date.today().isoformat(), "amount": amount, "merchant": merchant, "kind": kind}


def test_chat_attach_p3_roundtrip_markdown_resources_log_and_spending():
    assert client.post("/api/session/persona/P3").status_code == 200
    try:
        transactions = synthetic.generate_transactions("P3", months=3, seed=42, end=date.today())
        # 대출상환 항목의 "가맹점"은 합성 데이터에서 loan.name을 그대로 쓴다(app/data/synthetic.py).
        # P3-L2의 loan.name이 우연히 일반 대출종류 라벨("카드론")과 같은 문자열이라, SPEC 2.15
        # 답변("이렇게 연결돼요")이 정당하게 쓰는 대출종류 라벨(app/services/spending.py의
        # _LOAN_TYPE_LABELS_KR, 실명이 아닌 일반 명사)까지 "가맹점명 유출"로 오탐하지 않도록
        # 대출 표시명은 이 검사에서 제외한다(가맹점 자체는 아니다).
        loan_names = {loan.name for loan in synthetic.get_persona("P3").loans}
        merchants = {t.merchant for t in transactions} - loan_names
        payload = {
            "filename": "P3_거래내역.csv",
            "months": 3,
            "transactions": [t.model_dump(mode="json") for t in transactions],
        }
        r = client.post("/api/chat/attach", json=payload)
        assert r.status_code == 200
        body = r.json()

        # 답변 형식과 본문 내용
        assert body["answer_format"] == "markdown"
        assert "월평균 지출" in body["reply_text"]
        assert "**핵심**" in body["reply_text"]
        assert "원본 거래내역은 저장하지 않고 요약만 남겨요." in body["reply_text"]
        assert EM_DASH not in body["reply_text"]
        assert "추천" not in body["reply_text"]
        # 가맹점 이름은 쓰지 않고 카테고리 라벨만 쓴다(SPEC 2.12).
        for merchant in merchants:
            assert merchant not in body["reply_text"]

        # 리소스: calc(소비 패턴 분석) + profile(내 정보/대출)
        kinds = {res["kind"] for res in body["resources"]}
        assert "calc" in kinds
        assert "profile" in kinds

        # 칩: 소비 패턴 보기(항상 있음) + 시나리오로 확인하기(SPEC 2.15, 절감 연결이 있으면)
        chip_intents = [c["intent"] for c in body["chips"]]
        assert "spending" in chip_intents
        assert set(chip_intents) <= {"spending", "scenario"}

        # 액션: 소비 패턴 화면으로 이동
        assert body["action"] == {"type": "open_view", "payload": {"view": "spending"}}
        assert body["llm_used"] is False
        assert body["model"] is None
        assert body["route"] == "internal"

        # trace 3개, 전부 done
        assert len(body["trace"]) == 3
        assert all(t["status"] == "done" for t in body["trace"])

        chat_id = body["chat_id"]
        assert chat_id

        # 대화 로그: 사용자 메시지 "파일 첨부: ..." + 응답(trace 3개 저장)
        msgs = client.get(f"/api/chats/{chat_id}/messages").json()
        user_msg = next(m for m in msgs if m["role"] == "user")
        reply_msg = next(m for m in msgs if m["role"] == "reply")
        assert user_msg["text"].startswith("파일 첨부:")
        assert f"({len(transactions)}행)" in user_msg["text"]
        assert len(reply_msg["trace"]) == 3
        assert reply_msg["answer_format"] == "markdown"
        assert reply_msg["route"] == "internal"

        # 저장된 소비 패턴 분석이 남는다(요약·피처만, 원본 미저장)
        r_spending = client.get("/api/spending")
        assert r_spending.status_code == 200
        assert r_spending.json()["summary"]["profile_id"] == "P3"

        client.delete(f"/api/chats/{chat_id}")
    finally:
        client.delete("/api/spending")
        client.delete("/api/session")


def test_chat_attach_without_profile_uses_guest():
    assert client.delete("/api/session").status_code == 200
    try:
        payload = {"filename": "guest.csv", "months": 1, "transactions": [_tx("g1", 8_000, "GS25")]}
        r = client.post("/api/chat/attach", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert body["answer_format"] == "markdown"

        r_spending = client.get("/api/spending")
        assert r_spending.status_code == 200
        assert r_spending.json()["summary"]["profile_id"] == "guest"

        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/spending")
        client.delete("/api/session")


def test_chat_attach_masks_pii_in_filename():
    assert client.post("/api/session/persona/P1").status_code == 200
    try:
        payload = {
            "filename": "010-2222-3333_거래내역.csv",
            "months": 1,
            "transactions": [_tx("m1", 10_000, "GS25")],
        }
        r = client.post("/api/chat/attach", json=payload)
        assert r.status_code == 200
        chat_id = r.json()["chat_id"]

        msgs = client.get(f"/api/chats/{chat_id}/messages").json()
        user_msg = next(m for m in msgs if m["role"] == "user")
        assert "010-2222-3333" not in user_msg["text"]
        assert "[전화번호]" in user_msg["text"]

        client.delete(f"/api/chats/{chat_id}")
    finally:
        client.delete("/api/spending")
        client.delete("/api/session")


def test_chat_attach_row_limit_exceeded_returns_422():
    rows = [_tx(f"r{i}", 1_000, "GS25") for i in range(10_001)]
    r = client.post("/api/chat/attach", json={"filename": "big.csv", "transactions": rows})
    assert r.status_code == 422


def test_chat_attach_foreign_chat_id_creates_new_chat():
    """다른 브라우저 세션(별도 TestClient, 별도 donn_sid)의 chat_id를 주면 그 대화를 잇는
    대신 새 대화를 만든다(대화는 세션에 종속된다, SPEC 2.3/2.12)."""
    client_a = TestClient(app)
    client_b = TestClient(app)
    chat_id_a: str | None = None
    chat_id_b: str | None = None
    try:
        payload = {"filename": "내역.csv", "months": 1, "transactions": [_tx("f1", 5_000, "스타벅스")]}
        r_a = client_a.post("/api/chat/attach", json=payload)
        assert r_a.status_code == 200
        chat_id_a = r_a.json()["chat_id"]

        r_b = client_b.post("/api/chat/attach", json={**payload, "chat_id": chat_id_a})
        assert r_b.status_code == 200
        chat_id_b = r_b.json()["chat_id"]

        assert chat_id_b != chat_id_a
        assert client_b.get(f"/api/chats/{chat_id_a}/messages").status_code == 404
    finally:
        if chat_id_a:
            client_a.delete(f"/api/chats/{chat_id_a}")
        if chat_id_b:
            client_b.delete(f"/api/chats/{chat_id_b}")
        client_a.delete("/api/spending")
        client_a.delete("/api/session")
        client_b.delete("/api/spending")
        client_b.delete("/api/session")
