"""DONN PoC API 라우트 (SPEC 2.5). main.py가 prefix="/api"로 include한다."""
from __future__ import annotations

import json
import logging
import math
import queue
import contextvars
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from app.api.schemas import (ChatAttachRequest,
    ChatCreateRequest,
    ChatRequest,
    ComparePrepareRequest,
    ExplainRequest,
    MetaResponse,
    OkResponse,
    PersonaSummary,
    ReplayResponse,
    SpendingAnalyzeRequest,
    SpendingAnalyzeSyntheticRequest,
)
from app.core.capacity import compute_capacity
from app.core.schedule import build_schedule
from app.core.scenarios import run_scenarios
from app.core import lifecycle as lifecycle_core
from app.core import ratios as ratios_core
from app.core import retirement as retirement_core
from app.core import spending as spending_core
from app.data import policy, products, synthetic
from app.data.finlife import CRDT_GRADE_LABELS
from app.llm import guardrails, slotfill
from app.llm.gemini import GeminiProvider
from app.llm.provider import LLMUnavailable
from app.services import chatlog
from app import kb as kb_search
from app.models import (
    ENGINE_VERSION,
    ActionCard,
    ChatReply,
    ChatResource,
    Chip,
    CompareContext,
    CompareResult,
    DecisionRecord,
    ExplainResult,
    HomePayload,
    LenderGroup,
    LifecycleView,
    LoanSchedule,
    ProductCategory,
    RepayMethod,
    ScenarioResult,
    SortKey,
    UserProfile,
    WhatIfResult,
)
from app.services import actions as actions_service
from app.services import answer as answer_service
from app.services import compare as compare_service
from app.services import decisions as decisions_service
from app.services import explain as explain_service
from app.services import insights as insights_service
from app.services import lifecycle as lifecycle_service
from app.services import session as session_service
from app.services import spending as spending_service
from app.services import whatif as whatif_service

router = APIRouter()
logger = logging.getLogger(__name__)

# 모듈 임포트 시 한 번 만든다(SPEC: 키 값은 절대 로그/코드에 남기지 않고 .env에서만 읽는다).
# 테스트는 이 이름(app.api.routes._llm_provider)을 monkeypatch해서 LLM 경로를 통제한다.
_llm_provider = GeminiProvider.from_env()


# ---------------------------------------------------------------------------
# 상태/메타
# ---------------------------------------------------------------------------


@router.get("/health")
def get_health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "snapshot_id": products.latest_snapshot_id() or "",
        "llm_provider": "gemini",
        "llm_available": _llm_provider.available(),
        "model_chain": list(_llm_provider.model_chain),
    }


@router.get("/meta", response_model=MetaResponse)
def get_meta() -> MetaResponse:
    """SPEC 표에는 없지만 화면이 신용구간/카테고리 등 라벨 값을 알 수 있도록 추가한다."""
    return MetaResponse(
        credit_bands=list(dict.fromkeys(CRDT_GRADE_LABELS.values())),
        categories=[c.value for c in ProductCategory],
        repay_methods=[m.value for m in RepayMethod],
        sort_keys=[s.value for s in SortKey],
        lender_groups=[g.value for g in LenderGroup],
    )


@router.get("/products/stats")
def get_products_stats() -> dict[str, Any]:
    return products.stats()


# ---------------------------------------------------------------------------
# 페르소나 / 세션 / 프로필
# ---------------------------------------------------------------------------


@router.get("/personas", response_model=list[PersonaSummary])
def get_personas() -> list[PersonaSummary]:
    out: list[PersonaSummary] = []
    for p in synthetic.PERSONAS:
        one_liner = (p.notes or "").split(".")[0].strip() or p.display_name
        out.append(PersonaSummary(
            id=p.id,
            display_name=p.display_name,
            one_liner=one_liner,
            loans_count=len(p.loans),
            total_balance=sum(l.balance for l in p.loans),
        ))
    return out


@router.post("/session/persona/{persona_id}", response_model=UserProfile)
def post_load_persona(persona_id: str) -> UserProfile:
    try:
        persona = synthetic.get_persona(persona_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"알 수 없는 페르소나입니다: {persona_id}")
    session_service.set_profile(persona)
    return persona


@router.delete("/session", response_model=OkResponse)
def delete_session() -> OkResponse:
    session_service.clear_profile()
    return OkResponse(ok=True)


@router.get("/profile", response_model=UserProfile)
def get_profile() -> UserProfile:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    return profile


@router.put("/profile", response_model=UserProfile)
def put_profile(profile: UserProfile) -> UserProfile:
    session_service.set_profile(profile)
    return profile


# ---------------------------------------------------------------------------
# 홈 / 대출 / 시나리오 / 행동
# ---------------------------------------------------------------------------


@router.get("/home", response_model=HomePayload)
def get_home() -> HomePayload:
    profile = session_service.get_profile()
    stats = products.stats()
    params = policy.load_policy_params()
    return insights_service.build_home(profile, stats, params, today=date.today())


@router.get("/loans/{loan_id}/schedule", response_model=LoanSchedule)
def get_loan_schedule(loan_id: str, extra: int = Query(0, ge=0)) -> LoanSchedule:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    loan = next((l for l in profile.loans if l.id == loan_id), None)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"대출을 찾을 수 없습니다: {loan_id}")
    return build_schedule(loan, extra_payment=max(extra, 0))


@router.get("/scenarios", response_model=list[ScenarioResult])
def get_scenarios(horizon: int = Query(60, ge=1, le=360)) -> list[ScenarioResult]:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    return run_scenarios(profile, params, horizon_months=horizon, today=date.today(), goals=profile.goals)


@router.get("/actions", response_model=list[ActionCard])
def get_actions() -> list[ActionCard]:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    return actions_service.list_actions(profile, params, today=date.today())


@router.post("/actions/{action_id}/explain", response_model=ExplainResult)
def post_action_explain(action_id: str, body: Optional[ExplainRequest] = None) -> ExplainResult:
    """SPEC 2.8: 현재 프로필의 행동 카드 설명. 프로필 없음 또는 카드 없음이면 404.
    첫 화면에서는 호출되지 않고, 사용자가 "AI 설명 보기"를 눌렀을 때만 호출된다."""
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    refresh = body.refresh if body is not None else False
    detail = body.detail if body is not None else "full"
    result = explain_service.explain_action(
        action_id, _llm_provider, profile, params, today=date.today(), refresh=refresh, detail=detail,
    )
    if result is None:
        raise HTTPException(status_code=404, detail=f"행동 카드를 찾을 수 없습니다: {action_id}")
    return result


# ---------------------------------------------------------------------------
# 생애주기 층 (P7)
# ---------------------------------------------------------------------------


@router.get("/lifecycle", response_model=LifecycleView)
def get_lifecycle() -> LifecycleView:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    thresholds = lifecycle_service.load_thresholds()
    return lifecycle_service.build_lifecycle_view(profile, params, thresholds, today=date.today())


# ---------------------------------------------------------------------------
# 공시 비교
# ---------------------------------------------------------------------------


@router.post("/compare/prepare", response_model=CompareContext)
def post_compare_prepare(body: ComparePrepareRequest) -> CompareContext:
    profile = session_service.get_profile()
    return compare_service.prepare_context(profile, body.params or {})


