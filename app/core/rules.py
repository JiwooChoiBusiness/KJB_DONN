"""행동 카드 룰 엔진 R0~R6 (순수 함수).

I/O 없음, 오늘 날짜는 인자 `today`로만 받는다. app.models와
app.core.loan/schedule(같은 패키지)만 import한다.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from app.core.loan import extra_payment_effect, prepay_fee, refinance_compare
from app.models import (
    ActionCard,
    Capacity,
    CapacityBand,
    Chip,
    Loan,
    LoanSchedule,
    LoanType,
    PolicyParams,
    ProductCategory,
    UserProfile,
)

_COUNSELING_STEP = "오늘 신용회복위원회(1600-5500) 또는 서민금융진흥원(국번없이 1397)에 상담을 신청하세요."

_CATEGORY_BY_LOAN_TYPE = {
    LoanType.CREDIT: ProductCategory.CREDIT,
    LoanType.CARD_LOAN: ProductCategory.CREDIT,
    LoanType.OVERDRAFT: ProductCategory.CREDIT,
    LoanType.MORTGAGE: ProductCategory.MORTGAGE,
    LoanType.JEONSE: ProductCategory.JEONSE,
    LoanType.STUDENT: ProductCategory.CREDIT,
    LoanType.POLICY: ProductCategory.POLICY,
    LoanType.OTHER: ProductCategory.CREDIT,
}


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _policy(params: PolicyParams, key: str, default: Any) -> tuple[Any, bool]:
    p = params.params.get(key)
    if p is None:
        return default, True
    return p.value, p.needs_verification


def _sorted_loans(profile: UserProfile) -> list[Loan]:
    return sorted(profile.loans, key=lambda l: l.id)


def _verify_note(needs_verification: bool) -> str:
    return "확인 필요" if needs_verification else "확인됨"


def _rule_r0(profile: UserProfile, capacity: Capacity) -> Optional[ActionCard]:
    triggered = (
        "delinquency_signal" in profile.flags
        or capacity.band == CapacityBand.NEGATIVE
        or capacity.debt_service_ratio >= 0.7
    )
    if not triggered:
        return None
    ratio_pct = _round_half_up(capacity.debt_service_ratio * 100)
    summary = (
        f"현재 부채 상환 비율이 소득의 {ratio_pct}%이고 이번 달 남는 돈이 {capacity.net_monthly:,}원이라 "
        "공적 상담 창구를 먼저 이용하는 것이 안전합니다."
    )
    return ActionCard(
        id="action-r0-safe-mode",
        rule_id="R0",
        title="안전 모드: 공적 상담부터 시작하세요",
        summary=summary,
        numbers={
            "debt_service_ratio": capacity.debt_service_ratio,
            "net_monthly": capacity.net_monthly,
        },
        assumptions=["안전 모드 판단 기준: 연체 신호, 상환비율 70% 이상, 또는 이번 달 남는 돈이 음수 중 하나 이상 해당"],
        caveats=["이 안내는 상담 신청을 돕기 위한 정보이며 특정 상품 가입을 유도하지 않습니다."],
        steps=[_COUNSELING_STEP],
        priority=0,
        safe_mode=True,
        related_loan_ids=[l.id for l in profile.loans],
        chip=None,
    )


def _rule_r5(loan: Loan, capacity: Capacity, schedule_by_loan: dict[str, LoanSchedule]) -> Optional[ActionCard]:
    near_maturity = loan.repay_method.value in ("bullet", "revolving") and loan.remaining_months <= 3
    grace_ending = 1 <= loan.grace_months <= 2
    if not (near_maturity or grace_ending):
        return None
    sched = schedule_by_loan.get(loan.id)
    payoff_amount = sched.rows[-1].payment if sched and sched.rows else loan.balance
    summary = (
        f"{'만기' if near_maturity else '거치 종료'}가 가까운 대출이 있습니다. "
        f"예상 정리 금액은 {payoff_amount:,}원이고 이번 달 남는 돈은 {capacity.net_monthly:,}원입니다."
    )
    return ActionCard(
        id=f"action-r5-{loan.id}",
        rule_id="R5",
        title="만기·거치 종료가 가까운 대출을 확인하세요",
        summary=summary,
        numbers={
            "payoff_amount": payoff_amount,
            "remaining_months": loan.remaining_months,
            "grace_months": loan.grace_months,
            "net_monthly": capacity.net_monthly,
        },
        assumptions=["정리 금액은 현재 스케줄의 마지막 회차 납입액 기준입니다."],
        caveats=["실제 정리 시점 금리와 잔액에 따라 금액이 달라질 수 있습니다."],
        steps=["만기 전 상환 계획(재대출, 일시 상환, 거치 연장 여부)을 미리 확인하세요."],
        priority=10,
        safe_mode=False,
        related_loan_ids=[loan.id],
        chip=None,
    )


def _rule_r4(profile: UserProfile, params: PolicyParams) -> Optional[ActionCard]:
    months, needs_verification = _policy(params, "emergency_fund_months", 1)
    target = profile.fixed_expenses * months
    if profile.emergency_fund >= target:
        return None
    gap = target - profile.emergency_fund
    summary = (
        f"비상금이 {profile.emergency_fund:,}원으로 생활비 {months}개월분({target:,}원)에 못 미칩니다. "
        f"{gap:,}원을 먼저 모으는 방법을 생각해보세요."
    )
    return ActionCard(
        id="action-r4-emergency-fund",
        rule_id="R4",
        title="비상금부터 채우세요",
        summary=summary,
        numbers={
            "emergency_fund": profile.emergency_fund,
            "target_emergency_fund": target,
            "gap": gap,
        },
        assumptions=[f"policy: emergency_fund_months={months}개월 ({_verify_note(needs_verification)})"],
        caveats=[],
        steps=["매월 여유자금 일부를 별도 비상금 계좌로 먼저 옮겨 두세요."],
        priority=20,
        safe_mode=False,
        related_loan_ids=[],
        chip=None,
    )


def _rule_r6(loan: Loan, profile: UserProfile) -> Optional[ActionCard]:
    if loan.loan_type.value not in ("card_loan", "overdraft"):
        return None
    if loan.annual_rate < 12 or loan.balance > profile.monthly_income * 2:
        return None
    summary = (
        f"금리 {loan.annual_rate:.4g}%, 잔액 {loan.balance:,}원인 고금리 소액 대출이 있습니다. "
        "다른 대출보다 먼저 줄이는 것을 고려하세요."
    )
    return ActionCard(
        id=f"action-r6-{loan.id}",
        rule_id="R6",
        title="고금리 소액 대출을 먼저 줄이세요",
        summary=summary,
        numbers={
            "balance": loan.balance,
            "rate": loan.annual_rate,
            "monthly_income_x2": profile.monthly_income * 2,
        },
        assumptions=["고금리 소액 기준: 금리 연 12% 이상이며 잔액이 월소득의 2배 이하"],
        caveats=[],
        steps=["여유자금이 생기면 이 대출부터 우선 상환하세요."],
        priority=30,
        safe_mode=False,
        related_loan_ids=[loan.id],
        chip=None,
    )


def _rule_r2(profile: UserProfile, capacity: Capacity, priority: int) -> Optional[ActionCard]:
    if len(profile.loans) < 2 or capacity.net_monthly <= 0:
        return None
    target = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
    effect = extra_payment_effect(target, capacity.net_monthly)
    summary = (
        f"이번 달 남는 돈 {capacity.net_monthly:,}원을 금리 {target.annual_rate:.4g}%인 대출에 전액 추가 상환하면 "
        f"{effect['months_saved']}개월 빨리 끝내고 이자 {effect['interest_saved']:,}원을 아낄 수 있습니다."
    )
    return ActionCard(
        id=f"action-r2-{target.id}",
        rule_id="R2",
        title="최고금리 대출부터 추가 상환하세요",
        summary=summary,
        numbers={
            "extra_monthly": capacity.net_monthly,
            "target_rate": target.annual_rate,
            "months_saved": effect["months_saved"],
            "interest_saved": effect["interest_saved"],
            "new_months": effect["new_months"],
        },
        assumptions=["월 여력 전액을 매월 동일하게 추가 상환한다고 가정했습니다."],
        caveats=["중도상환수수료가 있는 대출은 수수료를 먼저 확인하세요."],
        steps=["이번 달부터 여유자금을 해당 대출 원금 상환에 우선 배정하세요."],
        priority=priority,
        safe_mode=False,
        related_loan_ids=[target.id],
        chip=None,
    )


def _rule_r1(loan: Loan, profile: UserProfile, params: PolicyParams) -> Optional[ActionCard]:
    if loan.loan_type.value not in ("credit", "card_loan", "overdraft"):
        return None
    min_rate, needs_verification = _policy(params, "rate_cut_request_min_rate", 6.0)
    if loan.annual_rate < min_rate:
        return None
    if not ({"income_up", "job_changed"} & set(profile.flags)):
        return None
    summary = (
        f"금리 {loan.annual_rate:.4g}%인 대출은 금리인하요구권 신청 대상일 수 있습니다. "
        "비용 없이 신청할 수 있습니다."
    )
    return ActionCard(
        id=f"action-r1-{loan.id}",
        rule_id="R1",
        title="금리인하요구권을 신청해보세요",
        summary=summary,
        numbers={"current_rate": loan.annual_rate, "balance": loan.balance, "cost": 0},
        assumptions=[f"policy: rate_cut_request_min_rate={min_rate}% ({_verify_note(needs_verification)})"],
        caveats=["심사 결과에 따라 인하가 거절될 수 있습니다."],
        steps=[
            "1. 소득 증가 또는 고용 변동 증빙 서류를 준비하세요.",
            "2. 대출 취급 금융회사 창구 또는 앱에서 금리인하요구권을 신청하세요.",
            "3. 심사 결과를 통보받고 인하 여부를 확인하세요.",
        ],
        priority=50,
        safe_mode=False,
        related_loan_ids=[loan.id],
        chip=None,
    )


def _rule_r3(loan: Loan, params: PolicyParams, today: date) -> Optional[ActionCard]:
    if loan.remaining_months < 12 or loan.annual_rate < 7.0:
        return None
    refi_gap, needs_verification = _policy(params, "refi_rate_gap_min_pct", 1.0)
    monthly_interest_saving = _round_half_up(loan.balance * refi_gap / 100 / 12)
    if monthly_interest_saving <= 0:
        return None
    fee = prepay_fee(loan, loan.balance, today)
    breakeven_months = -(-fee // monthly_interest_saving) if fee > 0 else 0
    if breakeven_months >= loan.remaining_months:
        return None

    category = _CATEGORY_BY_LOAN_TYPE.get(loan.loan_type, ProductCategory.CREDIT)
    summary = (
        f"현재 금리 {loan.annual_rate:.4g}%, 잔여 {loan.remaining_months}개월 남은 대출입니다. "
        "내 조건으로 다른 상품 금리를 비교해볼 수 있습니다."
    )
    chip = Chip(
        id=f"chip-r3-compare-{loan.id}",
        text="내 조건으로 공시 비교하기",
        tier=1,
        intent="compare",
        params={
            "category": category.value,
            "amount": loan.balance,
            "term_months": loan.remaining_months,
            "target_loan_id": loan.id,
        },
    )
    return ActionCard(
        id=f"action-r3-{loan.id}",
        rule_id="R3",
        title="대환 후보: 공시 금리를 비교해보세요",
        summary=summary,
        numbers={
            "current_rate": loan.annual_rate,
            "assumed_rate_gap": refi_gap,
            "monthly_interest_saving": monthly_interest_saving,
            "prepay_fee": fee,
            "breakeven_months": breakeven_months,
        },
        assumptions=[
            f"policy: refi_rate_gap_min_pct={refi_gap}%p ({_verify_note(needs_verification)}), "
            "이 값만큼 금리가 낮아진다고 가정해 월 이자 절감을 추정했습니다.",
            "중도상환수수료는 loan.py의 prepay_fee 계산값입니다.",
        ],
        caveats=["실제 승인 금리와 한도는 금융회사 심사에 따라 다를 수 있습니다."],
        steps=[],
        priority=60,
        safe_mode=False,
        related_loan_ids=[loan.id],
        chip=chip,
    )


def evaluate_rules(
    profile: UserProfile,
    schedules: list[LoanSchedule],
    capacity: Capacity,
    params: PolicyParams,
    *,
    today: date,
) -> list[ActionCard]:
    """R0~R6 규칙을 평가해 priority 오름차순으로 정렬된 ActionCard 목록을 반환한다."""
    cards: list[ActionCard] = []
    schedule_by_loan = {s.loan_id: s for s in schedules}

    r0 = _rule_r0(profile, capacity)
    if r0 is not None:
        cards.append(r0)
    safe_mode_active = r0 is not None

    for loan in _sorted_loans(profile):
        r5 = _rule_r5(loan, capacity, schedule_by_loan)
        if r5 is not None:
            cards.append(r5)

    r4 = _rule_r4(profile, params)
    if r4 is not None:
        cards.append(r4)

    for loan in _sorted_loans(profile):
        r6 = _rule_r6(loan, profile)
        if r6 is not None:
            cards.append(r6)

    if not safe_mode_active:
        r2_priority = 45 if r4 is not None else 40
        r2 = _rule_r2(profile, capacity, r2_priority)
        if r2 is not None:
            cards.append(r2)

    for loan in _sorted_loans(profile):
        r1 = _rule_r1(loan, profile, params)
        if r1 is not None:
            cards.append(r1)

    if not safe_mode_active:
        for loan in _sorted_loans(profile):
            r3 = _rule_r3(loan, params, today)
            if r3 is not None:
                cards.append(r3)

    cards.sort(key=lambda c: (c.priority, c.id))
    return cards
