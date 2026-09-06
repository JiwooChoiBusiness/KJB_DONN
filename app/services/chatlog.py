"""페르소나(프로필)별 대화 로그. PoC는 로그인이 없으므로 현재 세션 프로필 id에 종속된다.

저장 원칙: 사용자 발화는 PII 마스킹본을 저장한다(D4). 응답은 guardrails를 통과한 문장만 저장된다.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Optional

from app.data import db

GUEST_PROFILE_ID = "guest"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _row_to_chat(row: Any) -> dict[str, Any]:
    keys = row.keys()
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row["message_count"] if "message_count" in keys else 0,
    }


def list_chats(profile_id: str, limit: int = 30) -> list[dict[str, Any]]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS message_count "
            "FROM chats c WHERE c.profile_id = ? ORDER BY c.updated_at DESC, c.id DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        return [_row_to_chat(r) for r in rows]
    finally:
        conn.close()


def get_chat(chat_id: str) -> Optional[dict[str, Any]]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = c.id) AS message_count "
            "FROM chats c WHERE c.id = ?",
            (chat_id,),
        ).fetchone()
        return _row_to_chat(row) if row else None
    finally:
        conn.close()


def create_chat(profile_id: str, title: str = "") -> dict[str, Any]:
    chat_id = f"chat-{uuid.uuid4().hex[:12]}"
    now = _now()
    clean_title = (title or "새 대화").strip()[:40]
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO chats (id, profile_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, profile_id, clean_title, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return {"id": chat_id, "profile_id": profile_id, "title": clean_title,
            "created_at": now, "updated_at": now, "message_count": 0}


def append_message(chat_id: str, role: str, text: str, *, llm_used: bool = False,
                   action: Optional[dict[str, Any]] = None,
                   chips: Optional[list[dict[str, Any]]] = None,
                   trace: Optional[list[dict[str, Any]]] = None,
                   meta: Optional[dict[str, Any]] = None) -> int:
    """trace(SPEC 2.9 생각 과정, 종료 상태 stage 목록)는 생략하면 저장하지 않는다
    (get_messages가 빈 리스트로 채워 돌려준다). meta(SPEC 2.11: {"route", "resources",
    "answer_format", "model"})도 생략하면 저장하지 않는다(get_messages가 기본값으로
    채워 돌려준다). 사용자 메시지에는 meta를 넘기지 않는다(경로 개념이 없다)."""
    now = _now()
    conn = db.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO chat_messages "
            "(chat_id, role, text, llm_used, action_json, chips_json, trace_json, meta_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                chat_id, role, text, 1 if llm_used else 0,
                json.dumps(action, ensure_ascii=False) if action is not None else None,
                json.dumps(chips, ensure_ascii=False) if chips is not None else None,
                json.dumps(trace, ensure_ascii=False) if trace is not None else None,
                json.dumps(meta, ensure_ascii=False) if meta is not None else None,
                now,
            ),
        )
        conn.execute("UPDATE chats SET updated_at = ? WHERE id = ?", (now, chat_id))
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_messages(chat_id: str) -> list[dict[str, Any]]:
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, role, text, llm_used, action_json, chips_json, trace_json, meta_json, created_at "
            "FROM chat_messages WHERE chat_id = ? ORDER BY id",
            (chat_id,),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for r in rows:
        keys = r.keys()
        meta_raw = r["meta_json"] if "meta_json" in keys else None
        meta = json.loads(meta_raw) if meta_raw else {}
        out.append({
            "id": r["id"],
            "role": r["role"],
            "text": r["text"],
            "llm_used": bool(r["llm_used"]),
            "action": json.loads(r["action_json"]) if r["action_json"] else None,
            "chips": json.loads(r["chips_json"]) if r["chips_json"] else [],
            "trace": json.loads(r["trace_json"]) if r["trace_json"] else [],
            # SPEC 2.11: 응답 메시지(role="reply")에만 의미가 있다. 사용자 메시지나
            # meta 저장 전 옛 행은 기본값(route None, resources 빈 리스트, text 포맷,
            # model None)으로 채운다.
            "route": meta.get("route"),
            "resources": meta.get("resources") or [],
            "answer_format": meta.get("answer_format") or "text",
            "model": meta.get("model"),
            "created_at": r["created_at"],
        })
    return out


def delete_chat(chat_id: str) -> bool:
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM chat_messages WHERE chat_id = ?", (chat_id,))
        cur = conn.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
