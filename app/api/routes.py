"""DONN PoC API 라우트 (SPEC 2.5). main.py가 prefix="/api"로 include한다."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Response

from app.api.schemas import (ChatCreateRequest,
    ChatRequest,
    ComparePrepareRequest,
    MetaResponse,
    OkResponse,
    PersonaSummary,
    ReplayResponse,
    SpendingAnalyzeRequest,
    SpendingAnalyzeSyntheticRequest,
)
from app.core.schedule import build_schedule
from app.core.scenarios import run_scenarios
from app.core import lifecycle as lifecycle_core
from app.core import ratios as ratios_core
from app.core import retirement as retirement_core
from app.core import spending as spending_core
from app.data import policy, products, synthetic
from app.data.finlife import CRDT_GRADE_LABELS
from app.llm import guardrails
from app.llm.gemini import GeminiProvider
from app.llm.provider import LLMUnavailable
from app.services import chatlog
from app import kb as kb_search
from app.models import (
    ENGINE_VERSION,
    ActionCard,
    ChatReply,
    Chip,
    CompareContext,
    CompareResult,
    DecisionRecord,
    HomePayload,
    LenderGroup,
    LifecycleView,
    LoanSchedule,
    ProductCategory,
    RepayMethod,
    ScenarioResult,
    SortKey,
    UserProfile,
)
from app.services import actions as actions_service
from app.services import compare as compare_service
from app.services import decisions as decisions_service
from app.services import insights as insights_service
from app.services import lifecycle as lifecycle_service
from app.services import session as session_service
from app.services import spending as spending_service

router = APIRouter()

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
def get_loan_schedule(loan_id: str, extra: int = 0) -> LoanSchedule:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    loan = next((l for l in profile.loans if l.id == loan_id), None)
    if loan is None:
        raise HTTPException(status_code=404, detail=f"대출을 찾을 수 없습니다: {loan_id}")
    return build_schedule(loan, extra_payment=max(extra, 0))


@router.get("/scenarios", response_model=list[ScenarioResult])
def get_scenarios(horizon: int = 60) -> list[ScenarioResult]:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    return run_scenarios(profile, params, horizon_months=horizon)


@router.get("/actions", response_model=list[ActionCard])
def get_actions() -> list[ActionCard]:
    profile = session_service.get_profile()
    if profile is None:
        raise HTTPException(status_code=404, detail="저장된 프로필이 없습니다.")
    params = policy.load_policy_params()
    return actions_service.list_actions(profile, params, today=date.today())


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
def post_compare_run(ctx: CompareContext) -> CompareResult:
    profile = session_service.get_profile()
    try:
        return compare_service.run_compare(ctx, profile, today=date.today())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


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
    return {
        "summary": summary.model_dump(mode="json"),
        "features": features.model_dump(mode="json"),
        "cards": [c.model_dump(mode="json") for c in cards],
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
            "retirement", "saving", "liquidity",
        ]},
        "category": {"type": "STRING", "enum": ["deposit", "saving", "mortgage", "jeonse", "credit", "policy"]},
        "amount": {"type": "INTEGER"},
        "term_months": {"type": "INTEGER"},
        "max_rate": {"type": "NUMBER"},
        "exclude_companies": {"type": "ARRAY", "items": {"type": "STRING"}},
        "sort_key": {"type": "STRING", "enum": ["total_cost", "monthly_payment", "rate"]},
    },
    "required": ["intent"],
}

_CATEGORY_LABELS_KR = {
    "deposit": "예금", "saving": "적금", "mortgage": "주택담보대출",
    "jeonse": "전세자금대출", "credit": "신용대출", "policy": "정책상품",
}

_VALID_CHAT_INTENTS = {
    "compare", "schedule", "scenario", "action", "faq", "spending",
    "retirement", "saving", "liquidity",
}


def _normalize_intent(value: Any) -> str:
    return value if isinstance(value, str) and value in _VALID_CHAT_INTENTS else "faq"


def _clean_compare_params(slots: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for key in ("category", "amount", "term_months", "max_rate", "exclude_companies", "sort_key"):
        value = slots.get(key)
        if value not in (None, "", []):
            params[key] = value
    return params


def _ground_numeric_slots(slots: dict[str, Any], text: str) -> dict[str, Any]:
    """LLM이 발화에 없는 숫자를 지어내지 못하게 막는다(2026-09-06 실측: 금액 없는 발화에 3천만 원이 채워짐).

    규칙 파서가 같은 필드를 찾으면 그 값을 우선하고, 규칙 파서가 못 찾은 숫자는 발화에 정수부가
    그대로 등장할 때만 남긴다. 숫자는 결정론 코드의 입력이므로 근거 없는 값은 버린다.
    """
    rule = guardrails.parse_message(text)
    norm_text = text.replace(",", "")
    for key in ("amount", "term_months", "max_rate"):
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
            reply_text = (
                f"기준 시나리오 기준 은퇴 시점({base.retirement_age}세) 생활비는 월 "
                f"{base.retirement_living_cost:,}원, 확정소득은 월 {base.guaranteed_income_monthly:,}원으로 "
                f"월 {base.monthly_gap:,}원이 부족할 것으로 추정됩니다. 필요자금 대비 {base.shortfall:,}원이 "
                f"모자라 매월 {base.required_monthly_saving:,}원을 추가로 저축하는 방법이 안내됩니다."
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


def _build_chat_reply(message: str) -> tuple[str, list[Chip], Optional[dict[str, Any]], bool]:
    masked = guardrails.mask_pii(message)

    slots: dict[str, Any] = {}
    llm_used = False
    if _llm_provider.available():
        try:
            result = _llm_provider.extract(masked, _CHAT_EXTRACT_SCHEMA, _CHAT_EXTRACT_SYSTEM)
            if isinstance(result.data, dict) and result.data.get("intent"):
                slots = result.data
                llm_used = True
        except LLMUnavailable:
            slots = {}

    if not slots:
        slots = guardrails.parse_message(masked)
    elif llm_used:
        slots = _ground_numeric_slots(slots, masked)

    intent = _normalize_intent(slots.get("intent"))
    profile = session_service.get_profile()
    chips: list[Chip] = []
    action: Optional[dict[str, Any]] = None

    if intent == "compare":
        params = _clean_compare_params(slots)
        ctx = compare_service.prepare_context(profile, params)
        category_label = _CATEGORY_LABELS_KR.get(ctx.category.value, ctx.category.value)
        reply_text = (
            f"{category_label} 비교 조건을 준비했어요. 금액 {ctx.amount:,}원, 기간 {ctx.term_months}개월 "
            "기준입니다. 공시 비교 화면에서 조건을 확인하고 실행해보세요."
        )
        action = {"type": "prepare_compare", "payload": {"params": ctx.model_dump(mode="json")}}

    elif intent in ("schedule", "scenario"):
        reply_text = "내 부채 화면에서 상환표와 시나리오를 확인할 수 있어요."
        action = {"type": "open_view", "payload": {"view": "debts"}}
        chips.append(Chip(id=f"chip-chat-{intent}", text="내 부채로 이동", tier=1, intent=intent, params={}))

    elif intent == "spending":
        reply_text = "소비 패턴 화면에서 합성 거래내역을 확인할 수 있어요."
        action = {"type": "open_view", "payload": {"view": "spending"}}

    elif intent in ("retirement", "saving", "liquidity"):
        reply_text, chips, action = _build_lifecycle_chat_reply(intent, profile)

    elif intent == "action":
        if profile is None:
            reply_text = (
                "아직 프로필이 없어요. 페르소나를 선택하거나 내 부채 화면에서 정보를 입력하면 "
                "맞춤 행동을 보여드릴게요."
            )
            chips.append(Chip(id="chip-chat-onboarding", text="페르소나 선택하러 가기", tier=1,
                               intent="onboarding", params={}))
        else:
            params_policy = policy.load_policy_params()
            cards = actions_service.list_actions(profile, params_policy, today=date.today())
            if cards:
                top = cards[0]
                reply_text = top.summary
                if top.chip is not None:
                    chips.append(top.chip)
            else:
                reply_text = "지금은 특별히 안내할 행동이 없어요. 계속 잘 관리하고 계세요."

    else:  # faq 또는 인식하지 못한 의도: 제도 안내 KB(kb/*.md) 키워드 검색으로 답한다
        hit = kb_search.answer(masked)
        if hit is not None:
            reply_text = f"{hit['title']} 안내입니다. {hit['snippet']}"
            if hit.get("needs_verification"):
                reply_text += " 일부 수치는 확인이 필요한 항목입니다."
            reply_text += f" {hit['disclaimer']}"
            chips.append(Chip(id=f"chip-kb-{hit['slug']}", text=f"{hit['title']} 자세히 보기", tier=1,
                              intent="faq", params={"slug": hit["slug"]}))
            action = {"type": "open_kb", "payload": {"slug": hit["slug"], "title": hit["title"],
                                                    "sources": hit.get("sources") or []}}
        else:
            reply_text = (
                "부채 상환표, 공시 비교, 시나리오, 행동 제안, 제도 안내 중 무엇이든 물어보세요. "
                "예: '신용대출 공시 비교해줘', '금리 4% 이하만', '금리인하요구권 요건이 뭐야'"
            )
        chips.extend([
            Chip(id="chip-chat-faq-compare", text="공시 비교하기", tier=1, intent="compare", params={}),
            Chip(id="chip-chat-faq-debts", text="내 부채 보기", tier=1, intent="schedule", params={}),
            Chip(id="chip-chat-faq-spending", text="소비 패턴 보기", tier=1, intent="spending", params={}),
        ])

    banned = insights_service.get_banned_terms()
    kb_reply = bool(action and action.get("type") == "open_kb")  # KB 문서는 작성 시 금지어 검사를 통과한 텍스트
    if not kb_reply and guardrails.check_text(reply_text, banned):
        reply_text = "안내 문구를 표시할 수 없어 기본 안내로 대체했습니다. 메뉴에서 원하는 화면을 선택해주세요."

    return reply_text, chips, action, llm_used


def _current_profile_id() -> str:
    profile = session_service.get_profile()
    return profile.id if profile is not None else chatlog.GUEST_PROFILE_ID


@router.post("/chat", response_model=ChatReply)
def post_chat(body: ChatRequest) -> ChatReply:
    """대화 로그는 현재 프로필(페르소나)에 종속된다. chat_id가 없거나 다른 프로필 것이면 새 대화를 만든다."""
    profile_id = _current_profile_id()
    chat_id = body.chat_id
    if chat_id:
        chat = chatlog.get_chat(chat_id)
        if chat is None or chat["profile_id"] != profile_id:
            chat_id = None
    if not chat_id:
        chat_id = chatlog.create_chat(profile_id, title=body.message.strip()[:30])["id"]
    chatlog.append_message(chat_id, "user", guardrails.mask_pii(body.message))
    reply_text, chips, action, llm_used = _build_chat_reply(body.message)
    chatlog.append_message(chat_id, "reply", reply_text, llm_used=llm_used, action=action,
                           chips=[c.model_dump(mode="json") for c in chips])
    return ChatReply(reply_text=reply_text, chips=chips, action=action, llm_used=llm_used, chat_id=chat_id)


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
