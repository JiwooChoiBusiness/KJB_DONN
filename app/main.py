"""DONN PoC FastAPI 앱 진입점 (SPEC 2.5).

`run.py`가 `app.main:app`을 uvicorn으로 띄운다. 정적 파일은 `web/`을 그대로
서빙하고(`/static`), `GET /`은 `web/index.html`을 돌려준다.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.data.db import init_db

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    conn = init_db()
    conn.close()
    yield


app = FastAPI(title="DONN PoC", lifespan=lifespan)

app.include_router(api_router, prefix="/api")

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(str(WEB_DIR / "index.html"))