@router.post("/compare/run", response_model=CompareResult)
def post_compare_run(
    ctx: CompareContext, previous_decision_id: Optional[str] = Query(None),
) -> CompareResult:
    """SPEC 2.14: `previous_decision_id`가 있으면 이전 결정과 비교한 `CompareResult.delta`를
    채운다(현재 세션 소유가 아니거나 kind·카테고리가 다르면 delta는 None)."""
    profile = session_service.get_profile()
    try:
        return compare_service.run_compare(
            ctx, profile, today=date.today(), previous_decision_id=previous_decision_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/compare/{decision_id}/explain", response_model=ExplainResult)
def post_compare_explain(decision_id: str, body: Optional[ExplainRequest] = None) -> ExplainResult:
    """SPEC 2.8: 공시 비교 결과 설명. 저장된 설명이 있고 refresh가 아니면 LLM을
    호출하지 않고 cached=true로 돌려준다. 결정 기록이 없으면 404."""
    refresh = body.refresh if body is not None else False
    detail = body.detail if body is not None else "full"
    result = explain_service.explain_compare(decision_id, _llm_provider, refresh=refresh, detail=detail)
    if result is None:
        raise HTTPException(status_code=404, detail=f"결정 기록을 찾을 수 없습니다: {decision_id}")
    return result


@router.get("/compare/{decision_id}/explain", response_model=ExplainResult)
def get_compare_explain(decision_id: str, detail: Literal["full", "brief"] = Query("full")) -> ExplainResult:
    """저장된 설명만 돌려준다(생성하지 않음). 없으면 404. `detail`(SPEC 2.16)은 POST의 body와
    같은 값을 쿼리로 받아 먼저 그 캐시 키(ref_id, full은 접미사 없음·brief는 "#brief")로
    찾는다. 없으면 다른 detail로 저장된 설명이라도 있으면 그것을 돌려준다(화면의 기존
    getCompareExplain 호출은 detail 쿼리를 붙이지 않으므로, 사용자가 "간단히"로 설정한
    상태에서 만들어진 설명도 이 GET에서 찾을 수 있어야 한다)."""
    if decisions_service.get(decision_id) is None:  # 다른 브라우저 세션의 결정 기록은 없는 것으로 본다
        raise HTTPException(status_code=404, detail="저장된 설명이 없습니다.")
    result = explain_service.get_stored("compare", f"{decision_id}{explain_service.ref_suffix(detail)}")
    if result is None:
        other_detail = "brief" if detail == "full" else "full"
        result = explain_service.get_stored("compare", f"{decision_id}{explain_service.ref_suffix(other_detail)}")
    if result is None:
        raise HTTPException(status_code=404, detail="저장된 설명이 없습니다.")
    return result


# ---------------------------------------------------------------------------
# 결정 기록
# ---------------------------------------------------------------------------


@router.get("/decisions")
def get_decisions(limit: int = 20) -> list[dict[str, Any]]:
    """목록은 result를 뺀 요약만 돌려준다(SPEC: "result 제외 요약")."""
    records = decisions_service.list_recent(limit=limit)
    summaries = []
    for record in records:
        data = record.model_dump(mode="json")
        data.pop("result", None)
        summaries.append(data)
    return summaries


@router.get("/decisions/{decision_id}", response_model=DecisionRecord)
def get_decision(decision_id: str) -> DecisionRecord:
    record = decisions_service.get(decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="결정 기록을 찾을 수 없습니다.")
    return record


@router.post("/decisions/{decision_id}/replay", response_model=ReplayResponse)
def post_replay(decision_id: str) -> ReplayResponse:
    record = decisions_service.get(decision_id)
    if record is None:
        raise HTTPException(status_code=404, detail="결정 기록을 찾을 수 없습니다.")
    try:
        result = decisions_service.replay(decision_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return ReplayResponse(**result)


# ---------------------------------------------------------------------------
# 소비 패턴(합성 데이터)
# ---------------------------------------------------------------------------


@router.get("/synthetic/{persona_id}/transactions.csv")
def get_synthetic_csv(persona_id: str) -> Response:
    try:
        rows = synthetic.generate_transactions(persona_id, months=3, seed=42, end=date.today())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"알 수 없는 페르소나입니다: {persona_id}")
    csv_text = synthetic.transactions_to_csv(rows)
    headers = {"Content-Disposition": f'attachment; filename="{persona_id}_transactions.csv"'}
    return Response(content=csv_text, media_type="text/csv", headers=headers)


def _profile_or_guest() -> UserProfile:
    """세션 프로필이 없으면 게스트 프로필(소득 0)로 분석한다(SPEC 2.6: 소비 패턴은
    프로필 없이도 체험할 수 있어야 하므로 404 대신 게스트로 계산한다)."""
    profile = session_service.get_profile()
    if profile is not None:
        return profile
    return UserProfile(
        id=chatlog.GUEST_PROFILE_ID, display_name="게스트",
        monthly_income=0, fixed_expenses=0, variable_expenses=0,
    )


def _spending_payload(summary, features, profile: UserProfile) -> dict[str, Any]:
    cards = spending_core.build_spending_cards(features, summary, profile)
    opportunities = spending_core.savings_opportunities(summary, features)
    linked_actions = spending_service.link_savings_to_debt(profile, opportunities, today=date.today())
    return {
        "summary": summary.model_dump(mode="json"),
        "features": features.model_dump(mode="json"),
        "cards": [c.model_dump(mode="json") for c in cards],
        "opportunities": [o.model_dump(mode="json") for o in opportunities],
        "linked_actions": [a.model_dump(mode="json") for a in linked_actions],
    }


@router.post("/spending/analyze")
def post_spending_analyze(body: SpendingAnalyzeRequest) -> dict[str, Any]:
    """브라우저가 파싱·정규화한 거래내역을 받아 그 자리에서 분석하고 요약/피처만
    저장한다(원본 거래내역은 응답 후 버려지며 서버에 저장하지 않는다, SPEC 2.6 D3/D4)."""
    profile = _profile_or_guest()
    months = body.months or 3
    summary, features = spending_service.analyze(
        body.transactions, profile, end=date.today(), months=months,
    )
    spending_service.save(profile.id, summary, features)
    return _spending_payload(summary, features, profile)


@router.post("/spending/analyze-synthetic")
def post_spending_analyze_synthetic(body: SpendingAnalyzeSyntheticRequest) -> dict[str, Any]:
    profile = _profile_or_guest()
    persona_id = body.persona_id or profile.id
    months = body.months or 3
    seed = body.seed if body.seed is not None else 42
    try:
        transactions = spending_service.load_synthetic(persona_id, months=months, seed=seed, end=date.today())
    except ValueError:
        raise HTTPException(status_code=404, detail=f"알 수 없는 페르소나입니다: {persona_id}")
    summary, features = spending_service.analyze(transactions, profile, end=date.today(), months=months)
    spending_service.save(profile.id, summary, features)
    return _spending_payload(summary, features, profile)


@router.get("/spending")
def get_spending() -> dict[str, Any]:
    profile = _profile_or_guest()
    loaded = spending_service.load(profile.id)
    if loaded is None:
        raise HTTPException(status_code=404, detail="저장된 소비 패턴 분석이 없습니다.")
    summary, features = loaded
    return _spending_payload(summary, features, profile)


@router.delete("/spending", response_model=OkResponse)
def delete_spending() -> OkResponse:
    profile = _profile_or_guest()
    spending_service.clear(profile.id)
    return OkResponse(ok=True)


@router.get("/spending/taxonomy")
def get_spending_taxonomy() -> dict[str, Any]:
    """카테고리 목록과 키워드 규칙(화면이 "어떻게 분류되는지" 보여줄 때 쓴다)."""
    return spending_core.taxonomy_info()


# ---------------------------------------------------------------------------
# 채팅 (SPEC 2.4/2.5: extract 실패/무효 JSON -> parse_message 폴백)
# ---------------------------------------------------------------------------

_CHAT_EXTRACT_SYSTEM = (
    "당신은 한국어 개인 부채 코치 앱의 의도·조건 추출기입니다. 사용자 발화에서 의도와 "
    "비교 조건 슬롯만 지정된 JSON 스키마로 추출하세요. 발화에 없는 값은 절대 채우지 말고, "
    "금액·금리·회사명·상품명을 추측해서 만들어내지 마세요."
)

_CHAT_EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "intent": {"type": "STRING", "enum": [
            "compare", "schedule", "scenario", "action", "faq", "spending",
            "retirement", "saving", "liquidity", "direct", "whatif",
        ]},
        "category": {"type": "STRING", "enum": ["deposit", "saving", "mortgage", "jeonse", "credit", "policy"]},
        "amount": {"type": "INTEGER"},
        "term_months": {"type": "INTEGER"},
        "max_rate": {"type": "NUMBER"},
        "exclude_companies": {"type": "ARRAY", "items": {"type": "STRING"}},
        "sort_key": {"type": "STRING", "enum": ["total_cost", "monthly_payment", "rate"]},
        # SPEC 2.13: 계산형 자유 질의(what-if) 슬롯.
        "extra_monthly": {"type": "INTEGER"},
        "lump_sum": {"type": "INTEGER"},
        "new_rate": {"type": "NUMBER"},
        "retirement_age": {"type": "INTEGER"},
        "loan_hint": {"type": "STRING", "enum": [
            "card_loan", "credit", "overdraft", "mortgage", "jeonse", "student", "policy",
        ]},
    },
    "required": ["intent"],
}

_CATEGORY_LABELS_KR = {
    "deposit": "예금", "saving": "적금", "mortgage": "주택담보대출",
    "jeonse": "전세자금대출", "credit": "신용대출", "policy": "정책상품",
}

_VALID_CHAT_INTENTS = {
    "compare", "schedule", "scenario", "action", "faq", "spending",
    "retirement", "saving", "liquidity", "direct", "whatif",
}

# SPEC 2.11: internal 경로인데 faq가 아닌 의도들. 이 의도들은 발화에 제도 키워드(KB
# 검색 점수 임계값 이상 히트)가 있으면 kb 노드도 함께 실행해 리소스와 칩을 덧붙인다.
_MULTI_NODE_ELIGIBLE_INTENTS = {
    "compare", "action", "schedule", "scenario", "retirement", "saving", "liquidity",
}

# SPEC 2.11: 시점성 질문 키워드. faq인데 KB 히트가 없고 이 키워드가 있으면 external로 보낸다.
_TIME_SENSITIVE_KEYWORDS = ("최신", "요즘", "지금", "현재", "올해", "뉴스", "발표", "기준금리")

# 생애주기 채팅 의도별로 리소스 패널에 곁들일 정책 기준값(config/policy_params.yaml 키).
_LIFECYCLE_POLICY_KEYS: dict[str, list[str]] = {
    "retirement": ["national_pension_start_age"],
    "saving": [],
    "liquidity": ["emergency_fund_months"],
}


def _normalize_intent(value: Any) -> str:
    return value if isinstance(value, str) and value in _VALID_CHAT_INTENTS else "faq"


_POSITIVE_NUMERIC_SLOT_KEYS = ("amount", "term_months", "max_rate")


def _clean_compare_params(slots: dict[str, Any]) -> dict[str, Any]:
    """추출된 슬롯 중 실제 CompareContext 구성에 쓸 값만 남긴다.

    금액·기간·금리 상한은 0·음수·숫자가 아닌 값이면 거른다(SEV5 2026-09-06 리뷰: "0원
    신용대출 비교해줘" 같은 평범한 발화가 그대로 CompareContext 검증(amount > 0)까지
    흘러가 500으로 이어지던 문제). 거른 값은 추정값으로 채워지도록 아예 슬롯에서 뺀다.
    """
    params: dict[str, Any] = {}
    for key in ("category", "amount", "term_months", "max_rate", "exclude_companies", "sort_key"):
        value = slots.get(key)
        if value in (None, "", []):
            continue
        if key in _POSITIVE_NUMERIC_SLOT_KEYS:
            try:
                num = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(num) or num <= 0:
                continue
        params[key] = value
    return params


