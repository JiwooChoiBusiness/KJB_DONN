"""현금흐름 시나리오(기준/불리/유리) 계산 (순수 함수).

I/O 없음. app.models와 app.core.schedule(같은 패키지)만 import한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from app.core.capacity import compute_capacity
from app.core.schedule import build_schedule, monthly_payment_equal
from app.models import (
    Assets,
    CashflowPoint,
    Goal,
    Loan,
    PolicyParams,
    RateType,
    RepayMethod,
    Scenario,
    ScenarioResult,
    UserProfile,
)

_ONE = Decimal("1")
FAVORABLE_RATE_DELTA_PCT = 0.5
FAVORABLE_EXTRA_SHARE = Decimal("0.5")
ADVERSE_VARIABLE_EXPENSE_MULTIPLIER = Decimal("1.05")


def _round_won(value: Decimal) -> int:
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _monthly_rate(annual_rate_pct: float) -> Decimal:
    return Decimal(str(annual_rate_pct)) / Decimal(100) / Decimal(12)


@dataclass
class _LoanSim:
    """시나리오 내부용 대출 상태. build_schedule과 달리 매월 다른 extra를
    동적으로 받아야 하므로(유리 시나리오의 최고금리 대출 몰아주기) 별도 구현한다."""

    loan: Loan
    rate: float
    balance: Decimal
    month_idx: int = 0
    effective_payment: Optional[Decimal] = None
    base_principal: Optional[Decimal] = None
    finished: bool = False
    _pending_interest: Decimal = field(default=Decimal(0))
    _pending_scheduled: Decimal = field(default=Decimal(0))
    _pending_month_idx: int = 0

    @property
    def term_total(self) -> int:
        return max(self.loan.remaining_months, 0)

    @property
    def grace(self) -> int:
        return min(max(self.loan.grace_months, 0), self.term_total)

    def is_done(self) -> bool:
        return self.finished or self.balance <= 0

    def peek(self) -> tuple[Decimal, Decimal]:
        """다음 회차의 (이자, extra 제외 원금)을 계산한다(잔액은 바꾸지 않음)."""
        if self.is_done():
            return (Decimal(0), Decimal(0))
        next_month_idx = self.month_idx + 1
        r = _monthly_rate(self.rate)
        interest = (self.balance * r).quantize(_ONE, rounding=ROUND_HALF_UP)
        grace = self.grace
        post_grace_total = self.term_total - grace
        method = self.loan.repay_method

        if next_month_idx <= grace:
            scheduled = Decimal(0)
        elif method == RepayMethod.EQUAL_PAYMENT:
            if self.effective_payment is None:
                if post_grace_total <= 0:
                    self.effective_payment = self.balance
                else:
                    override = self.loan.monthly_payment_override
                    if override is not None and Decimal(override) > interest:
                        self.effective_payment = Decimal(override)
                    else:
                        self.effective_payment = Decimal(
                            monthly_payment_equal(int(self.balance), self.rate, post_grace_total)
                        )
            scheduled = self.effective_payment - interest
        elif method == RepayMethod.EQUAL_PRINCIPAL:
            if self.base_principal is None:
                self.base_principal = (
                    (self.balance / Decimal(post_grace_total)).quantize(_ONE, rounding=ROUND_HALF_UP)
                    if post_grace_total > 0
                    else self.balance
                )
            scheduled = self.base_principal
        else:  # BULLET, REVOLVING
            scheduled = Decimal(0)

        self._pending_interest = interest
        self._pending_scheduled = scheduled
        self._pending_month_idx = next_month_idx
        return (interest, scheduled)

    def commit(self, extra: Decimal) -> tuple[int, int, int]:
        """peek() 직후 호출. extra를 반영해 실제로 잔액을 갱신하고
        (이자, 원금, 납입액)을 정수로 반환한다."""
        if self.is_done():
            return (0, 0, 0)
        interest = self._pending_interest
        principal = self._pending_scheduled + extra
        is_last = self._pending_month_idx >= self.term_total or principal >= self.balance
        if is_last:
            principal = self.balance
        payment = principal + interest
        self.balance = self.balance - principal
        self.month_idx = self._pending_month_idx
        if is_last:
            self.finished = True
        return (_round_won(interest), _round_won(principal), _round_won(payment))


_LABELS = {
    Scenario.BASE: "기준 시나리오",
    Scenario.ADVERSE: "악화 시나리오",
    Scenario.FAVORABLE: "완화 시나리오",
}


def _effective_rate(loan: Loan, scenario: Scenario, stress_add: float) -> float:
    if loan.rate_type != RateType.VARIABLE:
        return loan.annual_rate
    if scenario == Scenario.ADVERSE:
        return loan.annual_rate + stress_add
    if scenario == Scenario.FAVORABLE:
        return max(loan.annual_rate - FAVORABLE_RATE_DELTA_PCT, 0.0)
    return loan.annual_rate


def _goal_month_offset(today: date, target: date) -> int:
    """today부터 target까지의 개월 수(달력 월 기준, 일자는 무시). target이 today보다 이르면
    0 이하가 나올 수 있다."""
    return (target.year - today.year) * 12 + (target.month - today.month)


def _run_one(
    scenario: Scenario,
    profile: UserProfile,
    stress_add: float,
    stress_add_needs_verification: bool,
    horizon_months: int,
    *,
    today: Optional[date] = None,
    goals: Optional[list[Goal]] = None,
) -> ScenarioResult:
    sims = [
        _LoanSim(loan=loan, rate=_effective_rate(loan, scenario, stress_add), balance=Decimal(loan.balance))
        for loan in profile.loans
    ]

    assumptions: list[str] = []
    if scenario == Scenario.BASE:
        assumptions.append("기준 시나리오는 지금의 금리와 지출 조건을 그대로 유지한다고 가정했어요.")
        variable_expenses = profile.variable_expenses
    elif scenario == Scenario.ADVERSE:
        confirm_tail = " (확인 필요)" if stress_add_needs_verification else ""
        assumptions.append(
            f"악화 시나리오는 변동금리 대출 금리를 연 {stress_add:.4g}%p 올리고{confirm_tail}, "
            "변동지출도 5% 늘어난다고 가정했어요."
        )
        variable_expenses = _round_won(Decimal(profile.variable_expenses) * ADVERSE_VARIABLE_EXPENSE_MULTIPLIER)
    else:
        assumptions.append(
            "완화 시나리오는 변동금리 대출 금리를 연 0.5%p 내리고, "
            "매월 여유자금의 절반을 최고금리 대출에 추가로 상환한다고 가정했어요."
        )
        variable_expenses = profile.variable_expenses

    expenses = profile.fixed_expenses + variable_expenses
    income_base = profile.monthly_income
    income_multiplier = Decimal(1)

    # 목표(결혼·출산·주택 등)를 월 인덱스로 미리 정리한다. today가 없으면(기존 호출부와의
    # 하위 호환) 목표를 반영하지 않는다 - 날짜 없이 목표 시점을 계산할 수 없기 때문이다.
    goal_by_month: dict[int, list[Goal]] = {}
    if goals and today is not None:
        for g in goals:
            idx = _goal_month_offset(today, g.target_date)
            if 1 <= idx <= horizon_months:
                goal_by_month.setdefault(idx, []).append(g)
        if goal_by_month:
            assumptions.append(
                "목표(결혼·출산·주택 등)의 목표 금액(저축분 차감)과 목표 시점부터의 소득 변화율을 "
                "현금흐름에 반영했어요."
            )

    points: list[CashflowPoint] = []
    cumulative_net = 0
    debt_free_month: Optional[int] = None
    total_interest_accum = 0

    if all(s.is_done() for s in sims):
        debt_free_month = 1 if horizon_months > 0 else None

    for m in range(1, max(horizon_months, 0) + 1):
        month_goals = goal_by_month.get(m, [])
        for g in month_goals:
            income_multiplier *= (Decimal(1) + Decimal(str(g.monthly_income_change_pct)))
        income = _round_won(Decimal(income_base) * income_multiplier)
        goal_outflow = sum(max(g.target_amount - g.saved_amount, 0) for g in month_goals)

        peeked = [s.peek() for s in sims]  # (interest, scheduled_principal) per loan, extra 제외

        required_payment = sum(_round_won(i + p) for (i, p) in peeked)
        surplus = income - expenses - required_payment

        extra_target_idx = None
        extra_amount = Decimal(0)
        if scenario == Scenario.FAVORABLE and surplus > 0:
            active = [(idx, s) for idx, s in enumerate(sims) if not s.is_done()]
            if active:
                extra_target_idx = sorted(active, key=lambda pair: (-pair[1].rate, pair[1].loan.id))[0][0]
                extra_amount = (Decimal(surplus) * FAVORABLE_EXTRA_SHARE).quantize(_ONE, rounding=ROUND_HALF_UP)

        month_interest = 0
        month_payment = 0
        for idx, sim in enumerate(sims):
            extra = extra_amount if idx == extra_target_idx else Decimal(0)
            interest, _principal, payment = sim.commit(extra)
            month_interest += interest
            month_payment += payment

        total_balance = sum(_round_won(s.balance) if s.balance > 0 else 0 for s in sims)
        net = income - month_payment - expenses - goal_outflow
        cumulative_net += net
        total_interest_accum += month_interest

        points.append(
            CashflowPoint(
                month=m,
                income=income,
                debt_payment=month_payment,
                expenses=expenses,
                goal_outflow=goal_outflow,
                net=net,
                total_balance=total_balance,
                cumulative_net=cumulative_net,
            )
        )

        if debt_free_month is None and total_balance == 0:
            debt_free_month = m

    min_cumulative_net = min((p.cumulative_net for p in points), default=0)

    return ScenarioResult(
        scenario=scenario,
        label=_LABELS[scenario],
        assumptions=assumptions,
        points=points,
        debt_free_month=debt_free_month,
        total_interest=total_interest_accum,
        min_cumulative_net=min_cumulative_net,
    )


def run_scenarios(
    profile: UserProfile,
    params: PolicyParams,
    *,
    horizon_months: int = 60,
    today: Optional[date] = None,
    goals: Optional[list[Goal]] = None,
) -> list[ScenarioResult]:
    """기준/악화/완화 3개 시나리오의 월별 현금흐름을 계산한다.

    `today`와 `goals`는 선택 인자다(기존 호출부와의 하위 호환). 둘 다 넘기면 목표(결혼·출산·
    주택 등)의 목표일이 시야 안에 들 때 그 달에 (목표금액-저축분)만큼 일시 지출을 반영하고,
    그 달부터 `monthly_income_change_pct`만큼 소득을 조정한다.
    """
    stress_param = params.params.get("stress_variable_rate_add_pct")
    if stress_param is None:
        stress_add = 1.0
        stress_needs_verification = True
    else:
        stress_add = float(stress_param.value)
        stress_needs_verification = stress_param.needs_verification

    return [
        _run_one(
            scenario, profile, stress_add, stress_needs_verification, horizon_months,
            today=today, goals=goals,
        )
        for scenario in (Scenario.BASE, Scenario.ADVERSE, Scenario.FAVORABLE)
    ]


def run_lifecycle_projection(
    profile: UserProfile,
    params: PolicyParams,
    *,
    today: date,
    until_age: int,
    scenario_returns: dict[str, float],
) -> list[dict[str, Any]]:
    """현재 나이부터 `until_age`까지, 시나리오별 연 단위 순자산 경로를 계산한다.

    자산(유동·투자·연금성 자산)은 시나리오 실질수익률로 증식하고, 매년 저축여력
    (`capacity.net_monthly` x 12, 음수면 0)을 유동자산에 더한다고 가정한다. 부채 잔액은
    대출별 현재 상환 스케줄(`app.core.schedule.build_schedule`)에서 그 해(회차 1~12,
    13~24, ...)에 예정된 "원금" 상환분만큼만 매년 줄어든다고 가정한다(원리금 전체를
    빼면 이자까지 잔액 감소로 계산돼 실제보다 훨씬 빠르게 상환되는 것으로 과대평가된다
    - 2026-09-06 리뷰 지적). 대출의 스케줄이 끝나면(완제) 그 대출은 그 이후 잔액을
    0으로 유지한다.

    결정론: 난수를 쓰지 않고, 날짜는 `today` 인자로만 받는다. 반환값은 시나리오별로 이어붙인
    평평한 리스트이며 각 행에 "scenario" 키로 어느 시나리오인지 표시한다.
    """
    assets = profile.assets or Assets()
    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    annual_savings_capacity = max(capacity.net_monthly, 0) * 12

    # 대출별로 회차 원금(이자 제외)만 12개월 단위 연차로 묶어 둔다. 스케줄이 짧아
    # until_age보다 먼저 끝나는 대출은(완제) 그 이후 연차에 principal=0으로 취급한다.
    per_loan_annual_principal: list[list[int]] = [
        [sum(r.principal for r in sched.rows[i : i + 12]) for i in range(0, len(sched.rows), 12)]
        for sched in schedules
    ]
    per_loan_initial_balance = [loan.balance for loan in profile.loans]

    age0 = profile.age if profile.age is not None else 40
    year0 = today.year
    ages = list(range(age0, until_age + 1))

    rows: list[dict[str, Any]] = []
    for scenario_name, real_return in scenario_returns.items():
        liquid = Decimal(assets.liquid)
        investment = Decimal(assets.investment)
        pension_fund = Decimal(
            assets.pension.db_dc_balance + assets.pension.irp_pension_savings_balance + assets.pension.isa_balance
        )
        loan_balances = [Decimal(b) for b in per_loan_initial_balance]
        growth = Decimal(1) + Decimal(str(real_return))

        for i, age in enumerate(ages):
            if i > 0:
                liquid = liquid * growth + Decimal(annual_savings_capacity)
                investment = investment * growth
                pension_fund = pension_fund * growth
                year_idx = i - 1  # i=1(1년 후)이 스케줄의 1년차(회차 1~12) 원금에 대응
                for li, annual in enumerate(per_loan_annual_principal):
                    principal_due = annual[year_idx] if year_idx < len(annual) else 0
                    loan_balances[li] = max(loan_balances[li] - Decimal(principal_due), Decimal(0))
            debt_balance = sum(loan_balances, Decimal(0))
            net_worth = liquid + investment + pension_fund + Decimal(assets.real_estate) - debt_balance
            rows.append({
                "scenario": scenario_name,
                "age": age,
                "year": year0 + i,
                "debt_balance": _round_won(debt_balance),
                "liquid_assets": _round_won(liquid),
                "investment_assets": _round_won(investment),
                "pension_assets": _round_won(pension_fund),
                "net_worth": _round_won(net_worth),
            })
    return rows
