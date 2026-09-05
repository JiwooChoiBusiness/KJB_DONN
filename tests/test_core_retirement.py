"""app/core/retirement.py 테스트: 문서(docs/reference/lifecycle_domain_v1.txt) 3.7~3.13절
예시를 golden vector로 검증한다(허용 오차 +-0.5%, 문서 자체가 "약" 표기의 단순화 예시라고
밝히고 있어 완전한 정합은 기대하지 않는다). 순수 함수라 프로필·정책 없이 값만 넣어 검증한다.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.core.retirement import (
    DEFAULT_RETIREMENT_SCENARIOS,
    annuity_pv,
    coverage_ratio,
    fv_lump,
    fv_monthly_saving,
    national_pension_estimate,
    pv_lump,
    real_rate,
    real_value,
    rebalance_amounts,
    required_monthly_saving,
    retirement_gap_projection,
    retirement_living_cost,
    withdrawal_rate,
)
from app.models import Assets, PensionAssets, PolicyParam, PolicyParams, UserProfile

TOLERANCE_PCT = 0.5


def assert_close(actual: float, expected: float, pct: float = TOLERANCE_PCT) -> None:
    diff_pct = abs(actual - expected) / abs(expected) * 100
    assert diff_pct <= pct, f"actual={actual} expected={expected} diff%={diff_pct:.4f} (허용 {pct}%)"


def make_params(**values) -> PolicyParams:
    params = {
        key: PolicyParam(key=key, value=value, needs_verification=False)
        for key, value in values.items()
    }
    return PolicyParams(version="test", params=params)


# ---------------------------------------------------------------------------
# 1.6 화폐의 시간가치
# ---------------------------------------------------------------------------


def test_fv_lump_and_pv_lump_are_inverse():
    fv = fv_lump(100_000_000, 0.05, 10)
    assert fv > 100_000_000
    pv = pv_lump(fv, 0.05, 10)
    assert_close(pv, 100_000_000, pct=0.01)


def test_real_rate_golden_vector():
    # 명목 5%, 물가 2% -> 실질수익률 약 2.94%(문서 3.9절)
    assert real_rate(0.05, 0.02) == pytest.approx(0.0294, abs=0.0002)


def test_real_value_golden_vector():
    # 30년 뒤 4억 원(명목)은 물가 연 2% 가정 시 현재가치로 약 2억 2,100만 원(문서 3.9절)
    assert_close(real_value(400_000_000, 0.02, 30), 221_000_000)


def test_retirement_living_cost_golden_vector():
    # 현재 월 300만 원, 물가 2%, 은퇴까지 20년 -> 약 446만 원(문서 3.10절)
    assert_close(retirement_living_cost(3_000_000, 0.02, 20), 4_460_000)


# ---------------------------------------------------------------------------
# 3.7~3.8 연금현재가치·적립식 복리·필요월적립액
# ---------------------------------------------------------------------------


def test_annuity_pv_golden_vector():
    # 월 부족액 150만 원, 25년, 연 3% 실질수익률 -> 약 3억 1,600만 원(문서 3.7절)
    assert_close(annuity_pv(1_500_000, 0.03, 25), 316_000_000)


def test_annuity_pv_zero_gap_or_years_is_zero():
    assert annuity_pv(0, 0.03, 25) == 0
    assert annuity_pv(1_500_000, 0.03, 0) == 0


@pytest.mark.parametrize(
    "rate,expected",
    [(0.05, 416_000_000), (0.03, 291_000_000), (0.07, 610_000_000)],
)
def test_fv_monthly_saving_golden_vectors(rate, expected):
    # 월 50만 원, 30년 납입(문서 3.8절)
    assert_close(fv_monthly_saving(500_000, rate, 30), expected)


def test_required_monthly_saving_golden_vector():
    # 목표 4억 원, 20년, 연 5% -> 월 약 97만 원(문서 3.8절)
    assert_close(required_monthly_saving(400_000_000, 0.05, 20), 970_000)


def test_fv_monthly_saving_and_required_monthly_saving_are_inverse():
    fv = fv_monthly_saving(500_000, 0.04, 15)
    pmt = required_monthly_saving(fv, 0.04, 15)
    assert_close(pmt, 500_000, pct=0.1)


# ---------------------------------------------------------------------------
# 3.11 인출전략
# ---------------------------------------------------------------------------


def test_coverage_ratio_golden_vector():
    # 필수지출 250만 원, 지속소득 180만 원 -> 충당률 72%(문서 3.11절)
    assert coverage_ratio(1_800_000, 2_500_000) == pytest.approx(0.72)


def test_coverage_ratio_zero_expense_is_zero():
    assert coverage_ratio(1_800_000, 0) == 0.0


def test_withdrawal_rate_golden_vector():
    # 은퇴자산 4억 원에서 첫해 1,600만 원 인출 -> 4%(문서 3.11절)
    assert withdrawal_rate(16_000_000, 400_000_000) == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# 3.13 리밸런싱
# ---------------------------------------------------------------------------


def test_rebalance_amounts_golden_vector():
    # 1억 원, 목표 성장60%/안정40%, 현재 7,000만/3,000만 -> 성장 1천만 원을 안정으로 이동(문서 3.13절)
    result = rebalance_amounts(
        100_000_000, {"성장": 0.6, "안정": 0.4}, {"성장": 70_000_000, "안정": 30_000_000}
    )
    assert result == {"성장": -10_000_000, "안정": 10_000_000}


# ---------------------------------------------------------------------------
# 3.3 국민연금 산식
# ---------------------------------------------------------------------------


def test_national_pension_estimate_golden_vectors():
    # A=319만원, B=300만원, 240개월(전부 2026년 이후) -> 월 약 66.6만원(문서 3.3절)
    assert_close(national_pension_estimate(3_190_000, 3_000_000, 240, 240), 666_000)
    # 480개월(전부 2026년 이후) -> 월 약 133.2만원. 240개월의 정확히 2배 관계도 함께 확인한다.
    v480 = national_pension_estimate(3_190_000, 3_000_000, 480, 480)
    assert_close(v480, 1_332_000)
    v240 = national_pension_estimate(3_190_000, 3_000_000, 240, 240)
    assert v480 == pytest.approx(v240 * 2, rel=0.001)


def test_national_pension_estimate_zero_months_is_zero():
    assert national_pension_estimate(3_190_000, 3_000_000, 0, 0) == 0


def test_national_pension_estimate_partial_2026_ratio_lowers_amount():
    full = national_pension_estimate(3_190_000, 3_000_000, 240, 240)
    partial = national_pension_estimate(3_190_000, 3_000_000, 240, 120)
    assert partial < full


def test_national_pension_estimate_custom_payout_rate_rule():
    fixed_rate = national_pension_estimate(3_190_000, 3_000_000, 240, 240, payout_rate_rule=lambda m: 1.0)
    default_rate = national_pension_estimate(3_190_000, 3_000_000, 240, 240)
    assert fixed_rate == default_rate  # 240개월은 기본 지급률도 이미 100%라 같아야 한다
    half_rate = national_pension_estimate(3_190_000, 3_000_000, 240, 240, payout_rate_rule=lambda m: 0.5)
    assert half_rate == pytest.approx(default_rate * 0.5, rel=0.001)


# ---------------------------------------------------------------------------
# retirement_gap_projection (낙관/기준/비관)
# ---------------------------------------------------------------------------


def _profile(**overrides) -> UserProfile:
    defaults = dict(
        id="rp-test", display_name="t", age=52, monthly_income=5_800_000,
        fixed_expenses=2_500_000, variable_expenses=600_000, retirement_age=63,
        target_retirement_monthly_expense=3_000_000,
        assets=Assets(
            liquid=10_000_000, investment=5_000_000,
            pension=PensionAssets(
                national_pension_months_paid=300, db_dc_balance=100_000_000,
                irp_pension_savings_balance=20_000_000, isa_balance=5_000_000,
            ),
        ),
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


def _params() -> PolicyParams:
    return make_params(national_pension_a_value=3_190_000)


def test_retirement_gap_projection_has_three_default_scenarios():
    profile = _profile()
    results = retirement_gap_projection(profile, _params(), today=date(2026, 9, 6))
    assert {r.scenario for r in results} == set(DEFAULT_RETIREMENT_SCENARIOS.keys())
    assert len(results) == 3


def test_retirement_gap_projection_worse_scenario_has_larger_or_equal_shortfall():
    profile = _profile()
    results = {r.scenario: r for r in retirement_gap_projection(profile, _params(), today=date(2026, 9, 6))}
    # 실질수익률이 낮을수록(비관) 필요자금은 커지고 적립예상액은 작아져 shortfall이 커지거나 같아야 한다.
    assert results["비관"].shortfall >= results["기준"].shortfall >= results["낙관"].shortfall
    assert results["비관"].required_fund_pv >= results["기준"].required_fund_pv >= results["낙관"].required_fund_pv


def test_retirement_gap_projection_uses_expected_national_pension_when_given():
    profile = _profile(assets=Assets(
        liquid=10_000_000, investment=5_000_000,
        pension=PensionAssets(national_pension_months_paid=300, expected_national_pension_monthly=1_500_000),
    ))
    results = retirement_gap_projection(profile, _params(), today=date(2026, 9, 6))
    assert all(r.guaranteed_income_monthly == 1_500_000 for r in results)


def test_retirement_gap_projection_no_shortfall_when_pension_assets_large():
    profile = _profile(assets=Assets(
        liquid=10_000_000, investment=5_000_000,
        pension=PensionAssets(
            national_pension_months_paid=480, db_dc_balance=2_000_000_000,
            irp_pension_savings_balance=500_000_000, isa_balance=100_000_000,
        ),
    ))
    results = {r.scenario: r for r in retirement_gap_projection(profile, _params(), today=date(2026, 9, 6))}
    assert results["낙관"].shortfall <= 0
    assert results["낙관"].required_monthly_saving == 0


def test_retirement_gap_projection_assumptions_mention_verification():
    profile = _profile()
    results = retirement_gap_projection(profile, _params(), today=date(2026, 9, 6))
    for r in results:
        assert r.assumptions
        assert any("national_pension_a_value" in a for a in r.assumptions)
