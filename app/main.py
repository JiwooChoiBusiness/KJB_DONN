"""DONN PoC FastAPI 앱 진입점 (SPEC 2.5, 2.10).

`run.py`가 `app.main:app`을 uvicorn으로 띄운다. 정적 파일은 `web/`을 그대로
서빙하고(`/static`), `GET /`은 `web/index.html`을 돌려준다.

브라우저 전용 모드(정적 배포, SPEC 2.10) 지원: 시작 시 상품 테이블이 비어 있으면
`seed/products_seed.json.gz`를 적재하고(`DONN_SEED_ON_EMPTY`), 원하면 실 데이터를
백그라운드로 추가 적재하며(`DONN_AUTOLOAD_PRODUCTS`), 정적 페이지 배포용 CORS를
켤 수 있다(`DONN_CORS_ORIGINS`). 이 환경변수들은 전부 기본값이 기존 동작(서버 모드,
CORS 없음)과 같아 기존 배포·테스트에는 영향이 없다.
"""
from __future__ import annotations

import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.data.db import init_db
from app.data.products import ensure_seed_loaded, load_snapshot

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
