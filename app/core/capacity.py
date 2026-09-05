"""가용 여력(Capacity) 계산 (순수 함수). I/O 없음, app.models만 import한다."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.models import Capacity, CapacityBand, LoanSchedule, UserProfile


def _round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def compute_capacity(profile: UserProfile, schedules: list[LoanSchedule]) -> Capacity:
    """월 가용 여력 계산.

    `debt_service = Σ first_payment`, `net = income - fixed - variable - debt_service`,
    `ratio = debt_service / income`.
    band: net < 0 → NEGATIVE; ratio ≥ 0.5 또는 net < 0.05*income → TIGHT;
    net ≥ 0.2*income → COMFORTABLE; 그 외 OK.
    """
    debt_service = sum(s.first_payment for s in schedules)
    income = profile.monthly_income
    net = income - profile.fixed_expenses - profile.variable_expenses - debt_service

    if income > 0:
        ratio = debt_service / income
    else:
        ratio = 1.0 if debt_service > 0 else 0.0

    if net < 0:
        band = CapacityBand.NEGATIVE
    elif ratio >= 0.5 or net < 0.05 * income:
        band = CapacityBand.TIGHT
    elif net >= 0.2 * income:
        band = CapacityBand.COMFORTABLE
    else:
        band = CapacityBand.OK

    ratio_pct = _round_half_up(ratio * 100)
    if net >= 0:
        explanation = (
            f"이번 달 소득 {income:,}원에서 고정·변동지출과 부채 상환액 {debt_service:,}원"
            f"({ratio_pct}%)을 빼면 {net:,}원이 남습니다."
        )
    else:
        explanation = (
            f"이번 달 소득 {income:,}원보다 고정·변동지출과 부채 상환액 {debt_service:,}원"
            f"({ratio_pct}%)이 많아 {abs(net):,}원이 부족합니다."
        )

    assumptions = [
        "월 부채 상환액은 각 대출 스케줄의 1회차 납입액 합계입니다.",
        "상환비율은 부채 상환액을 월소득으로 나눈 값입니다.",
    ]

    return Capacity(
        band=band,
        net_monthly=net,
        debt_service=debt_service,
        debt_service_ratio=ratio,
        explanation=explanation,
        assumptions=assumptions,
    )
