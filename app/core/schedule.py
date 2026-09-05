"""상환 스케줄 계산 (순수 함수).

I/O 없음. `app.models`만 import한다. 금액은 정수 원, 금리는 연 %.
반올림은 행 단위 round half up(Decimal), 마지막 회차는 잔액을 정확히 0으로 보정한다.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from app.models import Loan, LoanSchedule, RepayMethod, ScheduleRow

_ONE = Decimal("1")


def _round_won(value: Decimal) -> int:
    """원 단위 round half up."""
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _monthly_rate(annual_rate_pct: float) -> Decimal:
    return Decimal(str(annual_rate_pct)) / Decimal(100) / Decimal(12)


def monthly_payment_equal(principal: int, annual_rate_pct: float, months: int) -> int:
    """원리금균등 월 납입액. `P * r / (1 - (1+r)^-n)`, round half up."""
    if months <= 0 or principal <= 0:
        return 0
    p = Decimal(principal)
    r = _monthly_rate(annual_rate_pct)
    if r == 0:
        return _round_won(p / Decimal(months))
    factor = (_ONE + r) ** months
    payment = p * r * factor / (factor - _ONE)
    return _round_won(payment)


def build_schedule(
    loan: Loan,
    *,
    rate_override: float | None = None,
    extra_payment: int = 0,
    months_override: int | None = None,
) -> LoanSchedule:
    """현재 잔액(loan.balance) 기준 남은 상환 스케줄.

    - `grace_months` 동안은 이자만 납부하고, 이후 `remaining_months - grace_months`
      기간에 걸쳐 상환한다.
    - BULLET은 매월 이자만 내고 만기(마지막 회차)에 원금을 상환한다.
    - REVOLVING은 매월 이자만 내고 마지막 회차에 잔액을 정리한다(근사).
    - `extra_payment`는 매월 원금에 추가로 상환하는 금액이며, 잔액이 0이 되면
      그 회차를 마지막으로 스케줄을 종료한다.
    - `monthly_payment_override`가 있고 상환방식이 원리금균등이면 그 값을 월
      납입액으로 쓰되, (거치 종료 시점의) 이자보다 작거나 같으면 무시하고
      자동 계산된 월 납입액을 쓰며 그 사실을 `assumptions`에 남긴다.
    - 마지막 회차는 잔액을 정확히 0으로 보정한다.
    """
    assumptions: list[str] = []
    annual_rate = loan.annual_rate if rate_override is None else rate_override
    if rate_override is not None:
        assumptions.append(f"적용 금리를 연 {annual_rate:.4g}%로 가정해 계산했습니다.")

    total_months = loan.remaining_months if months_override is None else months_override
    if total_months <= 0 and loan.balance > 0:
        total_months = 1
        assumptions.append("남은 개월이 0 이하로 계산되어 최소 1개월로 보정했습니다.")
    total_months = max(total_months, 0)

    grace = min(max(loan.grace_months, 0), total_months)
    if grace > 0:
        post_grace = total_months - grace
        assumptions.append(f"거치 {grace}개월(이자만 납부) 후 {post_grace}개월간 상환합니다.")

    method = loan.repay_method
    if method == RepayMethod.REVOLVING:
        assumptions.append("리볼빙/마이너스통장은 매월 이자만 납부하고 마지막 회차에 잔액을 정리하는 근사 방식입니다.")

    extra = max(extra_payment, 0)
    if extra > 0:
        assumptions.append(f"매월 {extra:,}원을 원금에 추가 상환한다고 가정했습니다.")

    r = _monthly_rate(annual_rate)
    balance = Decimal(loan.balance)

    rows: list[ScheduleRow] = []

    post_grace_total = total_months - grace
    effective_payment: Decimal | None = None
    base_principal: Decimal | None = None

    month = 0
    while balance > 0 and month < total_months:
        month += 1
        interest = _round_won(balance * r) if r != 0 else 0
        interest_dec = Decimal(interest)

        in_grace = month <= grace
        if in_grace:
            scheduled_principal = Decimal(0)
        elif method == RepayMethod.EQUAL_PAYMENT:
            if effective_payment is None:
                if post_grace_total <= 0:
                    effective_payment = balance
                else:
                    override = loan.monthly_payment_override
                    if override is not None:
                        if Decimal(override) > interest_dec:
                            effective_payment = Decimal(override)
                        else:
                            assumptions.append(
                                f"지정한 월 납입액({override:,}원)이 이자보다 작거나 같아 무시하고 "
                                "자동 계산된 월 납입액을 사용했습니다."
                            )
                    if effective_payment is None:
                        effective_payment = Decimal(
                            monthly_payment_equal(int(balance), annual_rate, post_grace_total)
                        )
            scheduled_principal = effective_payment - interest_dec
        elif method == RepayMethod.EQUAL_PRINCIPAL:
            if base_principal is None:
                base_principal = (
                    (balance / Decimal(post_grace_total)).quantize(_ONE, rounding=ROUND_HALF_UP)
                    if post_grace_total > 0
                    else balance
                )
            scheduled_principal = base_principal
        else:  # BULLET, REVOLVING
            scheduled_principal = Decimal(0)

        principal = scheduled_principal + Decimal(extra)
        is_last_by_term = month >= total_months
        if principal >= balance or is_last_by_term:
            principal = balance

        payment = principal + interest_dec
        new_balance = balance - principal

        rows.append(
            ScheduleRow(
                month=month,
                payment=_round_won(payment),
                principal=_round_won(principal),
                interest=interest,
                balance=_round_won(new_balance) if new_balance > 0 else 0,
            )
        )
        balance = new_balance if new_balance > 0 else Decimal(0)

    total_payment = sum(row.payment for row in rows)
    total_interest = sum(row.interest for row in rows)
    first_payment = rows[0].payment if rows else 0

    return LoanSchedule(
        loan_id=loan.id,
        method=method,
        annual_rate=annual_rate,
        months=len(rows),
        rows=rows,
        total_payment=total_payment,
        total_interest=total_interest,
        first_payment=first_payment,
        assumptions=assumptions,
    )
