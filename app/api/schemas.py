"""API 요청/응답 스키마 (SPEC 2.5). 나머지 응답은 app.models의 타입을 그대로 쓴다."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models import Transaction


class ComparePrepareRequest(BaseModel):
    intent: str = "compare"
    params: dict[str, Any] = {}


class ChatRequest(BaseModel):
    # SEV2 2026-09-06 리뷰: 길이 상한이 없으면 아주 긴 발화가 그대로 LLM 페이로드와 KB
    # 검색까지 흘러가 비용·성능 문제를 만들 수 있다. 초과분은 422로 거절한다.
    message: str = Field(max_length=2000)
    chat_id: Optional[str] = None


class ChatCreateRequest(BaseModel):
    title: Optional[str] = None


class ChatAttachRequest(BaseModel):
    """POST /api/chat/attach 본문(SPEC 2.12). 브라우저가 소비 패턴 화면과 같은 파서·열
    자동 매핑으로 정규화한 거래 행만 받는다(원본 파일은 서버로 오지 않는다). chat_id가
    없거나 현재 프로필 소유가 아니면 새 대화를 만든다. 행 수 상한은 /api/spending/analyze에
    아직 명시적 상한이 없어 이 요청 전용으로 10,000행을 둔다(초과 시 422)."""

    chat_id: Optional[str] = None
    filename: str = Field(max_length=120)
    months: Optional[int] = 3
    transactions: list[Transaction] = Field(max_length=10_000)


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


class ExplainRequest(BaseModel):
    """POST /api/compare/{decision_id}/explain, POST /api/actions/{action_id}/explain 본문(선택,
    SPEC 2.8). refresh=true면 저장된 설명이 있어도 새로 만들어 덮어쓴다."""

    refresh: bool = False


class SpendingAnalyzeRequest(BaseModel):
    """POST /api/spending/analyze 본문. 브라우저가 파싱·정규화한 거래내역만 받는다
    (SPEC 2.6 D3/D4: 서버는 이 요청 처리 중에만 메모리에서 계산하고 원본을 저장하지 않는다)."""

    transactions: list[Transaction]
    months: Optional[int] = 3


class SpendingAnalyzeSyntheticRequest(BaseModel):
    """POST /api/spending/analyze-synthetic 본문. persona_id를 생략하면 현재 세션
    프로필의 id(페르소나로 로드된 경우)를 쓴다."""

    persona_id: Optional[str] = None
    months: Optional[int] = 3
    seed: Optional[int] = 42