def _ground_numeric_slots(slots: dict[str, Any], text: str) -> dict[str, Any]:
    """LLM이 발화에 없는 숫자를 지어내지 못하게 막는다(2026-09-06 실측: 금액 없는 발화에 3천만 원이 채워짐).

    규칙 파서가 같은 필드를 찾으면 그 값을 우선하고, 규칙 파서가 못 찾은 숫자는 발화에 정수부가
    그대로 등장할 때만 남긴다. 숫자는 결정론 코드의 입력이므로 근거 없는 값은 버린다.
    SPEC 2.13: whatif 슬롯(extra_monthly·lump_sum·new_rate·retirement_age)도 같은 규칙을 쓴다.
    """
    rule = guardrails.parse_message(text)
    norm_text = text.replace(",", "")
    for key in ("amount", "term_months", "max_rate", "extra_monthly", "lump_sum", "new_rate", "retirement_age"):
        value = slots.get(key)
        if value in (None, "", []):
            continue
        if rule.get(key) not in (None, "", []):
            slots[key] = rule[key]
            continue
        try:
            num = float(value)
        except (TypeError, ValueError):
            slots.pop(key, None)
            continue
        literal = str(int(num)) if num.is_integer() else str(num)
        if literal not in norm_text:
            slots.pop(key, None)
    return slots


def _build_lifecycle_chat_reply(
    intent: str, profile: Optional[UserProfile]
) -> tuple[str, list[Chip], Optional[dict[str, Any]]]:
    """P7 생애주기 층: retirement(노후·연금·은퇴)/saving(저축률)/liquidity(비상자금) 의도에
    코드가 계산한 재무비율·노후자금 격차 수치로 답한다(SPEC 2.7). 문장의 숫자는 전부
    `app.core.ratios`/`app.core.retirement`가 계산한 값이며 LLM은 관여하지 않는다."""
    chips = [Chip(id=f"chip-chat-{intent}", text="생애 흐름 보기", tier=1, intent="lifecycle", params={})]
    action: dict[str, Any] = {"type": "open_view", "payload": {"view": "lifecycle"}}

    if profile is None:
        reply_text = (
            "아직 프로필이 없어요. 페르소나를 선택하거나 내 부채 화면에서 정보를 입력하면 "
            "생애주기 정보를 계산해드릴게요."
        )
        return reply_text, [Chip(id="chip-chat-onboarding-lifecycle", text="페르소나 선택하러 가기", tier=1,
                                  intent="onboarding", params={})], None

    params_policy = policy.load_policy_params()

    if intent == "retirement":
        projections = retirement_core.retirement_gap_projection(profile, params_policy, today=date.today())
        base = next((p for p in projections if p.scenario == "기준"), projections[0] if projections else None)
        if base is None:
            reply_text = "노후자금 시뮬레이션에 필요한 정보가 부족합니다. 생애 흐름 화면에서 확인해보세요."
        elif base.shortfall > 0:
            # 2026-09-06 리뷰: required_monthly_saving이 이번 달 실제 여력(capacity.net_monthly)
            # 보다 크면(예: P6처럼 은퇴가 국민연금 개시 전이라 브릿지 구간까지 감당해야 하는
            # 경우), "매달 이만큼 저축하세요"는 사실상 실행 불가능한 지시라 대신 격차 크기만
            # 알리고 생애 흐름 화면에서 시나리오를 함께 살펴보도록 안내한다.
            schedules = [build_schedule(loan) for loan in profile.loans]
            capacity = compute_capacity(profile, schedules)
            base_summary = (
                f"기준 시나리오 기준 은퇴 시점({base.retirement_age}세) 생활비는 월 "
                f"{base.retirement_living_cost:,}원, 확정소득은 월 {base.guaranteed_income_monthly:,}원으로 "
                f"월 부족액 {base.monthly_gap:,}원, 필요 자금 {base.required_fund_pv:,}원으로 추정됩니다."
            )
            if base.required_monthly_saving > capacity.net_monthly:
                reply_text = (
                    f"{base_summary} 지금 이번 달 여력({capacity.net_monthly:,}원)만으로는 이 격차를 "
                    "매달 저축만으로 채우기 어려운 규모라 특정 저축액을 안내하지 않습니다. "
                    "생애 흐름 화면에서 은퇴 시점 조정 등 다른 시나리오를 함께 확인해보세요."
                )
            else:
                reply_text = (
                    f"{base_summary} 필요자금 대비 {base.shortfall:,}원이 모자라 매월 "
                    f"{base.required_monthly_saving:,}원을 추가로 저축하는 방법이 안내됩니다."
                )
        else:
            reply_text = (
                f"기준 시나리오 기준 은퇴 시점({base.retirement_age}세) 생활비는 월 "
                f"{base.retirement_living_cost:,}원, 확정소득은 월 {base.guaranteed_income_monthly:,}원으로 "
                "부족액이 없는 것으로 추정됩니다."
            )
        return reply_text, chips, action

    thresholds = lifecycle_service.load_thresholds()
    schedules = [build_schedule(loan) for loan in profile.loans]
    stage_result = lifecycle_core.classify_stage(profile, today=date.today())
    stage_thresholds = lifecycle_core.thresholds_for_stage(thresholds, stage_result.stage)
    ratios = ratios_core.compute_ratios(profile, schedules, stage_thresholds)

    if intent == "saving":
        if ratios.saving_rate is None:
            reply_text = "저축률을 계산할 소득 정보가 없습니다."
        else:
            reply_text = ratios.interpretations.get("saving_rate", "")
            if ratios.flags.get("saving_rate") == "warn":
                reply_text += " 생애 단계 기준보다 낮아 자동이체 저축 계획을 살펴보는 것이 안내됩니다."
    else:  # liquidity
        if ratios.liquidity_months is None:
            reply_text = "유동성비율을 계산할 자산 정보가 없습니다."
        else:
            reply_text = ratios.interpretations.get("liquidity_months", "")
            if ratios.flags.get("liquidity_months") == "warn":
                reply_text += " 생애 단계 기준보다 부족해 비상자금을 먼저 채우는 것이 안내됩니다."

    return reply_text, chips, action


_CRISIS_CONTACTS = {
    "self_harm": "자살예방상담전화 109(24시간), 정신건강위기상담 1577-0199",
    "financial": "신용회복위원회 1600-5500(채무조정 상담), 서민금융콜센터 1397, 불법 추심 신고 금융감독원 1332",
    # 2026-09-06 러너 소프트 경고 해소: financial 위기 응답에도 마음 건강 상담 채널을
    # 강요하지 않는 어조("~해도 돼요")로 한 줄 붙인다(P5-Q9, tests/golden/questions.yaml
    # "crisis-channel-mentioned" 검사). self_harm 분기는 이미 위 줄을 쓰므로 그대로 둔다.
    "mental_health": "정신건강위기상담 1577-0199나 자살예방상담전화 109",
}
_FOLLOWUP_KEYS = ("category", "amount", "term_months", "sort_key", "repay_method", "rate_type", "credit_band",
                  "lender_groups", "exclude_companies", "max_rate", "target_loan_id")
_FOLLOWUP_LABELS = {"amount": "금액", "term_months": "기간", "max_rate": "금리 상한", "category": "카테고리",
                    "sort_key": "정렬 기준", "exclude_companies": "제외 회사", "credit_band": "신용 구간",
                    "repay_method": "상환방식", "rate_type": "금리 유형", "lender_groups": "취급 기관"}


def _crisis_reply(level: str, profile: Optional[UserProfile]) -> tuple[str, list[Chip], Optional[dict[str, Any]]]:
    """위기 발화 응답. 상품·비교 안내를 하지 않고 공적 상담 창구만 안내한다."""
    chips = [
        Chip(id="chip-crisis-ccrs", text="채무조정 제도 안내", tier=1, intent="faq", params={"slug": "ccrs-debt-adjustment"}),
        Chip(id="chip-crisis-illegal", text="불법 추심 대응", tier=1, intent="faq", params={"slug": "illegal-lending-response"}),
    ]
    if level == "self_harm":
        text = ("많이 힘드셨겠어요. 지금 마음이 많이 힘들다면 먼저 사람과 이야기해 주세요. "
                f"{_CRISIS_CONTACTS['self_harm']}. 빚 문제는 혼자 해결하지 않아도 됩니다. "
                f"{_CRISIS_CONTACTS['financial']}에서 무료로 상담받을 수 있어요.")
        return text, chips, None
    text = ("지금 상황이 많이 버거우실 것 같아요. 연체나 독촉이 있을 때는 새 대출보다 공적 상담이 먼저입니다. "
            f"{_CRISIS_CONTACTS['financial']}. "
            f"마음이 많이 힘들면 {_CRISIS_CONTACTS['mental_health']}에 먼저 연락해도 돼요. ")
    if profile is not None:
        text += "홈의 안전 모드 카드에 오늘 할 수 있는 일 한 가지를 정리해 두었어요."
    else:
        text += "계정을 선택하면 지금 상황에 맞는 오늘의 할 일을 함께 정리해 드려요."
    return text, chips, {"type": "open_view", "payload": {"view": "home"}}


def _merge_followup(base: dict[str, Any], new_params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """같은 대화의 직전 비교 조건 위에 이번 발화의 델타만 덮어쓴다(후속 질의 재실행)."""
    merged = {k: base[k] for k in _FOLLOWUP_KEYS if k in base and base[k] is not None}
    changed: list[str] = []
    for k, v in new_params.items():
        if k == "exclude_companies":
            # SEV2 2026-09-06 리뷰: exclude_companies는 항상 리스트여야 하지만, 문자열
            # 하나만 온 경우(추출 스키마를 우회한 값 등)에도 리스트로 감싸 안전하게
            # 합친다(그렇지 않으면 list(v)가 문자열을 글자 단위로 쪼갠다).
            if isinstance(v, str):
                v = [v]
            prev = merged.get("exclude_companies") or []
            if isinstance(prev, str):
                prev = [prev]
            v = sorted(set(list(prev) + list(v or [])))
        if merged.get(k) != v:
            changed.append(k)
        merged[k] = v
    merged["estimated_fields"] = [f for f in (base.get("estimated_fields") or []) if f not in new_params]
    merged["user_confirmed"] = False
    return merged, changed


# ---------------------------------------------------------------------------
# 파이프라인 단계 이벤트("생각 과정", SPEC 2.9)
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    # 2026-09-06 PMO 지적: "생각 과정" 라벨이 기술 용어 그대로 노출됨. 사람 말로 바꾼다
    # (id는 그대로 둔다 - 화면·테스트가 id를 쓴다).
    "guard": "질문 확인",
    "intent": "무엇이 필요한지 파악",
    "compute": "상담 준비",
    "explain": "설명 쓰기",
    "check": "마지막 점검",
    # SPEC 2.11: 답변 경로·노드 카드용 신규 노드 id.
    "debt_data": "내 대출 살펴보기",
    "calc": "계산하기",
    "kb": "제도 안내 찾기",
    "products": "공시 자료 찾기",
    "external": "외부 자료 찾기",
    "direct": "바로 답하기",
}

