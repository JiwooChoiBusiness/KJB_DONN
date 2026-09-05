"""API 요청/응답 스키마 (SPEC 2.5). 나머지 응답은 app.models의 타입을 그대로 쓴다."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel


class ComparePrepareRequest(BaseModel):
    intent: str = "compare"
    params: dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str


class ReplayResponse(BaseModel):
    match: bool
    result_hash: str
    replay_hash: str


class PersonaSummary(BaseModel):
    id: str
    display_name: str
    one_liner: str
    loans_count: int
    total_balance: int


class MetaResponse(BaseModel):
    """SPEC에는 없지만 화면의 신용구간/카테고리/상환방식 등 라벨 값을 드러내기 위한
    보조 엔드포인트(GET /api/meta)의 응답 모양."""

    credit_bands: list[str]
    categories: list[str]
    repay_methods: list[str]
    sort_keys: list[str]
    lender_groups: list[str]


class OkResponse(BaseModel):
    ok: bool = True
