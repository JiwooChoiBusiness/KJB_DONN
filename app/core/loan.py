"""대출 단위 계산: 중도상환수수료, 대환 비교, 추가상환 효과 (순수 함수).

I/O 없음. `app.models`과 같은 패키지 내부(app.core.schedule)만 import한다.
"""
from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Optional

from app.core.schedule import build_schedule
from app.models import Loan, PolicyParams

_ONE = Decimal("1")
_DEFAULT_PREPAY_FEE_PERIOD_MONTHS = 36


def _round_won(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _shift_months(d: date, months: int) -> date:
    """d를 months개월 이동한 날짜. 대상 월에 그 일(day)이 없으면(예: 1월 31일 + 1개월은
    2월에 31일이 없음) 그 달의 마지막 날로 자른다. 이 자르기(clip)가 없으면 "몇 개월이
    온전히 지났는지"를 달력 월 길이가 다른 구간(특히 2월)에서 잘못 셀 수 있다."""
    total = d.year * 12 + (d.month - 1) + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


def _months_between(start: date, end: date) -> int:
    """start에서 end까지 남은 개월 수(부분월은 올림). end <= start면 0.

    단순히 "연*12+월 차이 후 일(day) 숫자 비교"만 하면(예: 1월 31일 -> 2월 1일처럼 짧은
    2월을 지나는 구간에서) 실제 경과 기간을 잘못 셀 수 있다. `_shift_months`로 온전히
    지난 개월 수를 먼저 찾고, 남는 일수가 있으면 한 달만 올림한다(2026-09-06 리뷰: 월말
    기준일 근처에서 수수료가 과다 계산되지 않도록 보정).
    """
    if end <= start:
        return 0
    months = 0
    while _shift_months(start, months + 1) <= end:
        months += 1
    anchor = _shift_months(start, months)
    if end > anchor:
        months += 1
    return max(months, 0)


def prepay_fee(loan: Loan, amount: int, today: date, params: Optional[PolicyParams] = None) -> int:
    """중도상환수수료.

    `prepay_fee_until`이 today 이후면
    `amount * prepay_fee_rate/100 * (남은 수수료 개월 / prepay_fee_period_months)` 반올림,
    아니면 0. 분모(`prepay_fee_period_months`, 확인 필요)는 `params`가 있으면
    `config/policy_params.yaml` 값을 쓰고, 없으면(기존 호출부와의 하위 호환) 기본값 36을 쓴다.
    """
    if loan.prepay_fee_until is None or loan.prepay_fee_until <= today:
        return 0
    if loan.prepay_fee_rate <= 0 or amount <= 0:
        return 0
    months_left = _months_between(today, loan.prepay_fee_until)
    if months_left <= 0:
        return 0
    period_months = _DEFAULT_PREPAY_FEE_PERIOD_MONTHS
    if params is not None:
        param = params.params.get("prepay_fee_period_months")
        if param is not None and param.value:
            try:
                period_months = int(param.value)
            except (TypeError, ValueError):
                period_months = _DEFAULT_PREPAY_FEE_PERIOD_MONTHS
    if period_months <= 0:
        period_months = _DEFAULT_PREPAY_FEE_PERIOD_MONTHS
    fee = (
        Decimal(amount)
        * Decimal(str(loan.prepay_fee_rate))
        / Decimal(100)
        * Decimal(months_left)
        / Decimal(period_months)
    )
    return _round_won(fee)


def refinance_compare(loan: Loan, new_rate: float, new_term_months: int, fee: int) -> dict:
    """현재 대출과 신규 조건(대환) 비교.

    키: current_total_interest, new_total_interest, interest_saving,
    monthly_before, monthly_after, breakeven_months
    (수수료 / 월 이자 절감, 절감이 없으면 None).

    breakeven_months는 월 "납입액" 절감이 아니라 월 "이자" 절감을 기준으로 계산한다.
    기간을 늘리면 월 납입액은 줄어도 총이자가 늘어날 수 있는데(원리금균등의 특성),
    월 납입액 절감만 보면 이 경우에도 손익분기 개월이 계산되어 "기간을 늘리면 이득"이라는
    잘못된 인상을 준다(2026-09-06 리뷰 지적). interest_saving(총이자 절감)이 0보다 클 때만
    수수료 회수가 의미 있으므로, 그 외에는 항상 None이다.
    """
    current_schedule = build_schedule(loan)
    new_schedule = build_schedule(loan, rate_override=new_rate, months_override=new_term_months)

    current_total_interest = current_schedule.total_interest
    new_total_interest = new_schedule.total_interest
    interest_saving = current_total_interest - new_total_interest
    monthly_before = current_schedule.first_payment
    monthly_after = new_schedule.first_payment

    breakeven_months = None
    if interest_saving > 0 and new_term_months > 0:
        # 월평균 이자 절감 = 총이자 절감 / 신규 대출 기간(개월). 첫 회차 이자 차액을 써도
        # 되지만(SPEC 문구의 대안), 원리금균등에서는 회차가 갈수록 이자가 줄어들어 첫 회차
        # 값이 절감을 과대평가할 수 있어 기간 평균을 기본으로 쓴다.
        monthly_interest_saving = Decimal(interest_saving) / Decimal(new_term_months)
        if fee <= 0:
            breakeven_months = 0
        elif monthly_interest_saving > 0:
            breakeven_months = int(
                (Decimal(fee) / monthly_interest_saving).to_integral_value(rounding=ROUND_CEILING)
            )

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


def lump_sum_effect(loan: Loan, amount: int, fee: int) -> dict:
    """일시 상환(목돈으로 한 번에 갚기) 시 효과(SPEC 2.13). 키: months_saved, interest_saved,
    fee, new_months.

    잔액에서 `amount`를 차감한 새 잔액으로 스케줄을 다시 계산해 기존 스케줄과 비교한다.
    `amount`가 잔액 이상이면 새 잔액이 0이 되어 `build_schedule`이 빈 스케줄(월 0, 이자 0)을
    돌려주므로 자연스럽게 "전액 정리"로 처리된다. 수수료(`fee`)는 이 함수가 계산하지 않고
    호출부가 `prepay_fee`로 구해 그대로 전달한다(결과에는 참고용으로만 담는다).
    """
    base_schedule = build_schedule(loan)
    amount = max(amount, 0)
    new_balance = max(loan.balance - amount, 0)
    new_loan = loan.model_copy(update={"balance": new_balance})
    new_schedule = build_schedule(new_loan)

    months_saved = base_schedule.months - new_schedule.months
    interest_saved = base_schedule.total_interest - new_schedule.total_interest

    return {
        "months_saved": months_saved,
        "interest_saved": interest_saved,
        "fee": fee,
        "new_months": new_schedule.months,
    }