_INTENT_LABELS_KR: dict[str, str] = {
    "compare": "공시 비교", "schedule": "상환표", "scenario": "시나리오", "action": "행동 제안",
    "faq": "제도 안내", "spending": "소비 패턴", "retirement": "노후자금", "saving": "저축률",
    "liquidity": "비상자금", "direct": "일반 안내", "whatif": "가정 계산",
}

# SPEC 2.13: whatif 도구별 표시 라벨(노드 detail·리소스 title에 쓴다).
_WHATIF_TOOL_LABELS_KR: dict[str, str] = {
    "extra_payment": "추가 상환",
    "lump_sum": "일시 상환",
    "refinance": "대환",
    "retirement_age": "은퇴 시점 변경",
}
_WHATIF_CALC_TITLES_KR: dict[str, str] = {
    "extra_payment": "추가 상환 효과",
    "lump_sum": "일시 상환 효과",
    "refinance": "대환 효과",
    "retirement_age": "은퇴 시점 변경",
}


def _whatif_calc_detail(result: WhatIfResult) -> str:
    if result.tool == "retirement_age":
        return f"은퇴 {result.before.get('retirement_age')}세 -> {result.after.get('retirement_age')}세"
    return f"{result.before.get('months')}개월 -> {result.after.get('months')}개월"


class _StageEmitter:
    """파이프라인 단계 이벤트 방출/기록(SPEC 2.9, 노드 카드는 SPEC 2.11). `emit`이 없으면
    trace만 쌓는다.

    같은 stage id로 `start` 뒤 종료 상태(done/fallback/skip)가 하나 온다. 저장되는
    `trace`(=`ChatReply.trace`)에는 종료 상태만 남기고, `start`는 실시간 스트림에만
    보낸다(스트리밍 화면의 "진행 중" 표시용). `steps`(사람이 읽는 단계 문장)와
    `resource_refs`(해당 `ChatResource.ref` 목록)는 SPEC 2.11 노드 카드용 추가 필드로,
    모든 이벤트에 항상 존재한다(없으면 빈 리스트).
    """

    def __init__(
        self,
        emit: Optional[Callable[[dict[str, Any]], None]],
        cancel_event: Optional[threading.Event] = None,
    ):
        self._emit = emit
        # SEV3 2026-09-06 리뷰: SSE 클라이언트가 스트림을 도중에 닫으면 `POST
        # /api/chat/stream`이 이 Event를 set한다. set된 뒤에는 start/finish가 조기
        # 반환하며 큐에 더 넣지 않는다(워커 스레드 자체의 계산은 계속 끝까지 돌되, 더는
        # 아무도 읽지 않는 큐에 이벤트를 쌓지 않는다).
        self._cancel_event = cancel_event
        self.trace: list[dict[str, Any]] = []
        self._started_at: dict[str, float] = {}

    def _cancelled(self) -> bool:
        return self._cancel_event is not None and self._cancel_event.is_set()

    def start(self, stage_id: str, detail: str = "") -> None:
        if self._cancelled():
            return
        self._started_at[stage_id] = time.monotonic()
        self._send({
            "id": stage_id, "label": _STAGE_LABELS[stage_id], "status": "start", "detail": detail, "ms": 0,
            "tech": "", "steps": [], "resource_refs": [],
        })

    def finish(
        self, stage_id: str, status: str, detail: str,
        *, tech: Optional[str] = None, steps: Optional[list[str]] = None,
        resource_refs: Optional[list[str]] = None,
    ) -> None:
        if self._cancelled():
            return
        started = self._started_at.get(stage_id)
        ms = int((time.monotonic() - started) * 1000) if started is not None else 0
        event = {
            "id": stage_id, "label": _STAGE_LABELS[stage_id], "status": status, "detail": detail, "ms": ms,
            # 2026-09-06 PMO 지적: label/detail은 사람 말로, 모델명·소요 초·사유 코드 같은
            # 기술 정보는 tech(선택, 화면은 작은 회색 줄로 보여준다)로 분리한다.
            "tech": tech or "", "steps": list(steps) if steps else [], "resource_refs": list(resource_refs) if resource_refs else [],
        }
        self.trace.append(event)
        self._send(event)

    def _send(self, event: dict[str, Any]) -> None:
        if self._emit is not None:
            self._emit(dict(event))


def _fmt_seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}초"


def _explain_stage_detail(result: ExplainResult) -> tuple[str, str, str]:
    """ExplainResult 하나로 explain 단계의 (status, detail, tech)을 만든다(SPEC 2.9).

    2026-09-06 PMO 지적: 모델명·소요 시간은 사람이 읽는 detail이 아니라 tech(기술 정보,
    선택)로 옮긴다."""
    if result.source == "llm":
        return "done", "설명을 썼어요", f"Gemini {result.model} · {_fmt_seconds(result.latency_ms)}"
    first_problem = result.problems[0] if result.problems else "unknown"
    return "fallback", "AI 대신 준비된 문장을 썼어요", f"템플릿({first_problem})"


