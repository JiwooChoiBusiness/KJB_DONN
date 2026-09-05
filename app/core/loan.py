"""대출 단위 계산: 중도상환수수료, 대환 비교, 추가상환 효과 (순수 함수).

I/O 없음. `app.models`과 같은 패키지 내부(app.core.schedule)만 import한다.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.core.schedule import build_schedule
from app.models import Loan

_ONE = Decimal("1")


def _round_won(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _months_between(start: date, end: date) -> int:
    """start에서 end까지 남은 개월 수(부분월은 올림). end <= start면 0."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day > start.day:
        months += 1
    return max(months, 0)


def prepay_fee(loan: Loan, amount: int, today: date) -> int:
    """중도상환수수료.

    `prepay_fee_until`이 today 이후면
    `amount * prepay_fee_rate/100 * (남은 수수료 개월 / 36)` 반올림, 아니면 0.
    산식 근거는 policy 항목 `prepay_fee_period_months`(확인 필요).
    """
    if loan.prepay_fee_until is None or loan.prepay_fee_until <= today:
        return 0
    if loan.prepay_fee_rate <= 0 or amount <= 0:
        return 0
    months_left = _months_between(today, loan.prepay_fee_until)
    if months_left <= 0:
        return 0
    fee = (
        Decimal(amount)
        * Decimal(str(loan.prepay_fee_rate))
        / Decimal(100)
        * Decimal(months_left)
        / Decimal(36)
    )
    return _round_won(fee)


def refinance_compare(loan: Loan, new_rate: float, new_term_months: int, fee: int) -> dict:
    """현재 대출과 신규 조건(대환) 비교.

    키: current_total_interest, new_total_interest, interest_saving,
    monthly_before, monthly_after, breakeven_months
    (수수료 / 월 납입 절감, 절감이 없으면 None).
    """
    current_schedule = build_schedule(loan)
    new_schedule = build_schedule(loan, rate_override=new_rate, months_override=new_term_months)

    current_total_interest = current_schedule.total_interest
    new_total_interest = new_schedule.total_interest
    interest_saving = current_total_interest - new_total_interest
    monthly_before = current_schedule.first_payment
    monthly_after = new_schedule.first_payment
    monthly_saving = monthly_before - monthly_after

    breakeven_months = None
    if monthly_saving > 0 and fee > 0:
        breakeven_months = -(-fee // monthly_saving)  # 올림 나눗셈
    elif monthly_saving > 0 and fee <= 0:
        breakeven_months = 0

    return {
        "current_total_interest": current_total_interest,
        "new_total_interest": new_total_interest,
        "interest_saving": interest_saving,
        "monthly_before": monthly_before,
        "monthly_after": monthly_after,
        "breakeven_months": breakeven_months,
    }


def extra_payment_effect(loan: Loan, extra_monthly: int) -> dict:
    """매월 추가상환 시 효과. 키: months_saved, interest_saved, new_months."""
    base_schedule = build_schedule(loan)
    new_schedule = build_schedule(loan, extra_payment=max(extra_monthly, 0))

    months_saved = base_schedule.months - new_schedule.months
    interest_saved = base_schedule.total_interest - new_schedule.total_interest

    return {
        "months_saved": months_saved,
        "interest_saved": interest_saved,
        "new_months": new_schedule.months,
    }
