"""홈 화면 인사이트 카드/칩 구성 (SPEC 2.3). LLM 호출 0회.

카드 문장(제목/본문/설명)은 모두 코드가 만든 한국어 문장이며, 상품/회사명이
섞여 들어가는 사고를 막기 위해 반환 직전에 `guardrails.check_text`로 한 번 더
검사한다(금칙어 목록은 DB의 모든 상품 회사명/상품명으로 한 번만 만들어 캐시).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.capacity import compute_capacity
from app.core.schedule import build_schedule, monthly_payment_equal
from app.core import spending as spending_core
from app.data import products
from app.llm import guardrails
from app.models import (
    Chip,
    HomePayload,
    InsightCard,
    LoanSchedule,
    PolicyParams,
    ProductCategory,
    UserProfile,
)
from app.services.actions import list_actions
from app.services.compare import LOAN_TYPE_TO_CATEGORY
from app.services import spending as spending_service

_GOAL_KIND_LABELS_KR: dict[str, str] = {
    "wedding": "결혼 자금", "childbirth": "출산·육아 자금", "housing": "주거 자금",
    "education": "교육 자금", "retirement": "노후 자금", "emergency": "비상자금", "other": "목표 자금",
}

_LOAN_TYPE_LABELS_KR: dict[str, str] = {
    "credit": "신용대출",
    "mortgage": "주택담보대출",
    "jeonse": "전세자금대출",
    "student": "학자금대출",
    "card_loan": "카드론",
    "overdraft": "마이너스통장",
    "policy": "정책상품대출",
    "other": "기타 대출",
}

# R0/R5/R6는 위험·경보성 규칙이라 카드 톤을 negative로 잡는다. R1/R2/R3/R4는
# 개선을 제안하는 규칙이라 neutral로 둔다(코드 기반의 결정론적 분류, LLM 미사용).
_NEGATIVE_RULES = {"R0", "R5", "R6"}

_banned_cache: Optional[list[str]] = None


def get_banned_terms() -> list[str]:
    """상품 회사명/상품명으로 금칙어 목록을 한 번만 만들어 캐시한다(DB 전체 카테고리).

    `app/api/routes.py`의 채팅 응답 검사에도 재사용한다.
    """
    global _banned_cache
    if _banned_cache is not None:
        return _banned_cache
    names: set[str] = set()
    for category in ProductCategory:
        try:
            rows = products.query(category)
        except Exception:  # DB 미초기화 등 방어적 처리, 카드 생성 자체를 막지 않는다
            rows = []
        for p in rows:
            for n in _company_name_variants(p.company_name):
                names.add(n)
            pn = (p.product_name or "").strip()
            # 상품명은 브랜드가 드러나는 긴 이름만 금지한다. "신용대출", "정기예금" 같은 범주 단어를
            # 금지하면 정상 문장까지 템플릿으로 대체되는 오탐이 생긴다(2026-09-06 실측).
            if pn and len(pn) >= 6 and pn not in _GENERIC_TERMS and not _is_generic_phrase(pn):
                names.add(pn)
    _banned_cache = sorted(names)
    return _banned_cache


_COMPANY_SUFFIXES = ("주식회사", "(주)", "㈜", "저축은행", "은행", "카드", "캐피탈", "보험", "생명", "화재")
_COMMON_WORD_STOPLIST = {
    "우리", "하나", "국민", "신한", "제일", "부산", "대구", "경남", "광주", "전북", "제주", "농협", "수협",
    "산업", "기업", "시티", "한국", "서울", "경기", "인천", "울산", "강원", "충북", "충남", "전남", "경북",
    "취급", "14개", "취급 은행(14개)",
}
_GENERIC_TERMS = {
    "신용대출", "정기예금", "정기적금", "자유적금", "주택담보대출", "전세자금대출", "개인신용대출", "직장인신용대출",
    "마이너스통장", "마이너스대출", "새희망홀씨", "햇살론", "사잇돌", "사잇돌2", "징검다리론", "소액생계비대출",
}
_GENERIC_TOKENS = ("신용", "대출", "예금", "적금", "직장인", "우대", "플러스", "온라인", "모바일", "스마트", "정기",
                   "자유", "주택", "담보", "전세", "자금", "개인", "사업자", "생활", "안정", "든든", "행복", "희망")


def _is_generic_phrase(name: str) -> bool:
    rest = name.replace(" ", "")
    for tok in _GENERIC_TOKENS:
        rest = rest.replace(tok, "")
    return len(rest) <= 1  # 범주 단어를 빼면 남는 게 없으면 브랜드가 없는 일반 명칭


_PUBLIC_TOKENS = ("공단", "공사", "진흥원", "위원회", "재단", "시청", "군청", "구청", "도청", "금융감독원", "금융위원회",
                  "중앙회", "정부", "지자체", "특별자치", "광역시", "특별시")
_SECTOR_WORDS = {"저축은행", "은행", "카드", "캐피탈", "보험", "증권", "취급 은행", "취급은행", "은행권", "저축은행권"}


def _company_name_variants(company_name: Optional[str]) -> list[str]:
    raw = (company_name or "").strip()
    if len(raw) < 2:
        return []
    # 공공기관·지자체·업권 일반명은 금지어가 아니다(제도 안내 문장에 정상적으로 등장한다).
    if raw in _SECTOR_WORDS or any(tok in raw for tok in _PUBLIC_TOKENS):
        return []
    out = [raw]
    cleaned = raw
    for suf in _COMPANY_SUFFIXES:
        cleaned = cleaned.replace(suf, "")
    cleaned = cleaned.strip(" ()")
    if len(cleaned) >= 3 and cleaned not in _COMMON_WORD_STOPLIST and cleaned not in _SECTOR_WORDS and cleaned != raw:
        out.append(cleaned)
    return out


def _safe_sentence(text: str, banned: list[str], fallback: str) -> str:
    return fallback if (text and guardrails.check_text(text, banned)) else text


def _guard_card(card: InsightCard, banned: list[str]) -> InsightCard:
    title = _safe_sentence(card.title, banned, "안내")
    body = _safe_sentence(card.body, banned, "표시할 수 없는 내용이 감지되어 일반 안내로 대체했습니다.")
    explain_fallback = "규칙 기반 계산 결과입니다." if card.explain else ""
    explain = _safe_sentence(card.explain, banned, explain_fallback)
    if title != card.title or body != card.body or explain != card.explain:
        card = card.model_copy(update={"title": title, "body": body, "explain": explain})
    return card


def _onboarding_cards(product_stats: dict) -> list[InsightCard]:
    example_payment = monthly_payment_equal(30_000_000, 6.0, 36)
    card1 = InsightCard(
        id="card-onboarding-preview",
        kind="onboarding",
        tone="neutral",
        title="부채를 등록하면 이렇게 계산돼요",
        body=(
            f"예를 들어 신용대출 30,000,000원, 금리 연 6.0%, 36개월이라면 월 상환액은 약 "
            f"{example_payment:,}원입니다. 내 대출을 등록하면 실제 숫자로 다시 계산해드려요."
        ),
        evidence={"예시 금액": "30,000,000원", "예시 금리": "6.0%", "예시 월 상환액": f"{example_payment:,}원"},
        chip=Chip(id="chip-onboarding-debts", text="내 부채 입력하기", tier=0, intent="onboarding", params={}),
        source_rule="onboarding",
        explain="실제 값이 아니라 계산 엔진(app.core.schedule)으로 만든 예시 가정입니다.",
    )
    total_products = int(product_stats.get("total_products") or 0)
    products_note = f" 지금 비교할 수 있는 공시 상품은 {total_products:,}개입니다." if total_products > 0 else ""
    card2 = InsightCard(
        id="card-onboarding-personas",
        kind="onboarding",
        tone="neutral",
        title="페르소나로 먼저 체험해볼 수 있어요",
        body=(
            "8명의 데모 페르소나 중 하나를 선택하면 실제 데이터로 홈 화면과 행동 제안을 바로 확인할 수 있어요."
            f"{products_note}"
        ),
        evidence={"데모 페르소나 수": "8명", "비교 가능 상품 수": f"{total_products:,}개"},
        chip=Chip(id="chip-onboarding-personas", text="페르소나 선택하러 가기", tier=0, intent="onboarding", params={}),
        source_rule="onboarding",
        explain="등록된 데모 페르소나 수와 상품 스냅샷 통계(app.data.products.stats)를 안내합니다.",
    )
    return [card1, card2]


def _onboarding_chips() -> list[Chip]:
    return [
        Chip(id="chip-tier0-personas", text="페르소나로 체험하기", tier=0, intent="onboarding", params={}),
        Chip(id="chip-tier0-compare-credit", text="신용대출 공시 비교하기", tier=0, intent="compare",
             params={"category": "credit"}),
        Chip(id="chip-tier0-compare-mortgage", text="주택담보대출 공시 비교하기", tier=0, intent="compare",
             params={"category": "mortgage"}),
        Chip(id="chip-tier0-compare-jeonse", text="전세자금대출 공시 비교하기", tier=0, intent="compare",
             params={"category": "jeonse"}),
        Chip(id="chip-tier0-debts", text="내 부채 직접 입력하기", tier=0, intent="onboarding", params={}),
    ]


def _debt_summary_card(profile: UserProfile, schedules: list[LoanSchedule]) -> InsightCard:
    total_balance = sum(l.balance for l in profile.loans)
    monthly_total = sum(s.first_payment for s in schedules)
    total_remaining_interest = sum(s.total_interest for s in schedules)
    if profile.loans:
        body = (
            f"현재 등록된 대출 {len(profile.loans)}건의 총잔액은 {total_balance:,}원이고, "
            f"이번 달 원리금 합계는 {monthly_total:,}원입니다."
        )
    else:
        body = "아직 등록된 대출이 없습니다. 대출을 등록하면 총잔액과 월 상환액을 계산해드려요."
    return InsightCard(
        id="card-debt-summary",
        kind="debt",
        tone="neutral",
        title="내 부채 한눈에 보기",
        body=body,
        evidence={
            "총잔액": f"{total_balance:,}원",
            "월 원리금": f"{monthly_total:,}원",
            "남은 총이자": f"{total_remaining_interest:,}원",
        },
        chip=Chip(id="chip-debt-schedule", text="상환표 보기", tier=1, intent="schedule", params={}),
        source_rule="debt_summary",
        explain="보유 대출별 상환 스케줄(app.core.schedule)을 계산해 합산했습니다.",
    )


def _progress_card(profile: UserProfile, schedules: list[LoanSchedule]) -> Optional[InsightCard]:
    if not schedules:
        return None
    soonest = min(schedules, key=lambda s: (s.months, s.loan_id))
    loan_obj = next((l for l in profile.loans if l.id == soonest.loan_id), None)
    label = _LOAN_TYPE_LABELS_KR.get(loan_obj.loan_type.value, "대출") if loan_obj else "대출"
    if soonest.months <= 0:
        body = f"{label}은(는) 이미 상환이 끝났거나 이번 달 안에 정리됩니다."
    else:
        body = f"{label}이(가) {soonest.months}개월 후 상환 완료될 예정입니다(현재 조건 유지 시)."
    return InsightCard(
        id="card-progress-soonest",
        kind="progress",
        tone="positive",
        title="가장 먼저 끝나는 대출",
        body=body,
        evidence={"남은 개월": f"{soonest.months}개월"},
        chip=Chip(
            id="chip-progress-schedule", text="상환표 자세히 보기", tier=1, intent="schedule",
            params={"target_loan_id": soonest.loan_id},
        ),
        source_rule="progress_soonest",
        explain="각 대출의 상환 스케줄에서 남은 회차가 가장 적은 대출을 찾았습니다.",
    )


def _goal_progress_card(profile: UserProfile) -> Optional[InsightCard]:
    """가장 우선순위가 높은(priority 오름차순, 동률이면 target_date, id 순) 목표의 저축
    진행률을 "목표 금액의 N% 확보" 프레이밍으로 보여준다(문서 4.5절, 손실 경고 대신 진행률).
    kind="progress"로 둬서 소비 패턴 카드의 자리 확보 로직(진행 카드부터 제거)과 같은
    방식으로 카드 총량 상한(3장)을 지킨다."""
    if not profile.goals:
        return None
    goal = sorted(profile.goals, key=lambda g: (g.priority, g.target_date, g.id))[0]
    if goal.target_amount <= 0:
        return None
    pct = min(round(goal.saved_amount / goal.target_amount * 100), 100)
    label = _GOAL_KIND_LABELS_KR.get(goal.kind, "목표 자금")
    body = (
        f"목표 {label}의 {pct}% 확보({goal.saved_amount:,}원 / {goal.target_amount:,}원)."
    )
    return InsightCard(
        id=f"card-goal-{goal.id}",
        kind="progress",
        tone="positive" if pct >= 50 else "neutral",
        title=f"{goal.label} 목표 진행률",
        body=body,
        evidence={"목표 금액": f"{goal.target_amount:,}원", "모은 금액": f"{goal.saved_amount:,}원", "진행률": f"{pct}%"},
        chip=Chip(id="chip-goal-lifecycle", text="생애 흐름 보기", tier=1, intent="lifecycle", params={}),
        source_rule="goal_progress",
        explain="가장 우선순위가 높은 목표(Goal.priority, target_date 순)의 저축 진행률을 계산했습니다.",
    )


def _derive_tier1_chips(profile: UserProfile, top_action) -> list[Chip]:
    chips: list[Chip] = []
    seen_ids: set[str] = set()

    def _add(chip: Optional[Chip]) -> None:
        if chip is not None and chip.id not in seen_ids:
            chips.append(chip)
            seen_ids.add(chip.id)

    if top_action is not None:
        _add(top_action.chip)
    _add(Chip(id="chip-tier1-schedule", text="상환표 보기", tier=1, intent="schedule", params={}))
    _add(Chip(id="chip-tier1-scenario", text="시나리오로 확인하기", tier=1, intent="scenario", params={}))
    if profile.loans:
        highest = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
        category = LOAN_TYPE_TO_CATEGORY.get(highest.loan_type, ProductCategory.CREDIT)
        _add(Chip(
            id="chip-tier1-compare", text="공시 비교해보기", tier=1, intent="compare",
            params={"category": category.value, "target_loan_id": highest.id},
        ))
    return chips[:5]


def build_home(
    profile: Optional[UserProfile], product_stats: dict, params: PolicyParams, *, today: date
) -> HomePayload:
    """SPEC 2.3: 프로필 없음 -> 온보딩 카드 2장 + Tier0 칩 5개. 프로필 있음 -> 데이터
    파생 카드(행동/부채요약/진행) + Tier1 칩. LLM 호출은 절대 하지 않는다(llm_calls=0)."""
    banned = get_banned_terms()

    if profile is None:
        cards = [_guard_card(c, banned) for c in _onboarding_cards(product_stats or {})]
        return HomePayload(
            profile_id=None, cards=cards, chips=_onboarding_chips(),
            capacity=None, top_action=None, llm_calls=0,
        )

    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    action_cards = list_actions(profile, params, today=today)
    top_action = action_cards[0] if action_cards else None

    base_cards: list[InsightCard] = []
    if top_action is not None:
        tone = "negative" if (top_action.safe_mode or top_action.rule_id in _NEGATIVE_RULES) else "neutral"
        base_cards.append(InsightCard(
            id=f"card-action-{top_action.id}",
            kind="action",
            tone=tone,
            title=top_action.title,
            body=top_action.summary,
            evidence=dict(top_action.numbers),  # actions.list_actions에서 이미 한글 라벨로 포맷됨
            chip=top_action.chip,
            source_rule=top_action.rule_id,
            explain=f"{top_action.rule_id} 규칙이 대출 정보와 이번 달 여력({capacity.band.value})을 근거로 판단했습니다.",
        ))

    base_cards.append(_debt_summary_card(profile, schedules))

    # 목표 진행률 카드(P7)가 대출 진행 카드보다 우선한다: 자리가 하나뿐이면 목표 카드를 먼저
    # 채운다(둘 다 kind="progress"라 아래 소비 패턴 자리 확보 로직도 그대로 적용된다).
    optional_cards: list[InsightCard] = []
    goal_card = _goal_progress_card(profile)
    if goal_card is not None:
        optional_cards.append(goal_card)
    progress_card = _progress_card(profile, schedules)
    if progress_card is not None:
        optional_cards.append(progress_card)

    room = 3 - len(base_cards)
    cards: list[InsightCard] = base_cards + optional_cards[: max(room, 0)]

    # 균형 규칙: negative 톤만 있으면 neutral/positive 카드 1장을 추가한다.
    if cards and all(c.tone == "negative" for c in cards):
        cards.append(InsightCard(
            id="card-balance-note",
            kind="progress",
            tone="neutral",
            title="지금 할 수 있는 것부터 정리해봐요",
            body=f"이번 달 남는 돈은 {capacity.net_monthly:,}원입니다. 위 안내부터 하나씩 확인해보세요.",
            evidence={"이번 달 남는 돈": f"{capacity.net_monthly:,}원"},
            chip=None,
            source_rule="balance_rule",
            explain="부정적인 톤의 카드만 있을 때 균형을 맞추기 위해 추가된 안내입니다.",
        ))

    # P5: 저장된 소비 패턴 분석이 있으면 최대 2장을 더한다. 홈 카드는 최대 3장(SPEC 3장)
    # 이므로 자리가 모자라면 progress 카드부터 빼서 자리를 만든다.
    spending_loaded = spending_service.load(profile.id)
    spending_cards: list[InsightCard] = []
    if spending_loaded is not None:
        s_summary, s_features = spending_loaded
        spending_cards = spending_core.build_spending_cards(s_features, s_summary, profile)[:2]

    if spending_cards:
        room = 3 - len(cards)
        if room < len(spending_cards):
            cards = [c for c in cards if c.kind != "progress"]
            room = 3 - len(cards)
        if room > 0:
            cards.extend(spending_cards[:room])

    cards = [_guard_card(c, banned) for c in cards]
    chips = _derive_tier1_chips(profile, top_action)
    if spending_loaded is not None:
        spending_chip = Chip(id="chip-tier1-spending", text="소비 패턴 보기", tier=1, intent="spending", params={})
        if spending_chip.id not in {c.id for c in chips}:
            chips = chips[:4] + [spending_chip]

    return HomePayload(
        profile_id=profile.id, cards=cards, chips=chips,
        capacity=capacity, top_action=top_action, llm_calls=0,
    )
