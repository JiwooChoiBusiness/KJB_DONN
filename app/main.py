"""DONN PoC FastAPI 앱 진입점 (SPEC 2.5, 2.10).

`run.py`가 `app.main:app`을 uvicorn으로 띄운다. 정적 파일은 `web/`을 그대로
서빙하고(`/static`), `GET /`은 `web/index.html`을 돌려준다.

브라우저 전용 모드(정적 배포, SPEC 2.10) 지원: 시작 시 상품 테이블이 비어 있으면
`seed/products_seed.json.gz`를 적재하고(`DONN_SEED_ON_EMPTY`), 원하면 실 데이터를
백그라운드로 추가 적재하며(`DONN_AUTOLOAD_PRODUCTS`), 정적 페이지 배포용 CORS를
켤 수 있다(`DONN_CORS_ORIGINS`). 이 환경변수들은 전부 기본값이 기존 동작(서버 모드,
CORS 없음)과 같아 기존 배포·테스트에는 영향이 없다.

브라우저별 세션 분리 + 호출 횟수 제한: Render 무료 플랜에 공개 배포되면서 접속자가
여러 명일 수 있으므로(`docs/DEPLOY.md` 참고), 요청마다 `donn_sid` 쿠키를 발급/유지해
`app.services.session`의 프로필·대화·결정 기록·소비 분석 저장을 브라우저 단위로
분리한다. 같은 미들웨어에서 Gemini 무료 티어를 보호하기 위해 LLM을 호출하는 경로에
sid별 + 전체 합산 슬라이딩 윈도(5분) 호출 횟수 제한도 적용한다.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.data.db import init_db
from app.data.products import ensure_seed_loaded, load_snapshot
from app.services import session as session_service

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

logger = logging.getLogger("donn.main")




def _env_flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in ("", "0", "false", "no")


def _autoload_products_in_background() -> None:
    """DONN_AUTOLOAD_PRODUCTS=1이고 금감원·공공데이터 키가 있을 때만 호출된다.

    데몬 스레드에서 실행되므로 여기서 나는 예외는 로그만 남기고 앱 구동(첫 화면 응답)에는
    영향을 주지 않는다(SPEC 2.10: "예외는 로그만"). finlife -> datago 순서로 시도한다.
    """
    if os.environ.get("FINLIFE_AUTH_KEY"):
        try:
            load_snapshot(["finlife"], ["020000", "030300"])
            logger.info("자동 적재: finlife 완료")
        except Exception:
            logger.exception("자동 적재: finlife 실패")
    if os.environ.get("DATA_GO_KR_SERVICE_KEY"):
        try:
            load_snapshot(["datago"], [])
            logger.info("자동 적재: datago 완료")
        except Exception:
            logger.exception("자동 적재: datago 실패")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    conn = init_db()
    conn.close()

    if _env_flag("DONN_SEED_ON_EMPTY", "1"):
        loaded = ensure_seed_loaded()
        logger.info("시드 적재 %s", "완료" if loaded else "건너뜀(상품 있음 또는 시드 파일 없음)")

    if _env_flag("DONN_AUTOLOAD_PRODUCTS", "0") and (
        os.environ.get("FINLIFE_AUTH_KEY") or os.environ.get("DATA_GO_KR_SERVICE_KEY")
    ):
        threading.Thread(target=_autoload_products_in_background, daemon=True).start()

    yield


app = FastAPI(title="DONN PoC", lifespan=lifespan)


@app.middleware("http")
async def no_cache_static(request, call_next):
    """정적 파일과 첫 페이지는 캐시하지 않는다(데모 중 수정 즉시 반영)."""
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


SID_COOKIE_NAME = "donn_sid"
SID_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30일

# LLM(Gemini)을 호출하는 경로만 호출 횟수 제한 대상이다. {decision_id}/{action_id}는
# 슬래시가 없는 임의 문자열이라 [^/]+로 매칭한다.
_RATE_LIMITED_PATTERNS = [re.compile(p) for p in (
    r"^/api/chat$",
    r"^/api/chat/stream$",
    r"^/api/compare/[^/]+/explain$",
    r"^/api/actions/[^/]+/explain$",
)]
_RATE_LIMIT_WINDOW_SECONDS = 300  # 5분

_rate_limit_lock = threading.Lock()
_rate_limit_hits_by_sid: dict[str, "deque[float]"] = {}
_rate_limit_hits_global: "deque[float]" = deque()


def _is_rate_limited_path(method: str, path: str) -> bool:
    if method != "POST":
        return False
    return any(p.match(path) for p in _RATE_LIMITED_PATTERNS)


def _prune_old_hits(hits: "deque[float]", now: float) -> None:
    while hits and now - hits[0] > _RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()


def _rate_limit_env(name: str, default: int) -> int:
    """요청마다 새로 읽는다(테스트가 monkeypatch로 즉시 반영되게 하기 위해)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _check_rate_limit(sid: str) -> bool:
    """허용되면 True. 값이 0이면 그 한도는 검사하지 않는다(무제한). 기본값은 세션당
    5분에 20회, 전체 합산 5분에 150회(Gemini 무료 티어 보호, docs/DEPLOY.md 참고)."""
    per_sid_limit = _rate_limit_env("DONN_RATE_LIMIT_PER_5MIN", 20)
    global_limit = _rate_limit_env("DONN_RATE_LIMIT_GLOBAL_PER_5MIN", 150)
    now = time.monotonic()
    with _rate_limit_lock:
        sid_hits = _rate_limit_hits_by_sid.setdefault(sid, deque())
        _prune_old_hits(sid_hits, now)
        _prune_old_hits(_rate_limit_hits_global, now)
        if per_sid_limit > 0 and len(sid_hits) >= per_sid_limit:
            return False
        if global_limit > 0 and len(_rate_limit_hits_global) >= global_limit:
            return False
        sid_hits.append(now)
        _rate_limit_hits_global.append(now)
        return True


@app.middleware("http")
async def session_and_rate_limit(request, call_next):
    """브라우저별 세션 쿠키(`donn_sid`)를 발급/유지하고, LLM을 호출하는 경로에 호출
    횟수 제한을 적용한다(SPEC 2.3).

    쿠키가 없으면 `secrets.token_urlsafe(24)`로 새로 발급해 요청을 처리하는 동안
    `app.services.session`의 contextvar에 묶는다(다른 서비스 함수들이
    `session.current_sid()`로 읽어 프로필·대화·결정 기록·소비 분석 저장을 분리한다).
    응답 후에는 항상 contextvar를 되돌린다. 정적 파일과 "/"에도 똑같이 적용해 첫 화면
    로드 때부터 쿠키가 생기게 한다.
    """
    sid = request.cookies.get(SID_COOKIE_NAME)
    is_new_sid = not sid
    if is_new_sid:
        sid = secrets.token_urlsafe(24)

    token = session_service.bind_sid(sid)
    try:
        if _is_rate_limited_path(request.method, request.url.path) and not _check_rate_limit(sid):
            response = JSONResponse(
                status_code=429,
                content={"detail": "요청이 너무 많아요. 잠시 후 다시 시도해 주세요."},
            )
        else:
            response = await call_next(request)
    finally:
        session_service.unbind_sid(token)

    if is_new_sid:
        is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(
            SID_COOKIE_NAME, sid, max_age=SID_MAX_AGE_SECONDS, httponly=True,
            samesite="lax", secure=is_https, path="/",
        )
    return response


_cors_origins = [o.strip() for o in os.environ.get("DONN_CORS_ORIGINS", "").split(",") if o.strip()]
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix="/api")

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))
