"""app/core/ratios.py 테스트: 문서 1.5/5.2절 5개 재무비율을 크래프트한 프로필로 검증한다.

각 비율의 값·해석 문장·flag("ok"/"warn"/"na")를 확인하고, 자산 정보가 없거나 분모가 0일 때
"na"가 정확히 나오는지, 생애 단계 임계값이 있을 때 "warn"이 정확히 갈리는지를 본다.
"""
from __future__ import annotations

import pytest

from app.core.ratios import compute_ratios
from app.core.schedule import build_schedule
from app.models import Assets, Loan, PensionAssets, RepayMethod, UserProfile


def make_loan(**overrides) -> Loan:
    defaults = dict(
        id="loan-1", name="테스트 대출", loan_type="credit", principal=20_000_000,
        balance=20_000_000, annual_rate=6.0, repay_method=RepayMethod.EQUAL_PAYMENT,
        remaining_months=24,
    )
    defaults.update(overrides)
    return Loan(**defaults)


def make_profile(**overrides) -> UserProfile:
    defaults = dict(
        id="profile-ratios", display_name="테스트", monthly_income=5_000_000,
        fixed_expenses=1_500_000, variable_expenses=500_000, loans=[],
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


THRESHOLDS = {
    "min_liquidity_months": 6.0,
    "min_saving_rate": 0.20,
    "max_debt_service_ratio": 0.40,
    "min_coverage_ratio": 0.60,
    "needs_verification": True,
    "note": "테스트 임계값",
}


def test_all_five_ratios_computed_on_full_profile():
    loans = [make_loan()]
    profile = make_profile(
        loans=loans,
        assets=Assets(
            liquid=12_000_000, investment=8_000_000, real_estate=100_000_000,
            pension=PensionAssets(db_dc_balance=10_000_000, irp_pension_savings_balance=5_000_000, isa_balance=5_000_000),
        ),
    )
    schedules = [build_schedule(l) for l in loans]
    ratios = compute_ratios(profile, schedules, THRESHOLDS)

    monthly_expense = 1_500_000 + 500_000
    assert ratios.liquidity_months == pytest.approx(12_000_000 / monthly_expense)
    assert ratios.total_assets == 12_000_000 + 8_000_000 + 20_000_000 + 100_000_000
    assert ratios.net_worth == ratios.total_assets - 20_000_000
    assert ratios.debt_ratio == pytest.approx(20_000_000 / ratios.total_assets)
    assert ratios.investment_ratio == pytest.approx(8_000_000 / ratios.net_worth)

    annual_income = 5_000_000 * 12
    annual_debt_service = sum(s.first_payment for s in schedules) * 12
    expected_saving_rate = (annual_income - monthly_expense * 12 - annual_debt_service) / annual_income
    assert ratios.saving_rate == pytest.approx(expected_saving_rate)
    assert ratios.debt_service_ratio == pytest.approx(annual_debt_service / annual_income)

    for key in ("liquidity_months", "saving_rate", "debt_ratio", "debt_service_ratio", "investment_ratio"):
        assert key in ratios.interpretations and ratios.interpretations[key]
        assert ratios.flags[key] in ("ok", "warn", "na")

    assert ratios.thresholds["min_liquidity_months"] == 6.0
    assert ratios.thresholds["min_saving_rate"] == 0.20
    assert ratios.thresholds["max_debt_service_ratio"] == 0.40
    assert ratios.thresholds["min_coverage_ratio"] == 0.60


def test_liquidity_and_saving_rate_flag_warn_below_threshold():
    loans = [make_loan(annual_rate=4.0, balance=5_000_000, principal=5_000_000)]
    profile = make_profile(
        loans=loans, fixed_expenses=2_000_000, variable_expenses=500_000,
        assets=Assets(liquid=2_000_000),  # 6개월 기준 15,000,000에 못 미침
    )
    schedules = [build_schedule(l) for l in loans]
    ratios = compute_ratios(profile, schedules, THRESHOLDS)
    assert ratios.flags["liquidity_months"] == "warn"
    assert ratios.liquidity_months < THRESHOLDS["min_liquidity_months"]


def test_liquidity_flag_ok_above_threshold():
    profile = make_profile(
        fixed_expenses=1_000_000, variable_expenses=0,
        assets=Assets(liquid=10_000_000),  # 10개월분, 6개월 기준 이상
    )
    ratios = compute_ratios(profile, [], THRESHOLDS)
    assert ratios.flags["liquidity_months"] == "ok"


def test_debt_service_ratio_flag_warn_above_max():
    loans = [make_loan(annual_rate=15.0, balance=50_000_000, principal=50_000_000, remaining_months=24)]
    profile = make_profile(loans=loans, monthly_income=3_000_000, fixed_expenses=500_000, variable_expenses=200_000)
    schedules = [build_schedule(l) for l in loans]
    ratios = compute_ratios(profile, schedules, THRESHOLDS)
    assert ratios.debt_service_ratio > THRESHOLDS["max_debt_service_ratio"]
    assert ratios.flags["debt_service_ratio"] == "warn"


def test_na_when_assets_missing():
    profile = make_profile(assets=None)
    ratios = compute_ratios(profile, [], THRESHOLDS)
    assert ratios.liquidity_months is None
    assert ratios.flags["liquidity_months"] == "na"
    assert ratios.debt_ratio is None
    assert ratios.flags["debt_ratio"] == "na"
    assert ratios.investment_ratio is None
    assert ratios.flags["investment_ratio"] == "na"
    assert ratios.total_assets == 0
    # 소득이 있으니 저축률·상환비율은 계산 가능해야 한다.
    assert ratios.saving_rate is not None
    assert ratios.flags["saving_rate"] != "na"


def test_na_when_income_zero():
    profile = make_profile(monthly_income=0, assets=Assets(liquid=1_000_000))
    ratios = compute_ratios(profile, [], THRESHOLDS)
    assert ratios.saving_rate is None
    assert ratios.flags["saving_rate"] == "na"
    assert ratios.debt_service_ratio is None
    assert ratios.flags["debt_service_ratio"] == "na"
    # 유동성비율은 소득과 무관하게 계산 가능해야 한다.
    assert ratios.liquidity_months is not None


def test_na_when_net_worth_non_positive():
    loans = [make_loan(balance=50_000_000, principal=50_000_000)]
    profile = make_profile(loans=loans, assets=Assets(liquid=1_000_000, investment=2_000_000))
    ratios = compute_ratios(profile, [], THRESHOLDS)
    assert ratios.net_worth <= 0
    assert ratios.investment_ratio is None
    assert ratios.flags["investment_ratio"] == "na"


def test_ratios_without_thresholds_default_to_ok_when_computable():
    profile = make_profile(assets=Assets(liquid=1_000_000))
    ratios = compute_ratios(profile, [], {})
    assert ratios.flags["liquidity_months"] == "ok"
    assert ratios.flags["saving_rate"] == "ok"
    assert ratios.thresholds == {}


def test_no_product_or_company_names_in_interpretations():
    profile = make_profile(assets=Assets(liquid=1_000_000, investment=500_000))
    ratios = compute_ratios(profile, [], THRESHOLDS)
    for text in ratios.interpretations.values():
        assert "—" not in text
        assert "추천" not in text