@dataclass
class _ChatBuildResult:
    reply_text: str
    chips: list[Chip]
    action: Optional[dict[str, Any]]
    llm_used: bool
    trace: list[dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    # SPEC 2.11: 답변 경로, 근거 리소스, 답변 형식.
    route: str = "internal"
    resources: list[ChatResource] = field(default_factory=list)
    answer_format: str = "text"


def _build_chat_reply(
    message: str,
    base_params: Optional[dict[str, Any]] = None,
    emit: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
    detail: str = "full",
) -> _ChatBuildResult:
    """SPEC 2.9: 단계별로 `stages.start`/`stages.finish`를 호출해 "생각 과정"을 기록·방출한다.
    수치·판단 자체는 기존과 동일한 코드 경로(규칙 파서/코드 계산)로 만든다. LLM은 의도
    추출(intent 단계)과 문장 설명(explain 단계)에만 관여한다. `cancel_event`가 set되면
    (SSE 클라이언트가 스트림을 닫음) 단계 이벤트만 더 이상 큐에 넣지 않는다(계산 자체는
    끝까지 마친다, SEV3 2026-09-06 리뷰). `detail`(SPEC 2.16, "full"|"brief")은 설명 생성
    함수(explain_compare/explain_action/explain_chat_compare/explain_chat_whatif,
    format_kb_answer, build_direct_answer)에 그대로 넘긴다."""
    stages = _StageEmitter(emit, cancel_event)
    banned = insights_service.get_banned_terms()

    # ---- guard: PII 마스킹 + 위기 신호 확인 ----
    stages.start("guard", "질문을 읽고 있어요")
    masked = guardrails.mask_pii(message)
    crisis = guardrails.detect_crisis(masked)
    if crisis != "none":
        stages.finish("guard", "done", "많이 힘든 상황으로 보여 상담 창구부터 준비했어요")
        stages.start("compute")
        stages.finish("compute", "done", "상담 창구를 준비했어요")
        stages.start("explain")
        stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
        text, chips, action = _crisis_reply(crisis, session_service.get_profile())
        stages.start("check")
        # SEV3 2026-09-06 리뷰: guardrails.check_text(상품·회사명) 외에 권유 표현
        # (slotfill.FORBIDDEN_PHRASES)도 경로 무관하게 검사한다.
        if guardrails.check_text(text, banned) or any(p in text for p in slotfill.FORBIDDEN_PHRASES):
            stages.finish("check", "fallback", "표현 문제가 있어 기본 안내로 바꿨어요")
            text = "안내 문구를 표시할 수 없어 기본 안내로 대체했습니다. 메뉴에서 원하는 화면을 선택해주세요."
        else:
            stages.finish("check", "done", "상품 이름이나 권유 표현이 없는지 확인했어요")
        return _ChatBuildResult(text, chips, action, False, stages.trace, None, route="safety")
    stages.finish("guard", "done", "개인정보는 가리고 읽었어요")

    # ---- intent: 의도·조건 추출 ----
    stages.start("intent")
    slots: dict[str, Any] = {}
    llm_used = False
    extract_model: Optional[str] = None
    extract_latency_ms = 0
    if _llm_provider.available():
        try:
            result = _llm_provider.extract(masked, _CHAT_EXTRACT_SCHEMA, _CHAT_EXTRACT_SYSTEM)
            extract_latency_ms = result.latency_ms
            if isinstance(result.data, dict) and result.data.get("intent"):
                slots = result.data
                llm_used = True
                extract_model = result.model
        except LLMUnavailable:
            slots = {}
        except Exception:  # noqa: BLE001 - SEV4 2026-09-06 리뷰: 어떤 이유로 실패해도
            # 규칙 파서 폴백으로 떨어져야 한다(예: resp.json() 실패 같은 예상 밖 예외).
            logger.exception("chat intent extract 실패, 규칙 파서로 대체")
            slots = {}

    if not slots:
        slots = guardrails.parse_message(masked)
    elif llm_used:
        slots = _ground_numeric_slots(slots, masked)

    intent = _normalize_intent(slots.get("intent"))
    if intent == "faq" and guardrails.parse_message(masked).get("intent") == "direct":
        # LLM이 인사·도움말을 faq로 분류하면 KB 바이그램 잡음으로 엉뚱한 제도 문서가 붙는다
        # (2026-09-06 실측: "안녕, 뭐 할 수 있어?" → 학자금 문서). 규칙이 direct면 직접 답변으로 고정한다.
        intent = "direct"
    if (base_params and slots.get("intent") in (None, "", "faq") and intent != "compare"
            and any(slots.get(k) not in (None, "", []) for k in ("max_rate", "term_months", "amount", "exclude_companies", "sort_key"))):
        intent = "compare"  # 직전 비교 조건이 있는 대화에서 다른 의도 없이 조건만 말하면 후속 질의로 본다

    if llm_used:
        intent_label = _INTENT_LABELS_KR.get(intent, intent)
        stages.finish(
            "intent", "done", f"{intent_label}{slotfill.josa(intent_label, '으로/로')} 이해했어요",
            tech=f"Gemini {extract_model} · {_fmt_seconds(extract_latency_ms)}",
        )
    else:
        stages.finish("intent", "fallback", "AI 응답이 없어 규칙으로 파악했어요", tech="규칙 파서")

    profile = session_service.get_profile()
    chips: list[Chip] = []
    action: Optional[dict[str, Any]] = None
    explain_model: Optional[str] = None
    explain_llm_used = False
    # SPEC 2.11: 답변 경로, 근거 리소스, 답변 형식. faq가 아닌 의도는 전부 internal이다.
    route = "internal"
    resources: list[ChatResource] = []
    answer_format = "text"
    _static_faq_chips = [
        Chip(id="chip-chat-faq-compare", text="공시 비교하기", tier=1, intent="compare", params={}),
        Chip(id="chip-chat-faq-debts", text="내 부채 보기", tier=1, intent="schedule", params={}),
        Chip(id="chip-chat-faq-spending", text="소비 패턴 보기", tier=1, intent="spending", params={}),
    ]

    if intent == "direct":
        # SPEC 2.11: 자료가 필요 없는 질문(인사, 할 수 있는 일 문의, 잡담). 개인 수치도
        # 프로필도 참조하지 않는다.
        stages.start("direct")
        text, direct_llm_used, direct_model, direct_latency_ms, direct_problems = (
            answer_service.build_direct_answer(_llm_provider, banned, detail=detail)
        )
        if direct_llm_used:
            stages.finish("direct", "done", "자료 없이 바로 답할 수 있는 질문이에요",
                          tech=f"Gemini {direct_model} · {_fmt_seconds(direct_latency_ms)}",
                          steps=["DONN이 할 수 있는 일을 안내했어요."])
        else:
            first_problem = direct_problems[0] if direct_problems else "llm_unavailable"
            stages.finish("direct", "fallback", "자료 없이 바로 답할 수 있는 질문이에요",
                          tech=f"고정 문장({first_problem})",
                          steps=["DONN이 할 수 있는 일을 안내했어요."])
        reply_text = text
        route = "direct"
        explain_llm_used = direct_llm_used
        explain_model = direct_model
        chips.extend(_static_faq_chips)

    elif intent == "compare":
        # SEV2 2026-09-06 리뷰: "compute"라는 뭉뚱그린 id 대신 SPEC 2.11의 노드 어휘
        # (debt_data/calc/products) 중 이 의도의 계산 성격에 맞는 "products"(공시 자료)를
        # 실제로 방출한다(그동안 _STAGE_LABELS에만 있고 어디서도 emit되지 않았다).
        stages.start("products")
        params = _clean_compare_params(slots)
        followup_changed: list[str] = []
        ctx: Optional[CompareContext] = None
        try:
            if base_params and params.get("category") in (None, base_params.get("category")):
                merged, followup_changed = _merge_followup(base_params, params)
                try:
                    ctx = CompareContext.model_validate(merged)
                except (ValidationError, ValueError):  # 직전 조건이 깨졌으면 새 추정으로 되돌아간다
                    ctx = compare_service.prepare_context(profile, params)
            else:
                ctx = compare_service.prepare_context(profile, params)
        except (ValidationError, ValueError):
            # SEV5 2026-09-06 리뷰: 평범한 발화("0원 신용대출 비교해줘" 등)가 조건 추정/검증
            # 단계에서 예외를 내면 안내 문장으로 답하고, 500으로 새지 않게 한다.
            ctx = None

        if ctx is None:
            stages.finish("products", "fallback", "비교 조건을 이해하지 못했어요")
            reply_text = "조건을 이해하지 못했어요. 금액과 기간(최대 600개월)을 다시 알려주세요."
            stages.start("explain")
            stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
        else:
            category_label = _CATEGORY_LABELS_KR.get(ctx.category.value, ctx.category.value)

            if ctx.category in (ProductCategory.DEPOSIT, ProductCategory.SAVING):
                # 결정 D5: 예·적금은 순위 비교 대상이 아니다. 비교 화면으로 보내는 대신
                # 공시 열람만 안내하고, prepare_compare 액션은 만들지 않는다(2026-09-06 리뷰).
                stages.finish(
                    "products", "done",
                    f"{category_label}{slotfill.josa(category_label, '은/는')} 순위 비교 대상이 아니에요",
                )
                reply_text = compare_service.NO_RANKING_CATEGORY_MESSAGE
                stages.start("explain")
                stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
            else:
                resources.extend(answer_service.profile_resources(profile))
                products_res = answer_service.products_resource(ctx.category)
                resources.append(products_res)
                compute_steps = [f"{products_res.title}을 조건에 맞춰 준비했어요."]
                followup_labels = [_FOLLOWUP_LABELS.get(k, k) for k in followup_changed]
                if followup_labels:
                    stages.finish("products", "done", f"이전 조건에서 {', '.join(followup_labels)}만 바꿔서 다시 준비했어요",
                                  steps=compute_steps, resource_refs=[r.ref for r in resources])
                else:
                    # 2026-09-06 PMO 지적: 조건 자체(금액·기간)보다 "공시 자료에서 몇 건을
                    # 골랐는지"가 이 단계에서 실제로 한 일이라 그 정보로 detail을 쓴다.
                    catalog_items = products.query(ctx.category)
                    disclosure_month = next(
                        (it.disclosure_month for it in catalog_items if it.disclosure_month), "",
                    )
                    month_prefix = f"{answer_service.format_disclosure_month(disclosure_month)} " if disclosure_month else ""
                    stages.finish(
                        "products", "done",
                        f"{month_prefix}공시 상품 {len(catalog_items)}건을 골랐어요",
                        steps=compute_steps, resource_refs=[r.ref for r in resources],
                    )
                stages.start("explain")
                explain_result = explain_service.explain_chat_compare(
                    ctx, followup_labels, _llm_provider, detail=detail,
                )
                status, stage_detail, tech = _explain_stage_detail(explain_result)
                stages.finish("explain", status, stage_detail, tech=tech)
                if explain_result.source == "llm":
                    explain_llm_used = True
                    explain_model = explain_result.model
                # SPEC 2.16: 설명 문장 뒤에 코드가 "준비한 조건" 목록을 마크다운으로 붙인다
                # (LLM 문장이든 템플릿 문장이든 동일하게, answer_format="markdown").
                max_rate_label = f"{ctx.max_rate:.4g}%" if ctx.max_rate is not None else "없음"
                prepared_lines = [
                    "", "**준비한 조건**",
                    f"- 카테고리: {category_label}",
                    f"- 금액: {ctx.amount:,}원",
                    f"- 기간: {ctx.term_months}개월",
                    f"- 금리 상한: {max_rate_label}",
                    f"- 정렬 기준: {explain_service._sort_basis_label(ctx.sort_key)}",
                ]
                reply_text = (
                    explain_result.summary + " 아래 버튼으로 조건을 확인하고 실행해보세요.\n"
                    + "\n".join(prepared_lines)
                )
                answer_format = "markdown"
                action = {"type": "prepare_compare", "payload": {"params": ctx.model_dump(mode="json")}}

    elif intent in ("schedule", "scenario"):
        # SEV2 2026-09-06 리뷰: 내 대출/프로필 자료만 참조하는 의도라 "debt_data"(내 부채
        # 자료) 노드로 실제 방출한다("compute"는 더 이상 쓰지 않는다).
        stages.start("debt_data")
        resources.extend(answer_service.profile_resources(profile))
        if resources:
            stages.finish("debt_data", "done", f"대출 {len(profile.loans) if profile else 0}건과 이번 달 여력을 확인했어요",
                          steps=["내 대출 자료를 참조했어요."], resource_refs=[r.ref for r in resources])
        else:
            stages.finish("debt_data", "done", "상환표와 시나리오를 준비했어요")
        reply_text = "내 부채 화면에서 상환표와 시나리오를 확인할 수 있어요."
        action = {"type": "open_view", "payload": {"view": "debts"}}
        chips.append(Chip(id=f"chip-chat-{intent}", text="내 부채로 이동", tier=1, intent=intent, params={}))
        stages.start("explain")
        stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")

    elif intent == "spending":
        stages.start("debt_data")
        resources.extend(answer_service.profile_resources(profile))
        if resources:
            stages.finish("debt_data", "done", f"대출 {len(profile.loans) if profile else 0}건과 이번 달 여력을 확인했어요",
                          steps=["내 대출 자료를 참조했어요."], resource_refs=[r.ref for r in resources])
        else:
            stages.finish("debt_data", "done", "소비 패턴 화면을 준비했어요")
        reply_text = "소비 패턴 화면에서 합성 거래내역을 확인할 수 있어요."
        action = {"type": "open_view", "payload": {"view": "spending"}}
        stages.start("explain")
        stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")

    elif intent in ("retirement", "saving", "liquidity"):
        # SEV2 2026-09-06 리뷰: 재무비율·노후자금은 실제 계산이므로 "calc"(계산 엔진)
        # 노드로 실제 방출한다.
        stages.start("calc")
        resources.extend(answer_service.profile_resources(profile))
        if profile is not None:
            policy_keys = _LIFECYCLE_POLICY_KEYS.get(intent, [])
            if policy_keys:
                resources.extend(answer_service.policy_resources(policy.load_policy_params(), policy_keys))
        reply_text, chips, action = _build_lifecycle_chat_reply(intent, profile)
        if profile is not None:
            stages.finish("calc", "done", "재무비율과 노후자금을 계산했어요",
                          steps=["재무비율과 노후자금 격차를 계산했어요."],
                          resource_refs=[r.ref for r in resources])
        else:
            stages.finish("calc", "done", "프로필이 없어 계산하지 못했어요")
        stages.start("explain")
        stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")

    elif intent == "action":
        # SEV2 2026-09-06 리뷰: 행동 규칙 평가는 계산 엔진의 산출물이므로 "calc" 노드로
        # 실제 방출한다.
        stages.start("calc")
        if profile is None:
            reply_text = (
                "아직 프로필이 없어요. 페르소나를 선택하거나 내 부채 화면에서 정보를 입력하면 "
                "맞춤 행동을 보여드릴게요."
            )
            chips.append(Chip(id="chip-chat-onboarding", text="페르소나 선택하러 가기", tier=1,
                               intent="onboarding", params={}))
            stages.finish("calc", "done", "프로필이 없어 행동 규칙을 검토하지 못했어요")
            stages.start("explain")
            stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
        else:
            resources.extend(answer_service.profile_resources(profile))
            params_policy = policy.load_policy_params()
            cards = actions_service.list_actions(profile, params_policy, today=date.today())
            if cards:
                top = cards[0]
                resources.append(answer_service.calc_resource("행동 카드", top.id, top.title))
                stages.finish("calc", "done", f"행동 규칙을 검토해 {len(cards)}건을 찾았어요",
                              steps=[f"행동 규칙 {len(cards)}건을 평가해 최우선 카드를 골랐어요."],
                              resource_refs=[r.ref for r in resources])
                if top.chip is not None:
                    chips.append(top.chip)
                stages.start("explain")
                explain_result = explain_service.explain_action(
                    top.id, _llm_provider, profile, params_policy, today=date.today(), detail=detail,
                )
                if explain_result is not None:
                    status, stage_detail, tech = _explain_stage_detail(explain_result)
                    stages.finish("explain", status, stage_detail, tech=tech)
                    if explain_result.source == "llm":
                        explain_llm_used = True
                        explain_model = explain_result.model
                    reply_text = explain_result.summary
                else:
                    stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
                    reply_text = top.summary
            else:
                stages.finish("calc", "done", "행동 규칙을 검토했지만 안내할 항목이 없었어요",
                              resource_refs=[r.ref for r in resources])
                reply_text = "지금은 특별히 안내할 행동이 없어요. 계속 잘 관리하고 계세요."
                stages.start("explain")
                stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")

    elif intent == "whatif":
        # SPEC 2.13: 계산형 자유 질의("매달 30만 원 더 갚으면?" 등). 슬롯이 없으면 되묻고,
        # 프로필이 없으면 온보딩 안내로 답한다(둘 다 debt_data/calc 노드를 열지 않는다 -
        # "노드는 intent까지").
        whatif_input = {k: slots.get(k) for k in
                         ("extra_monthly", "lump_sum", "new_rate", "retirement_age", "loan_hint")}
        has_calc_slot = any(
            whatif_input.get(k) not in (None, "", []) for k in
            ("extra_monthly", "lump_sum", "new_rate", "retirement_age")
        )
        if profile is None:
            stages.start("debt_data")
            reply_text = (
                "아직 프로필이 없어요. 페르소나를 선택하거나 내 부채 화면에서 정보를 입력하면 "
                "가정 계산을 도와드릴게요."
            )
            chips.append(Chip(id="chip-chat-onboarding-whatif", text="페르소나 선택하러 가기", tier=1,
                               intent="onboarding", params={}))
            stages.finish("debt_data", "done", "프로필이 없어 계산하지 못했어요")
            stages.start("explain")
            stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
        elif not has_calc_slot:
            reply_text = "매달 얼마를 더 갚을지, 또는 어떤 금리·은퇴 나이를 가정할지 알려주세요(예: 30만 원 더 갚으면?)"
            stages.start("explain")
            stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
        else:
            params_policy = policy.load_policy_params()
            result = whatif_service.run_whatif(profile, params_policy, whatif_input, today=date.today())
            stages.start("debt_data")
            if result is None:
                stages.finish("debt_data", "done", "계산할 수 있는 대출 정보가 없어요")
                reply_text = "계산할 수 있는 대출 정보가 없어요. 내 부채 화면에서 대출 정보를 입력해 주세요."
                stages.start("explain")
                stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
            else:
                resources.extend(answer_service.profile_resources(profile))
                if result.tool == "retirement_age":
                    stages.finish("debt_data", "done", "은퇴 계획 정보를 확인했어요",
                                  steps=["프로필의 자산·저축 정보를 참조했어요."],
                                  resource_refs=[r.ref for r in resources])
                else:
                    n_loans = len(profile.loans)
                    stages.finish("debt_data", "done", f"대출 {n_loans}건 중 {result.target_loan_label}을 골랐어요",
                                  steps=["내 대출 자료를 참조했어요."], resource_refs=[r.ref for r in resources])

                stages.start("calc")
                tool_label = _WHATIF_TOOL_LABELS_KR.get(result.tool, result.tool)
                calc_res = answer_service.calc_resource(
                    _WHATIF_CALC_TITLES_KR.get(result.tool, tool_label), f"whatif:{result.tool}",
                    _whatif_calc_detail(result),
                )
                resources.append(calc_res)
                stages.finish("calc", "done", f"{tool_label} 효과를 계산했어요",
                              steps=[f"{tool_label} 효과를 계산했어요."], resource_refs=[calc_res.ref])

                stages.start("explain")
                conclusion, w_llm_used, w_model, w_latency_ms, w_problems = explain_service.explain_chat_whatif(
                    result, _llm_provider, detail=detail,
                )
                if w_llm_used:
                    stages.finish("explain", "done", "설명을 썼어요", tech=f"Gemini {w_model} · {_fmt_seconds(w_latency_ms)}")
                    explain_llm_used = True
                    explain_model = w_model
                else:
                    first_problem = w_problems[0] if w_problems else "template"
                    stages.finish("explain", "fallback", "AI 대신 준비된 문장을 썼어요", tech=f"템플릿({first_problem})")

                reply_text = answer_service.format_whatif_answer(result, conclusion)
                answer_format = "markdown"

                chips.append(Chip(id="chip-chat-whatif-scenario", text="시나리오로 확인하기", tier=1,
                                   intent="scenario", params={}))
                chips.append(Chip(id="chip-chat-whatif-schedule", text="상환표 보기", tier=1,
                                   intent="schedule", params={}))
                if result.tool == "refinance":
                    sp = result.scenario_params
                    chips.append(Chip(
                        id="chip-chat-whatif-compare", text="내 조건으로 공시 비교", tier=1, intent="compare",
                        params={"amount": sp.get("amount"), "term_months": sp.get("term_months"),
                                "target_loan_id": sp.get("loan_id")},
                    ))

    else:  # faq: internal(KB 히트) 또는 external(KB 미달·시점성 질문)
        # 라우팅 근거는 두 단계다. (1) 발화에 문서 키워드가 그대로 들어있으면(고정밀) 그 문서로
        # internal. (2) 아니면 바이그램 점수가 임계값을 넘고 시점성 질문이 아닐 때만 internal.
        # 바이그램 점수만 쓰면 "요즘 기준금리 얼마야?"가 "금리" 겹침으로 금리인하요구권 문서에
        # 붙는 오답이 난다(2026-09-06 화면 실측).
        hits = kb_search.search(masked, k=20)
        hit = hits[0] if hits else None
        keyword_doc = answer_service.find_institutional_keyword_doc(masked)
        time_sensitive_q = any(k in masked for k in _TIME_SENSITIVE_KEYWORDS)
        if keyword_doc is not None:
            kw_hit = next((h for h in hits if h.slug == keyword_doc.slug), None)
            if kw_hit is not None:
                hit = kw_hit
            kb_ok = hit is not None and hit.slug == keyword_doc.slug
        else:
            kb_ok = hit is not None and hit.score > kb_search.MIN_SCORE and not time_sensitive_q

        if kb_ok:
            route = "internal"
            answer_format = "markdown"
            doc = next((d for d in kb_search.load_docs() if d.slug == hit.slug), None)
            if doc is None:  # 방어적: 검색 인덱스와 문서 캐시가 어긋난 경우에만 발생
                answer_format = "text"
                reply_text = f"{hit.title} 안내입니다. {hit.snippet}"
                stages.start("kb")
                stages.finish("kb", "done", f"{hit.title} 문서를 찾았어요")
                stages.start("explain")
                stages.finish("explain", "skip", "준비된 문장을 그대로 썼어요")
            else:
                stages.start("kb")
                # SPEC 2.16: 섹션 개수 상한(최대 5는 full, 3은 brief)은 format_kb_answer가
                # 답변 본문에 실제로 쓰는 개수와 같아야 "N개 문단을 참조했어요" 안내와
                # 리소스 패널이 어긋나지 않는다.
                ref_sections = answer_service.kb_reference_sections(
                    doc, limit=explain_service.KB_SECTION_LIMIT[detail],
                )
                kb_focus_hit = answer_service.kb_focus_hit(doc, masked)
                kb_res = answer_service.kb_resources(doc, ref_sections)
                resources.extend(kb_res)
                stages.finish("kb", "done", f"{hit.title} 문서에서 {len(ref_sections)}개 문단을 참조했어요",
                              steps=[f"제도 문서 1편에서 {len(ref_sections)}개 문단을 참조했어요: {hit.title}"]
                                    + ([f"질문과 가장 가까운 문단을 먼저 찾았어요({kb_focus_hit[0]})"] if kb_focus_hit else []),
                              resource_refs=[r.ref for r in kb_res])
                stages.start("explain")
                markdown_text, kb_llm_used, kb_model, kb_latency_ms, kb_problems = answer_service.format_kb_answer(
                    doc, hit, _llm_provider, banned,
                    deadline_seconds=explain_service.CHAT_EXPLAIN_DEADLINE_SECONDS, detail=detail, question=masked,
                )
                if kb_llm_used:
                    stages.finish("explain", "done", "설명을 썼어요", tech=f"Gemini {kb_model} · {_fmt_seconds(kb_latency_ms)}")
                    explain_llm_used = True
                    explain_model = kb_model
                else:
                    first_problem = kb_problems[0] if kb_problems else "규칙 렌더링"
                    stages.finish("explain", "fallback", "AI 대신 준비된 문장을 썼어요", tech=f"템플릿({first_problem})")
                reply_text = markdown_text
            chips.append(Chip(id=f"chip-kb-{hit.slug}", text=f"{hit.title} 자세히 보기", tier=1,
                              intent="faq", params={"slug": hit.slug}))
            action = {"type": "open_kb", "payload": {"slug": hit.slug, "title": hit.title,
                                                    "sources": hit.sources or []}}
        else:
            route = "external"
            time_sensitive = any(k in masked for k in _TIME_SENSITIVE_KEYWORDS)
            reason_step = (
                "시점성 질문이라 외부 검색을 사용했어요." if time_sensitive
                else "내부 자료에서 답을 찾지 못해 외부 검색을 사용했어요."
            )
            stages.start("external")
            text, ext_resources, ext_llm_used, ext_model, ext_problems, search_html = (
                answer_service.external_answer(masked, _llm_provider, banned)
            )
            resources.extend(ext_resources)
            # SEV3 2026-09-06 리뷰: 그라운딩 응답이 왔지만 요약이 비어(empty_summary) 폴백
            # 문구로 대체된 경우도 실제로는 그라운딩에 실패한 것이므로, llm_used를 True로
            # 만들지 않고 trace도 fallback으로 남긴다(그래야 화면이 "그라운딩 성공"으로
            # 잘못 표시하지 않는다).
            grounded_ok = ext_llm_used and not (
                {"banned_term_removed", "no_grounding_sources", "empty_summary"} & set(ext_problems)
            )
            if grounded_ok:
                stages.finish("external", "done", f"웹 검색으로 출처 {len(ext_resources)}건을 찾았어요",
                              tech=f"Gemini {ext_model}",
                              steps=[reason_step, f"출처 {len(ext_resources)}건을 모았어요."],
                              resource_refs=[r.ref for r in ext_resources])
                explain_llm_used = True
                explain_model = ext_model
            else:
                first_problem = ext_problems[0] if ext_problems else "search_unavailable"
                stages.finish("external", "fallback", "내부 자료에 없어 공식 안내 링크를 모았어요",
                              tech=first_problem,
                              steps=[reason_step, f"공식 안내 링크 {len(ext_resources)}건을 모았어요."],
                              resource_refs=[r.ref for r in ext_resources])
            reply_text = text
            if search_html:
                action = {"type": "search_suggestions", "payload": {"html": search_html}}
        chips.extend(_static_faq_chips)

    # ---- 여러 노드: 주 의도(내부 계산·화면 안내 의도)인데 발화에 제도 키워드가 뚜렷하게
    # 들어있으면 kb 노드도 실행해 리소스와 칩을 함께 붙인다(SPEC 2.11, 예 "내 상황에서
    # 금리인하요구권 쓸 수 있어?"). faq 자체는 위에서 이미 처리했으므로 대상이 아니다.
    # `kb_search.search()`의 점수 대신 `find_institutional_keyword_doc`(발화에 문서
    # keyword가 그대로 들어있는지)을 쓴다: 바이그램 점수는 "상환표 보여줘"가 학자금
    # 문서와 우연히 크게 겹치는 것처럼 노이즈가 많아 이 "부가 참조" 판단에는 부적합하다.
    if intent in _MULTI_NODE_ELIGIBLE_INTENTS:
        extra_doc = answer_service.find_institutional_keyword_doc(masked)
        if extra_doc is not None:
            extra_sections = answer_service.kb_reference_sections(extra_doc)
            extra_res = answer_service.kb_resources(extra_doc, extra_sections)
            resources.extend(extra_res)
            stages.start("kb")
            stages.finish("kb", "done", f"{extra_doc.title} 문서에서 참조했어요",
                          steps=[f"제도 문서 1편에서 참조했어요: {extra_doc.title}"],
                          resource_refs=[r.ref for r in extra_res])
            extra_chip_id = f"chip-kb-extra-{extra_doc.slug}"
            if not any(c.id == extra_chip_id for c in chips):
                chips.append(Chip(id=extra_chip_id, text=f"{extra_doc.title} 자세히 보기", tier=1,
                                  intent="faq", params={"slug": extra_doc.slug}))

    # ---- check: 응답 점검 ----
    stages.start("check")
    # KB 응답도 예외 없이 검사한다(2026-09-06 리뷰: kb_reply 우회는 kb/*.md에 실제
    # 금융회사명이 남아있어도 그대로 통과시키는 구멍이었다). kb/*.md는 이제 상호금융권 등
    # 개별 기관 실명을 쓰지 않으므로(SEV5 #3) 15개 문서 전부 이 검사를 통과해야 한다.
    # SEV3 2026-09-06 리뷰: guardrails.check_text(products 테이블 기반 상품·회사명) 외에
    # slotfill.FORBIDDEN_PHRASES(권유 표현)도 검사한다. LLM 문장뿐 아니라 규칙/KB 렌더링
    # 경로도 예외 없이 통과해야 한다("경로 무관").
    if guardrails.check_text(reply_text, banned) or any(p in reply_text for p in slotfill.FORBIDDEN_PHRASES):
        stages.finish("check", "fallback", "표현 문제가 있어 기본 안내로 바꿨어요")
        reply_text = "안내 문구를 표시할 수 없어 기본 안내로 대체했습니다. 메뉴에서 원하는 화면을 선택해주세요."
        answer_format = "text"
    else:
        stages.finish("check", "done", "상품 이름이나 권유 표현이 없는지 확인했어요")

    final_llm_used = llm_used or explain_llm_used
    final_model = explain_model or extract_model
    return _ChatBuildResult(
        reply_text, chips, action, final_llm_used, stages.trace, final_model,
        route=route, resources=resources, answer_format=answer_format,
    )


def _current_profile_id() -> str:
    profile = session_service.get_profile()
    return profile.id if profile is not None else chatlog.GUEST_PROFILE_ID


def _append_reply_message(
    chat_id: str,
    reply_text: str,
    *,
    llm_used: bool,
    action: Optional[dict[str, Any]],
    chips: list[Chip],
    trace: list[dict[str, Any]],
    route: str,
    resources: list[ChatResource],
    answer_format: str,
    model: Optional[str],
) -> None:
    """대화 로그에 응답 메시지 1건을 저장한다. route/resources/answer_format/model은
    meta_json 한 컬럼에 함께 저장된다(SPEC 2.11, `chatlog.get_messages`가 응답 메시지마다
    이 네 값을 돌려준다). `run_chat`과 `post_chat_attach`(SPEC 2.12)가 이 함수를 공유한다."""
    meta = {
        "route": route,
        "resources": [r.model_dump(mode="json") for r in resources],
        "answer_format": answer_format,
        "model": model,
    }
    chatlog.append_message(
        chat_id, "reply", reply_text, llm_used=llm_used, action=action,
        chips=[c.model_dump(mode="json") for c in chips], trace=trace, meta=meta,
    )


def run_chat(
    body: ChatRequest,
    emit: Optional[Callable[[dict[str, Any]], None]] = None,
    cancel_event: Optional[threading.Event] = None,
) -> ChatReply:
    """`/api/chat`과 `/api/chat/stream`이 공유하는 본체(SPEC 2.9). `emit`이 있으면 파이프라인
    단계 이벤트를 실시간으로 방출한다(없으면 무시하고 마지막에 `ChatReply.trace`로만 실린다).
    `cancel_event`는 `/api/chat/stream`이 클라이언트 연결 종료를 알리는 데만 쓴다(SEV3
    2026-09-06 리뷰, `_build_chat_reply` 문서 참고).

    대화 로그는 현재 프로필(페르소나)에 종속된다. chat_id가 없거나 다른 프로필 것이면
    새 대화를 만든다."""
    profile_id = _current_profile_id()
    chat_id = body.chat_id
    if chat_id:
        chat = chatlog.get_chat(chat_id)
        if chat is None or chat["profile_id"] != profile_id:
            chat_id = None
    base_params: Optional[dict[str, Any]] = None
    if chat_id:
        for m in reversed(chatlog.get_messages(chat_id)):
            a = m.get("action") or {}
            if a.get("type") == "prepare_compare":
                base_params = (a.get("payload") or {}).get("params")
                break
    if not chat_id:
        title = guardrails.mask_pii(body.message).strip()[:30]
        chat_id = chatlog.create_chat(profile_id, title=title)["id"]
    chatlog.append_message(chat_id, "user", guardrails.mask_pii(body.message))

    built = _build_chat_reply(
        body.message, base_params=base_params, emit=emit, cancel_event=cancel_event, detail=body.detail,
    )

    _append_reply_message(
        chat_id, built.reply_text, llm_used=built.llm_used, action=built.action, chips=built.chips,
        trace=built.trace, route=built.route, resources=built.resources,
        answer_format=built.answer_format, model=built.model,
    )
    return ChatReply(
        reply_text=built.reply_text, chips=built.chips, action=built.action, llm_used=built.llm_used,
        chat_id=chat_id, trace=built.trace, model=built.model,
        route=built.route, resources=built.resources, answer_format=built.answer_format,
    )


_GENERIC_CHAT_ERROR_MESSAGE = "응답을 만들지 못했어요. 잠시 후 다시 시도해 주세요."


@router.post("/chat", response_model=ChatReply)
def post_chat(body: ChatRequest) -> ChatReply:
    """대화 로그는 현재 프로필(페르소나)에 종속된다. chat_id가 없거나 다른 프로필 것이면 새 대화를 만든다."""
    try:
        return run_chat(body)
    except Exception:  # noqa: BLE001 - SEV3 2026-09-06 리뷰: 예외 원문(모듈 경로,
        # 트레이스백 등)을 사용자 응답에 노출하지 않는다. 원문은 로그로만 남긴다.
        logger.exception("POST /api/chat 처리 실패")
        raise HTTPException(status_code=500, detail=_GENERIC_CHAT_ERROR_MESSAGE)


@router.post("/chat/stream")
def post_chat_stream(body: ChatRequest) -> StreamingResponse:
    """SPEC 2.9: 파이프라인 단계("생각 과정")를 실시간으로 내보내는 SSE 엔드포인트.

    워커 스레드에서 `run_chat`을 실행하고(대화 로그 저장까지 `/api/chat`과 동일하게
    그 스레드 안에서 끝낸다), emit된 stage 이벤트를 큐로 받아 `event: stage` 프레임으로
    즉시 내보낸다. 끝나면 `event: reply`, 스레드에서 예외가 나면 `event: error`를 보내고
    스트림을 닫는다(스레드 예외를 큐로 전달해 조용히 사라지지 않게 한다).
    """

    # SEV3 2026-09-06 리뷰: 클라이언트가 스트림을 도중에 닫으면 제너레이터의 finally에서
    # 이 Event를 set한다. _StageEmitter가 이를 보고 조기 반환해 더 이상 큐에 넣지 않는다.
    cancel_event = threading.Event()

    def worker(q: "queue.Queue[tuple[str, Any]]") -> None:
        try:
            reply = run_chat(
                body, emit=lambda stage: q.put(("stage", stage)), cancel_event=cancel_event,
            )
        except Exception:  # noqa: BLE001 - SEV3 2026-09-06 리뷰: 원문 예외를 그대로
            # 클라이언트에 보내지 않는다(모듈 경로·트레이스백 노출 방지). 원문은 로그로만.
            logger.exception("POST /api/chat/stream 처리 실패")
            q.put(("error", _GENERIC_CHAT_ERROR_MESSAGE))
        else:
            q.put(("reply", reply))
        finally:
            q.put(("done", None))

    def event_stream():
        q: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        # 요청 컨텍스트(브라우저 세션 sid 등 contextvars)는 스레드에 자동으로 전달되지 않으므로
        # 스냅샷 안에서 워커를 실행한다(전역 threading 패치 대신 이 한 곳에서 명시적으로 처리).
        request_ctx = contextvars.copy_context()
        worker_thread = threading.Thread(target=request_ctx.run, args=(worker, q), daemon=True)
        worker_thread.start()
        try:
            while True:
                try:
                    kind, payload = q.get(timeout=0.1)
                except queue.Empty:
                    # 스레드가 이미 끝났는데 큐도 비어 있으면(이론상 "done"이 먼저 와야 하지만
                    # 방어적으로), 또는 클라이언트가 이미 연결을 닫아 취소되었으면 더 기다리지
                    # 않고 종료한다(SEV3 2026-09-06 리뷰: 큐 대기 루프가 스레드 종료 또는
                    # cancel 중 하나만 있어도 반드시 끝나야 한다).
                    if cancel_event.is_set() or (not worker_thread.is_alive() and q.empty()):
                        break
                    continue
                if kind == "stage":
                    yield f"event: stage\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                elif kind == "reply":
                    data = payload.model_dump(mode="json")
                    yield f"event: reply\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                elif kind == "error":
                    yield f"event: error\ndata: {json.dumps({'message': payload}, ensure_ascii=False)}\n\n"
                elif kind == "done":
                    break
        finally:
            # 정상 종료든 클라이언트가 도중에 스트림을 닫아 GeneratorExit이 나든 항상
            # 실행된다(SEV3 2026-09-06 리뷰: SSE 스레드 정리).
            cancel_event.set()

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@router.post("/chat/attach", response_model=ChatReply)
def post_chat_attach(body: ChatAttachRequest) -> ChatReply:
    """SPEC 2.12: 대화창에 첨부한 거래내역을 즉시 분석해 소비 패턴 요약을 답변으로
    돌려준다. `/api/spending/analyze`와 같은 프라이버시 원칙(원본 거래내역 미저장, 요약·
    피처만 저장)을 따르고 LLM을 호출하지 않는다(추출·설명 어느 쪽도 쓰지 않으므로 호출
    횟수 제한 대상이 아니다 - `app/main.py`의 제한 목록에도 이 경로는 없다). 세션 프로필이
    없으면 `_profile_or_guest()`로 게스트 프로필을 쓴다."""
    t0 = time.monotonic()
    n = len(body.transactions)
    masked_filename = guardrails.mask_pii(body.filename)
    t1 = time.monotonic()

    profile = _profile_or_guest()
    months = body.months or 3
    summary, features = spending_service.analyze(
        body.transactions, profile, end=date.today(), months=months,
    )
    spending_service.save(profile.id, summary, features)
    t2 = time.monotonic()

    # SPEC 2.15: 지출 절감 후보를 상환 효과로 연결해 답변에 "이렇게 연결돼요" 목록으로 붙인다.
    opportunities = spending_core.savings_opportunities(summary, features)
    linked_actions = spending_service.link_savings_to_debt(profile, opportunities, today=date.today())

    markdown = answer_service.format_spending_answer(summary, features, profile, months, linked_actions)
    banned = insights_service.get_banned_terms()
    if guardrails.check_text(markdown, banned) or any(p in markdown for p in slotfill.FORBIDDEN_PHRASES):
        markdown = "분석 요약을 표시할 수 없어 소비 패턴 화면에서 확인해 주세요."
        check_status, check_detail, answer_format = "fallback", "표현 문제가 있어 기본 안내로 바꿨어요", "text"
    else:
        check_status, check_detail, answer_format = "done", "상품 이름이나 권유 표현이 없는지 확인했어요", "markdown"
    t3 = time.monotonic()

    resources = [
        answer_service.calc_resource(
            "소비 패턴 분석", profile.id, f"{summary.period_start}~{summary.period_end}, 거래 {n}건",
        ),
        *answer_service.profile_resources(profile),
    ]
    chips = [Chip(id="chip-chat-attach-spending", text="소비 패턴 자세히 보기", tier=1, intent="spending", params={})]
    if linked_actions:
        chips.append(Chip(id="chip-chat-attach-scenario", text="시나리오로 확인하기", tier=1,
                           intent="scenario", params={}))
    action: dict[str, Any] = {"type": "open_view", "payload": {"view": "spending"}}
    trace = [
        {"id": "attach_read", "label": "파일 확인", "status": "done", "detail": f"거래 {n}건을 읽었어요",
         "ms": int((t1 - t0) * 1000), "tech": "", "steps": [f"첨부 파일에서 거래 {n}건을 확인했어요."],
         "resource_refs": []},
        {"id": "attach_calc", "label": "소비 패턴 계산", "status": "done", "detail": "카테고리·정기 결제·급증을 계산했어요",
         "ms": int((t2 - t1) * 1000), "tech": "", "steps": ["카테고리·정기 결제·급증 항목을 계산했어요."],
         "resource_refs": [r.ref for r in resources]},
        {"id": "check", "label": "마지막 점검", "status": check_status, "detail": check_detail,
         "ms": int((t3 - t2) * 1000), "tech": "", "steps": [], "resource_refs": []},
    ]

    chat_id = body.chat_id
    if chat_id:
        chat = chatlog.get_chat(chat_id)
        if chat is None or chat["profile_id"] != profile.id:
            chat_id = None
    if not chat_id:
        chat_id = chatlog.create_chat(profile.id, title=f"파일 첨부: {masked_filename[:30]}")["id"]

    chatlog.append_message(chat_id, "user", f"파일 첨부: {masked_filename[:60]} ({n}행)")
    _append_reply_message(
        chat_id, markdown, llm_used=False, action=action, chips=chips, trace=trace,
        route="internal", resources=resources, answer_format=answer_format, model=None,
    )

    return ChatReply(
        reply_text=markdown, chips=chips, action=action, llm_used=False, chat_id=chat_id, trace=trace,
        model=None, route="internal", resources=resources, answer_format=answer_format,
    )


@router.get("/chats")
def list_chats() -> list[dict[str, Any]]:
    """현재 프로필의 대화 목록(최근순)."""
    return chatlog.list_chats(_current_profile_id())


@router.post("/chats")
def create_chat(body: ChatCreateRequest) -> dict[str, Any]:
    return chatlog.create_chat(_current_profile_id(), title=body.title or "")


@router.get("/chats/{chat_id}/messages")
def chat_messages(chat_id: str) -> list[dict[str, Any]]:
    chat = chatlog.get_chat(chat_id)
    if chat is None or chat["profile_id"] != _current_profile_id():
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return chatlog.get_messages(chat_id)


@router.delete("/chats/{chat_id}")
def delete_chat(chat_id: str) -> dict[str, Any]:
    chat = chatlog.get_chat(chat_id)
    if chat is None or chat["profile_id"] != _current_profile_id():
        raise HTTPException(status_code=404, detail="대화를 찾을 수 없습니다.")
    return {"ok": chatlog.delete_chat(chat_id)}


@router.get("/kb/search")
def kb_search_endpoint(q: str, k: int = 3) -> list[dict[str, Any]]:
    """제도 안내 KB 키워드 검색(임베딩·LLM 없음)."""
    hits = kb_search.search(q, k=max(1, min(int(k), 10)))
    return [{"slug": h.slug, "title": h.title, "score": round(float(h.score), 4), "snippet": h.snippet,
             "needs_verification": h.needs_verification, "sources": h.sources} for h in hits]


@router.get("/kb/{slug}")
def kb_doc(slug: str) -> dict[str, Any]:
    for d in kb_search.load_docs():
        if d.slug == slug:
            return {"slug": d.slug, "title": d.title, "category": d.category, "keywords": d.keywords,
                    "sources": d.sources, "verified_at": str(d.verified_at), "needs_verification": d.needs_verification,
                    "sections": d.sections, "body": d.body}
    raise HTTPException(status_code=404, detail="문서를 찾을 수 없습니다.")
