"""단일 데모 세션의 현재 프로필 저장/조회 (SPEC 2.3).

`db.session` 테이블(key TEXT PRIMARY KEY, value TEXT)의 key="current_profile"
행 하나로 "현재 활성 프로필"을 관리한다. PoC는 다중 사용자를 지원하지 않는
단일 데모 세션이므로 이것으로 충분하다.
"""
from __future__ import annotations

from typing import Optional

from app.data import db
from app.models import UserProfile

_SESSION_KEY = "current_profile"


def get_profile() -> Optional[UserProfile]:
    """현재 세션에 로드된 프로필. 없으면 None."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM session WHERE key = ?", (_SESSION_KEY,)
        ).fetchone()
        if not row or not row["value"]:
            return None
        return UserProfile.model_validate_json(row["value"])
    finally:
        conn.close()


def set_profile(profile: UserProfile) -> None:
    """프로필 전체를 현재 세션 프로필로 저장한다(페르소나 로드 또는 수기 입력 저장)."""
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO session(key, value) VALUES (?, ?)",
            (_SESSION_KEY, profile.model_dump_json()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_profile() -> None:
    """현재 세션 프로필을 지운다(DELETE /api/session)."""
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM session WHERE key = ?", (_SESSION_KEY,))
        conn.commit()
    finally:
        conn.close()
