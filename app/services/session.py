"""브라우저별(sid) 세션의 현재 프로필 저장/조회 (SPEC 2.3).

Render 무료 플랜에 공개 배포되면서 접속자가 여러 명일 수 있으므로, "현재 세션"의
의미를 프로세스 전역이 아니라 브라우저(쿠키) 단위로 좁힌다. `app.main`의 미들웨어가
요청마다 `donn_sid` 쿠키 값을 읽어(없으면 새로 발급) `bind_sid`로 contextvar에 설정하고
응답 후 `unbind_sid`로 되돌린다. 이 모듈의 함수들은 그 contextvar 값을 `db.session`
테이블 키 접두사로 써서 `current_profile:{sid}` 행 하나로 "그 브라우저의 활성 프로필"을
관리한다(옛 단일 키 "current_profile"은 더는 읽지 않는다 - 무시됨).

미들웨어가 sid를 설정하지 않은 경로(테스트, `scripts/run_personas.py`처럼 서비스
함수를 직접 호출하는 스크립트)에서는 `current_sid()`가 "local"을 돌려준다. 이는 기존
PoC의 단일 데모 세션과 동일하게 동작해 하위 호환된다.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

from app.data import db
from app.models import UserProfile

_DEFAULT_SID = "local"
_sid_var: ContextVar[str] = ContextVar("donn_sid", default=_DEFAULT_SID)


def current_sid() -> str:
    """현재 요청의 브라우저 세션 id. 미들웨어가 설정하지 않았으면 "local"."""
    return _sid_var.get()


def bind_sid(value: str) -> Token:
    """`app.main` 미들웨어 전용: 요청 시작 시 sid를 contextvar에 설정하고, 요청이 끝난
    뒤 `unbind_sid`에 넘길 토큰을 돌려준다."""
    return _sid_var.set(value or _DEFAULT_SID)


def unbind_sid(token: Token) -> None:
    """`app.main` 미들웨어 전용: 요청 처리가 끝나면(성공/예외 무관) contextvar를 이전
    값으로 되돌린다."""
    _sid_var.reset(token)


def _session_key(sid: Optional[str] = None) -> str:
    return f"current_profile:{sid if sid is not None else current_sid()}"


def get_profile() -> Optional[UserProfile]:
    """현재 세션(sid)에 로드된 프로필. 없으면 None."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM session WHERE key = ?", (_session_key(),)
        ).fetchone()
        if not row or not row["value"]:
            return None
        return UserProfile.model_validate_json(row["value"])
    finally:
        conn.close()


def set_profile(profile: UserProfile) -> None:
    """프로필 전체를 현재 세션(sid) 프로필로 저장한다(페르소나 로드 또는 수기 입력 저장)."""
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO session(key, value) VALUES (?, ?)",
            (_session_key(), profile.model_dump_json()),
        )
        conn.commit()
    finally:
        conn.close()


def clear_profile() -> None:
    """현재 세션(sid) 프로필을 지운다(DELETE /api/session)."""
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM session WHERE key = ?", (_session_key(),))
        conn.commit()
    finally:
        conn.close()
