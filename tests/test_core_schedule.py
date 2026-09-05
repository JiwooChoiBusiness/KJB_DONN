"""app/core/schedule.py, app/core/loan.py, app/core/scenarios.py 테스트.

SPEC.md 4장 골든 벡터를 명시적 허용오차와 함께 검증한다.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.loan import extra_payment_effect, prepay_fee, refinance_compare
from app.core.schedule import build_schedule, monthly_payment_equal
from app.core.scenarios import run_scenarios
from app.models import (
    Loan,
    PolicyParam,
    PolicyParams,
    RateType,
    RepayMethod,
    UserProfile,
)


def make_loan(**overrides) -> Loan:
    defaults = dict(
        id="loan-1",
        name="테스트 대출",
        loan_type="credit",
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        rate_type=RateType.FIXED,
        repay_method=RepayMethod.EQUAL_PAYMENT,
        remaining_months=12,
        grace_months=0,
    )
    defaults.update(overrides)
    return Loan(**defaults)


def make_params(**values) -> PolicyParams:
    params = {}
    for key, value in values.items():
        params[key] = PolicyParam(key=key, value=value, needs_verification=False)
    return PolicyParams(version="test", params=params)


# ---------------------------------------------------------------------------
# 골든 벡터: 원리금균등
# ---------------------------------------------------------------------------


def test_gv_equal_payment_1_monthly_payment():
    # 100,000,000원, 5.0%, 12개월 -> 월 8,560,748원
    assert monthly_payment_equal(100_000_000, 5.0, 12) == 8_560_748


def test_gv_equal_payment_1_total_interest():
    loan = make_loan(principal=100_000_000, balance=100_000_000, annual_rate=5.0, remaining_months=12)
    sched = build_schedule(loan)
    assert sched.first_payment == 8_560_748
    # 총이자 2,728,976원 ± 12원
    assert abs(sched.total_interest - 2_728_976) <= 12
    assert sched.rows[-1].balance == 0


def test_gv_equal_payment_2_monthly_payment():
    # 30,000,000원, 6.0%, 36개월 -> 월 912,658원
    assert monthly_payment_equal(30_000_000, 6.0, 36) == 912_658


def test_gv_equal_payment_2_total_interest():
    loan = make_loan(principal=30_000_000, balance=30_000_000, annual_rate=6.0, remaining_months=36)
    sched = build_schedule(loan)
    assert sched.first_payment == 912_658
    # 총이자 2,855,688원 ± 36원
    assert abs(sched.total_interest - 2_855_688) <= 36
    assert sched.rows[-1].balance == 0


# ---------------------------------------------------------------------------
# 골든 벡터: 원금균등
# ---------------------------------------------------------------------------


def test_gv_equal_principal():
    # 12,000,000원, 12.0%, 12개월 -> 1회차 1,120,000원(원금 1,000,000 + 이자 120,000), 총이자 780,000원
    loan = make_loan(
        principal=12_000_000,
        balance=12_000_000,
        annual_rate=12.0,
        remaining_months=12,
        repay_method=RepayMethod.EQUAL_PRINCIPAL,
    )
    sched = build_schedule(loan)
    row1 = sched.rows[0]
    assert row1.principal == 1_000_000
    assert row1.interest == 120_000
    assert row1.payment == 1_120_000
    assert sched.total_interest == 780_000
    assert sched.rows[-1].balance == 0


# ---------------------------------------------------------------------------
# 골든 벡터: 만기일시(BULLET)
# ---------------------------------------------------------------------------


def test_gv_bullet():
    # 10,000,000원, 6.0%, 12개월 -> 매월 이자 50,000원, 12회차 10,050,000원, 총이자 600,000원
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=12,
        repay_method=RepayMethod.BULLET,
    )
    sched = build_schedule(loan)
    assert all(row.interest == 50_000 for row in sched.rows[:-1])
    assert all(row.principal == 0 for row in sched.rows[:-1])
    assert sched.rows[-1].payment == 10_050_000
    assert sched.rows[-1].principal == 10_000_000
    assert sched.rows[-1].balance == 0
    assert sched.total_interest == 600_000


# ---------------------------------------------------------------------------
# 골든 벡터: 거치
# ---------------------------------------------------------------------------


def test_gv_grace_interest_only_rows():
    # 10,000,000원, 6.0%, 거치 2 + 상환 10개월 원리금균등 -> 1~2회차 50,000원
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=12,
        grace_months=2,
    )
    sched = build_schedule(loan)
    assert sched.rows[0].payment == 50_000
    assert sched.rows[0].principal == 0
    assert sched.rows[1].payment == 50_000
    assert sched.rows[1].principal == 0
    assert sched.months == 12
    assert sched.rows[-1].balance == 0


def test_gv_grace_post_grace_payment_and_total_interest():
    """SPEC.md 4장 표는 이 케이스의 3회차 납입액을 1,027,660원 ± 5원,
    총이자를 376,600원 ± 60원으로 적어 두었다. 그러나 표준 원리금균등 공식
    `P*r*(1+r)^n/((1+r)^n-1)` (골든 벡터 1, 2와 정확히 일치하는 바로 그 공식)을
    거치 종료 시점 잔액 10,000,000원, n=10, r=0.5%에 대입하면 1,027,706원이
    나오고(총이자 377,057원), 이는 SPEC 표의 값과 46원/457원 차이가 나 명시된
    허용오차를 벗어난다. 다른 해석(n=8, n=12 등)은 표의 값과 훨씬 크게 어긋난다.
    따라서 SPEC 2.1의 서술("거치 후 remaining_months - grace_months로 상환")과
    골든 벡터 1·2가 공유하는 공식을 기준으로 삼고, 이 표의 수치는 저자의 단순
    계산 오차로 보고 재계산한 값을 기준값으로 사용한다. (SPEC.md 미수정, 이 사실은
    최종 보고서에 기록한다.)
    """
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=12,
        grace_months=2,
    )
    sched = build_schedule(loan)
    assert abs(sched.rows[2].payment - 1_027_706) <= 5
    assert abs(sched.total_interest - 377_057) <= 60
    assert sched.rows[-1].balance == 0


# ---------------------------------------------------------------------------
# REVOLVING
# ---------------------------------------------------------------------------


def test_revolving_interest_only_then_payoff():
    loan = make_loan(
        principal=5_000_000,
        balance=5_000_000,
        annual_rate=8.0,
        remaining_months=6,
        repay_method=RepayMethod.REVOLVING,
    )
    sched = build_schedule(loan)
    assert sched.months == 6
    for row in sched.rows[:-1]:
        assert row.principal == 0
        assert row.interest > 0
    assert sched.rows[-1].principal == 5_000_000
    assert sched.rows[-1].balance == 0


# ---------------------------------------------------------------------------
# monthly_payment_override
# ---------------------------------------------------------------------------


def test_monthly_payment_override_used_when_valid():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=12,
        monthly_payment_override=1_500_000,
    )
    sched = build_schedule(loan)
    assert sched.first_payment == 1_500_000
    assert sched.months < 12  # 더 큰 금액을 내므로 원래 기간보다 빨리 끝난다
    assert sched.rows[-1].balance == 0
    assert not sched.assumptions  # 유효한 override는 별도 경고를 남기지 않는다


def test_monthly_payment_override_ignored_when_not_greater_than_interest():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=12,
        monthly_payment_override=10_000,  # 1회차 이자(50,000원)보다 작다
    )
    sched = build_schedule(loan)
    assert sched.first_payment == monthly_payment_equal(10_000_000, 6.0, 12)
    assert any("무시" in note for note in sched.assumptions)


# ---------------------------------------------------------------------------
# extra_payment / build_schedule extra_payment 종료 조건
# ---------------------------------------------------------------------------


def test_build_schedule_extra_payment_ends_when_balance_zero():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=36,
    )
    sched = build_schedule(loan, extra_payment=2_000_000)
    assert sched.months < 36
    assert sched.rows[-1].balance == 0
    assert sched.total_payment - sched.total_interest == 10_000_000


@pytest.mark.parametrize("extra", [0, 100_000, 300_000, 900_000])
def test_extra_payment_effect_sanity(extra):
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=8.0,
        remaining_months=36,
    )
    effect = extra_payment_effect(loan, extra)
    assert effect["new_months"] <= 36
    assert effect["months_saved"] >= 0
    assert effect["interest_saved"] >= 0


def test_extra_payment_effect_monotonic_more_extra_is_better():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=8.0,
        remaining_months=36,
    )
    small = extra_payment_effect(loan, 100_000)
    large = extra_payment_effect(loan, 300_000)
    # 더 많이 추가 상환할수록 개월수는 더 짧아지고 이자는 더 적게 낸다
    assert large["new_months"] < small["new_months"]
    assert large["months_saved"] > small["months_saved"]
    assert large["interest_saved"] > small["interest_saved"]


# ---------------------------------------------------------------------------
# loan.py: prepay_fee / refinance_compare
# ---------------------------------------------------------------------------


def test_prepay_fee_charged_before_until_date():
    loan = make_loan(
        remaining_months=36,
        prepay_fee_rate=1.4,
        prepay_fee_until=date(2027, 9, 6),
    )
    fee = prepay_fee(loan, 10_000_000, date(2026, 9, 6))
    # 10,000,000 * 1.4% * (12/36) = 46,666.67 -> round half up
    assert fee == 46_667


def test_prepay_fee_zero_after_until_date():
    loan = make_loan(
        remaining_months=36,
        prepay_fee_rate=1.4,
        prepay_fee_until=date(2026, 1, 1),
    )
    fee = prepay_fee(loan, 10_000_000, date(2026, 9, 6))
    assert fee == 0


def test_prepay_fee_zero_when_not_set():
    loan = make_loan(remaining_months=36)
    fee = prepay_fee(loan, 10_000_000, date(2026, 9, 6))
    assert fee == 0


def test_prepay_fee_uses_policy_period_months_when_given():
    """(nit) prepay_fee_period_months를 policy에서 읽으면 분모 36 대신 그 값을 쓴다."""
    loan = make_loan(
        remaining_months=36,
        prepay_fee_rate=1.4,
        prepay_fee_until=date(2027, 9, 6),
    )
    params = PolicyParams(version="t", params={
        "prepay_fee_period_months": PolicyParam(
            key="prepay_fee_period_months", value=24, needs_verification=True,
        ),
    })
    fee = prepay_fee(loan, 10_000_000, date(2026, 9, 6), params=params)
    # 10,000,000 * 1.4% * (12/24) = 70,000 (분모가 36이면 46,667이 나와야 함)
    assert fee == 70_000


def test_prepay_fee_missing_policy_param_falls_back_to_36():
    loan = make_loan(
        remaining_months=36,
        prepay_fee_rate=1.4,
        prepay_fee_until=date(2027, 9, 6),
    )
    params = PolicyParams(version="t", params={})
    fee = prepay_fee(loan, 10_000_000, date(2026, 9, 6), params=params)
    assert fee == 46_667


def test_refinance_compare_lower_rate_saves_interest_and_has_breakeven():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=36,
    )
    result = refinance_compare(loan, new_rate=4.0, new_term_months=36, fee=46_667)
    assert result["new_total_interest"] < result["current_total_interest"]
    assert result["interest_saving"] > 0
    assert result["monthly_after"] < result["monthly_before"]
    assert result["breakeven_months"] is not None
    assert result["breakeven_months"] > 0


def test_refinance_compare_no_saving_gives_none_breakeven():
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=4.0,
        remaining_months=36,
    )
    # 더 높은 금리로 "대환"하면 월 납입액이 늘어나 손익분기가 없다
    result = refinance_compare(loan, new_rate=6.0, new_term_months=36, fee=50_000)
    assert result["breakeven_months"] is None


def test_refinance_compare_same_rate_longer_term_has_no_breakeven():
    """SEV4 #5: 금리는 그대로 두고 기간만 늘리면 월 납입액은 줄어도 총이자는 늘어난다
    (원리금균등 특성). breakeven_months는 월 "납입액" 절감이 아니라 월 "이자" 절감을
    기준으로 삼아야 하므로, 이 경우처럼 interest_saving이 음수면 월 납입액 감소와
    무관하게 breakeven_months는 항상 None이어야 한다(수수료를 내고 "갈아탈 이유"가 없는데도
    손익분기가 계산되던 버그)."""
    loan = make_loan(
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        remaining_months=36,
    )
    result = refinance_compare(loan, new_rate=6.0, new_term_months=60, fee=500_000)
    assert result["interest_saving"] < 0
    assert result["monthly_after"] < result["monthly_before"]
    assert result["breakeven_months"] is None


# ---------------------------------------------------------------------------
# scenarios.py 스모크 테스트 (재현성, 방향성)
# ---------------------------------------------------------------------------


def _sample_profile() -> UserProfile:
    loans = [
        Loan(
            id="a",
            name="신용대출",
            loan_type="credit",
            principal=10_000_000,
            balance=10_000_000,
            annual_rate=8.0,
            rate_type=RateType.VARIABLE,
            repay_method=RepayMethod.EQUAL_PAYMENT,
            remaining_months=24,
        ),
        Loan(
            id="b",
            name="카드론",
            loan_type="card_loan",
            principal=3_000_000,
            balance=3_000_000,
            annual_rate=15.0,
            rate_type=RateType.FIXED,
            repay_method=RepayMethod.EQUAL_PAYMENT,
            remaining_months=12,
        ),
    ]
    return UserProfile(
        id="p1",
        display_name="테스트",
        monthly_income=3_000_000,
        fixed_expenses=1_000_000,
        variable_expenses=500_000,
        emergency_fund=500_000,
        loans=loans,
    )


def test_run_scenarios_reproducible():
    profile = _sample_profile()
    params = make_params(stress_variable_rate_add_pct=1.0, emergency_fund_months=1)
    r1 = run_scenarios(profile, params, horizon_months=24)
    r2 = run_scenarios(profile, params, horizon_months=24)
    assert [r.model_dump() for r in r1] == [r.model_dump() for r in r2]


def test_run_scenarios_adverse_worse_than_base_and_favorable_better():
    profile = _sample_profile()
    params = make_params(stress_variable_rate_add_pct=1.0, emergency_fund_months=1)
    results = {r.scenario.value: r for r in run_scenarios(profile, params, horizon_months=36)}
    base = results["base"]
    adverse = results["adverse"]
    favorable = results["favorable"]

    # 악화 시나리오는 기준보다 총이자가 크다 (변동금리 인상)
    assert adverse.total_interest > base.total_interest
    # 완화 시나리오는 추가상환 덕분에 총이자가 더 적고 부채 완료가 더 빠르다
    assert favorable.total_interest < base.total_interest
    assert favorable.debt_free_month is not None
    assert base.debt_free_month is not None
    assert favorable.debt_free_month <= base.debt_free_month
