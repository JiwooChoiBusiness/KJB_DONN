"""app/core/capacity.py, app/core/rules.py 테스트."""
from __future__ import annotations

from datetime import date

import pytest

from app.core.capacity import compute_capacity
from app.core.rules import evaluate_rules
from app.core.schedule import build_schedule
from app.models import (
    Assets,
    CapacityBand,
    Loan,
    PensionAssets,
    PolicyParam,
    PolicyParams,
    RepayMethod,
    UserProfile,
)

TODAY = date(2026, 9, 6)


def make_loan(**overrides) -> Loan:
    defaults = dict(
        id="loan-1",
        name="테스트 대출",
        loan_type="credit",
        principal=10_000_000,
        balance=10_000_000,
        annual_rate=6.0,
        repay_method=RepayMethod.EQUAL_PAYMENT,
        remaining_months=12,
        grace_months=0,
    )
    defaults.update(overrides)
    return Loan(**defaults)


def make_profile(loans: list[Loan], **overrides) -> UserProfile:
    defaults = dict(
        id="profile-1",
        display_name="테스트",
        monthly_income=3_000_000,
        fixed_expenses=1_000_000,
        variable_expenses=500_000,
        emergency_fund=3_000_000,
        loans=loans,
        flags=[],
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


def make_params(**values) -> PolicyParams:
    params = {
        key: PolicyParam(key=key, value=value, needs_verification=False)
        for key, value in values.items()
    }
    return PolicyParams(version="test", params=params)


def build_schedules(loans: list[Loan]) -> list:
    return [build_schedule(loan) for loan in loans]


# ---------------------------------------------------------------------------
# compute_capacity
# ---------------------------------------------------------------------------


def test_capacity_negative_band_when_net_below_zero():
    loans = [make_loan(annual_rate=6.0, remaining_months=12, balance=10_000_000)]
    profile = make_profile(loans, monthly_income=1_500_000, fixed_expenses=1_000_000, variable_expenses=600_000)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.band == CapacityBand.NEGATIVE
    assert cap.net_monthly < 0
    assert "부족" in cap.explanation


def test_capacity_tight_band_when_ratio_high():
    # 부채상환비율이 50% 이상이면 순가용액이 양수여도 TIGHT
    loans = [make_loan(annual_rate=5.0, remaining_months=12, balance=100_000_000, principal=100_000_000)]
    profile = make_profile(loans, monthly_income=15_000_000, fixed_expenses=1_000_000, variable_expenses=500_000)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.net_monthly > 0
    assert cap.debt_service_ratio >= 0.5
    assert cap.band == CapacityBand.TIGHT


def test_capacity_comfortable_band():
    loans = [make_loan(annual_rate=4.0, remaining_months=36, balance=3_000_000, principal=3_000_000)]
    profile = make_profile(loans, monthly_income=5_000_000, fixed_expenses=800_000, variable_expenses=500_000)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.net_monthly >= 0.2 * profile.monthly_income
    assert cap.band == CapacityBand.COMFORTABLE
    assert "상품" not in cap.explanation  # 상품명 없이 숫자만 담긴 문장


def test_capacity_guest_profile_no_income_no_debt_is_tight():
    """SEV3 #17: 소득 0원(온보딩 전 게스트)이고 부채도 없으면 TIGHT(정보 부족)이어야
    하며, 이전처럼 net>=0.2*income(=0)이 참이 되어 COMFORTABLE로 잘못 판정되면 안 된다."""
    profile = make_profile([], monthly_income=0, fixed_expenses=0, variable_expenses=0)
    cap = compute_capacity(profile, [])
    assert cap.band == CapacityBand.TIGHT
    assert "소득" in cap.explanation


def test_capacity_guest_profile_no_income_with_debt_is_negative():
    """SEV3 #17: 소득 0원인데 상환할 대출이 있으면 NEGATIVE로 판정해야 한다."""
    loans = [make_loan(annual_rate=6.0, remaining_months=12, balance=5_000_000)]
    profile = make_profile(loans, monthly_income=0, fixed_expenses=0, variable_expenses=0)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.band == CapacityBand.NEGATIVE
    assert "소득" in cap.explanation


def test_capacity_ok_band_between_tight_and_comfortable():
    loans = [make_loan(annual_rate=5.0, remaining_months=24, balance=15_000_000, principal=15_000_000)]
    profile = make_profile(loans, monthly_income=3_500_000, fixed_expenses=1_500_000, variable_expenses=900_000)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.debt_service_ratio < 0.5
    assert cap.net_monthly >= 0.05 * profile.monthly_income
    assert cap.net_monthly < 0.2 * profile.monthly_income
    assert cap.band == CapacityBand.OK


# ---------------------------------------------------------------------------
# R0 안전 모드
# ---------------------------------------------------------------------------


def test_r0_safe_mode_only_card_on_delinquency_signal():
    """delinquency_signal 플래그가 있으면 안전 모드 카드 1장만 나오고
    R2(추가상환)·R3(대환)는 생성되지 않는다. 이 프로필은 R1/R4/R5/R6 조건도
    만족하지 않도록 구성해 '카드 목록이 안전 모드 1장뿐'임을 명확히 검증한다."""
    loans = [
        make_loan(id="a1", annual_rate=9.0, remaining_months=24, balance=20_000_000, principal=20_000_000),
        make_loan(id="a2", annual_rate=8.0, remaining_months=18, balance=5_000_000, principal=5_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=2_000_000,
        fixed_expenses=1_000_000,
        variable_expenses=800_000,
        emergency_fund=2_000_000,  # 비상금 충분 -> R4 미발동
        flags=["delinquency_signal"],  # income_up/job_changed 없음 -> R1 미발동
    )
    params = make_params(
        rate_cut_request_min_rate=6.0,
        refi_rate_gap_min_pct=1.0,
        emergency_fund_months=1,
    )
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)

    assert len(cards) == 1
    assert cards[0].rule_id == "R0"
    assert cards[0].safe_mode is True
    assert cards[0].priority == 0
    assert not any(c.rule_id == "R3" for c in cards)
    assert not any(c.rule_id == "R2" for c in cards)


def test_r0_triggers_on_negative_band_even_without_flag():
    loans = [make_loan(annual_rate=10.0, remaining_months=24, balance=50_000_000, principal=50_000_000)]
    profile = make_profile(
        loans,
        monthly_income=1_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=2_000_000,
        flags=[],
    )
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.band == CapacityBand.NEGATIVE
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert any(c.rule_id == "R0" and c.safe_mode for c in cards)
    assert not any(c.rule_id in ("R2", "R3") for c in cards)


def test_r0_suppresses_r3_when_refinance_conditions_also_hold():
    """연체 신호와 동시에 R3 대환 조건(잔여>=12, 금리>=7.0)도 만족하는 대출이
    있어도 R3 카드는 생성되지 않아야 한다."""
    loans = [
        make_loan(id="a1", annual_rate=9.0, remaining_months=24, balance=20_000_000, principal=20_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=2_000_000,
        fixed_expenses=1_000_000,
        variable_expenses=800_000,
        emergency_fund=2_000_000,
        flags=["delinquency_signal"],
    )
    params = make_params(refi_rate_gap_min_pct=1.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R3" for c in cards)
    assert any(c.rule_id == "R0" for c in cards)


# ---------------------------------------------------------------------------
# R4 비상금과 R2 우선순위 하향
# ---------------------------------------------------------------------------


def test_r4_fires_and_lowers_r2_priority_to_45():
    loans = [
        make_loan(id="b1", annual_rate=9.0, remaining_months=24, balance=10_000_000, principal=10_000_000),
        make_loan(id="b2", annual_rate=5.0, remaining_months=24, balance=5_000_000, principal=5_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=3_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=100_000,  # fixed_expenses*1개월(800,000)보다 적음 -> R4 발동
        flags=[],
    )
    params = make_params(emergency_fund_months=1)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)

    r4 = next(c for c in cards if c.rule_id == "R4")
    r2 = next(c for c in cards if c.rule_id == "R2")
    assert r4.priority == 20
    assert r2.priority == 45
    # 최고금리 대출(b1)을 대상으로 한다
    assert r2.related_loan_ids == ["b1"]


def test_r2_absent_when_leftover_non_positive():
    loans = [
        make_loan(id="c1", annual_rate=9.0, remaining_months=24, balance=10_000_000, principal=10_000_000),
        make_loan(id="c2", annual_rate=5.0, remaining_months=24, balance=5_000_000, principal=5_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=1_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=3_000_000,
    )
    params = make_params(emergency_fund_months=1)
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.net_monthly <= 0
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R2" for c in cards)


def test_r2_requires_at_least_two_loans():
    loans = [make_loan(id="d1", annual_rate=9.0, remaining_months=24, balance=5_000_000, principal=5_000_000)]
    profile = make_profile(loans, monthly_income=3_000_000, fixed_expenses=500_000, variable_expenses=300_000)
    params = make_params(emergency_fund_months=1)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R2" for c in cards)


# ---------------------------------------------------------------------------
# R1 금리인하요구권
# ---------------------------------------------------------------------------


def test_r1_fires_for_high_rate_credit_with_income_up_flag():
    loans = [make_loan(id="e1", loan_type="credit", annual_rate=7.5, remaining_months=24)]
    profile = make_profile(loans, flags=["income_up"])
    params = make_params(rate_cut_request_min_rate=6.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    r1 = next(c for c in cards if c.rule_id == "R1")
    assert r1.priority == 50
    assert r1.numbers["cost"] == 0
    assert len(r1.steps) == 3


def test_r1_absent_without_flags():
    loans = [make_loan(id="e2", loan_type="credit", annual_rate=7.5, remaining_months=24)]
    profile = make_profile(loans, flags=[])
    params = make_params(rate_cut_request_min_rate=6.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R1" for c in cards)


def test_r1_absent_below_min_rate():
    loans = [make_loan(id="e3", loan_type="credit", annual_rate=5.0, remaining_months=24)]
    profile = make_profile(loans, flags=["job_changed"])
    params = make_params(rate_cut_request_min_rate=6.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R1" for c in cards)


# ---------------------------------------------------------------------------
# R6 고금리 소액
# ---------------------------------------------------------------------------


def test_r6_fires_for_small_high_rate_card_loan():
    loans = [make_loan(id="f1", loan_type="card_loan", annual_rate=18.0, balance=1_000_000, principal=1_000_000, remaining_months=6)]
    profile = make_profile(loans, monthly_income=3_000_000)
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert any(c.rule_id == "R6" for c in cards)


def test_r6_absent_when_balance_too_large():
    loans = [make_loan(id="f2", loan_type="card_loan", annual_rate=18.0, balance=10_000_000, principal=10_000_000, remaining_months=24)]
    profile = make_profile(loans, monthly_income=1_000_000)  # balance > income*2
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R6" for c in cards)


# ---------------------------------------------------------------------------
# R5 만기·거치 임박
# ---------------------------------------------------------------------------


def test_r5_fires_for_bullet_near_maturity():
    loans = [
        make_loan(id="g1", repay_method=RepayMethod.BULLET, remaining_months=2, balance=50_000_000, principal=50_000_000, annual_rate=4.0),
    ]
    profile = make_profile(loans, monthly_income=5_000_000, fixed_expenses=1_000_000, variable_expenses=500_000)
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    r5 = next(c for c in cards if c.rule_id == "R5")
    assert r5.priority == 10
    assert r5.numbers["payoff_amount"] > 0


def test_r5_fires_for_grace_ending_soon():
    loans = [
        make_loan(id="g2", grace_months=1, remaining_months=200, balance=90_000_000, principal=100_000_000, annual_rate=4.5),
    ]
    profile = make_profile(loans, monthly_income=5_000_000, fixed_expenses=1_000_000, variable_expenses=500_000)
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert any(c.rule_id == "R5" for c in cards)


def test_r5_absent_when_far_from_maturity():
    loans = [
        make_loan(id="g3", repay_method=RepayMethod.BULLET, remaining_months=24, balance=50_000_000, principal=50_000_000, annual_rate=4.0),
    ]
    profile = make_profile(loans, monthly_income=5_000_000, fixed_expenses=1_000_000, variable_expenses=500_000)
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R5" for c in cards)


# ---------------------------------------------------------------------------
# R3 대환 후보
# ---------------------------------------------------------------------------


def test_r3_fires_and_chip_has_no_product_name():
    loans = [make_loan(id="h1", loan_type="credit", annual_rate=9.5, remaining_months=30, balance=15_000_000, principal=15_000_000)]
    profile = make_profile(loans, flags=[])
    params = make_params(refi_rate_gap_min_pct=1.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    r3 = next(c for c in cards if c.rule_id == "R3")
    assert r3.priority == 60
    assert r3.chip is not None
    assert r3.chip.text == "내 조건으로 공시 비교하기"
    assert r3.chip.intent == "compare"
    assert r3.chip.params["target_loan_id"] == "h1"
    assert r3.chip.params["amount"] == 15_000_000
    assert r3.chip.params["term_months"] == 30
    # 상품명/회사명 언급 금지, em dash 금지
    for text in (r3.title, r3.summary):
        assert "—" not in text
        assert "추천" not in text


def test_r3_absent_when_remaining_months_below_12():
    loans = [make_loan(id="h2", loan_type="credit", annual_rate=9.5, remaining_months=6, balance=5_000_000, principal=5_000_000)]
    profile = make_profile(loans)
    params = make_params(refi_rate_gap_min_pct=1.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R3" for c in cards)


def test_r3_absent_when_rate_below_threshold():
    loans = [make_loan(id="h3", loan_type="credit", annual_rate=5.0, remaining_months=24, balance=5_000_000, principal=5_000_000)]
    profile = make_profile(loans)
    params = make_params(refi_rate_gap_min_pct=1.0)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R3" for c in cards)


# ---------------------------------------------------------------------------
# 정렬 순서와 재현성
# ---------------------------------------------------------------------------


def test_cards_sorted_by_priority_ascending():
    loans = [
        make_loan(id="i1", loan_type="credit", annual_rate=9.5, remaining_months=30, balance=15_000_000, principal=15_000_000),
        make_loan(id="i2", loan_type="card_loan", annual_rate=18.0, remaining_months=6, balance=1_000_000, principal=1_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=3_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=100_000,
        flags=["income_up"],
    )
    params = make_params(
        emergency_fund_months=1,
        rate_cut_request_min_rate=6.0,
        refi_rate_gap_min_pct=1.0,
    )
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    priorities = [c.priority for c in cards]
    assert priorities == sorted(priorities)


def test_evaluate_rules_is_reproducible():
    loans = [
        make_loan(id="j1", loan_type="credit", annual_rate=9.5, remaining_months=30, balance=15_000_000, principal=15_000_000),
        make_loan(id="j2", loan_type="card_loan", annual_rate=18.0, remaining_months=6, balance=1_000_000, principal=1_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=3_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=100_000,
        flags=["income_up"],
    )
    params = make_params(
        emergency_fund_months=1,
        rate_cut_request_min_rate=6.0,
        refi_rate_gap_min_pct=1.0,
    )
    cap = compute_capacity(profile, build_schedules(loans))
    schedules = build_schedules(loans)
    cards1 = evaluate_rules(profile, schedules, cap, params, today=TODAY)
    cards2 = evaluate_rules(profile, schedules, cap, params, today=TODAY)
    assert [c.model_dump() for c in cards1] == [c.model_dump() for c in cards2]


def test_all_action_card_numbers_are_present_and_no_em_dash():
    loans = [
        make_loan(id="k1", loan_type="credit", annual_rate=9.5, remaining_months=30, balance=15_000_000, principal=15_000_000),
        make_loan(id="k2", loan_type="card_loan", annual_rate=18.0, remaining_months=6, balance=1_000_000, principal=1_000_000),
    ]
    profile = make_profile(
        loans,
        monthly_income=3_000_000,
        fixed_expenses=800_000,
        variable_expenses=500_000,
        emergency_fund=100_000,
        flags=["income_up"],
    )
    params = make_params(
        emergency_fund_months=1,
        rate_cut_request_min_rate=6.0,
        refi_rate_gap_min_pct=1.0,
    )
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert cards, "이 픽스처는 최소 한 장의 카드를 생성해야 한다"
    for card in cards:
        assert card.numbers, f"{card.rule_id} 카드는 numbers가 비어 있으면 안 된다"
        assert card.assumptions, f"{card.rule_id} 카드는 assumptions가 있어야 한다"
        assert "—" not in card.title
        assert "—" not in card.summary
        assert "추천" not in card.title
        assert "추천" not in card.summary


# ---------------------------------------------------------------------------
# R7(비상자금, 기존 R4 id 유지)~R10 생애주기 층(P7). thresholds 인자가 없으면(위 테스트들
# 전부) 하위 호환으로 R8~R10은 평가되지 않고 R4는 policy 기준으로만 동작해야 한다.
# ---------------------------------------------------------------------------

STAGE_THRESHOLDS = {
    "min_liquidity_months": 6.0,
    "min_saving_rate": 0.20,
    "max_debt_service_ratio": 0.40,
    "min_coverage_ratio": 0.60,
    "needs_verification": True,
    "note": "테스트 임계값",
}


def test_r4_uses_stage_threshold_months_when_thresholds_given():
    """thresholds가 있으면 emergency_fund_months(policy) 대신 min_liquidity_months(생애
    단계)를 개월 기준으로 쓴다. 기준이 6개월로 policy 기본값(1개월)보다 커서, policy만 쓸 때는
    통과했을 비상금이 thresholds를 쓰면 부족으로 판정되어야 한다."""
    profile = make_profile([], fixed_expenses=1_000_000, emergency_fund=2_000_000)  # policy 1개월(100만원)은 충족
    params = make_params(emergency_fund_months=1)
    cap = compute_capacity(profile, [])

    cards_without_thresholds = evaluate_rules(profile, [], cap, params, today=TODAY)
    assert not any(c.rule_id == "R4" for c in cards_without_thresholds)

    cards_with_thresholds = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    r4 = next(c for c in cards_with_thresholds if c.rule_id == "R4")
    assert r4.numbers["target_emergency_fund"] == 1_000_000 * 6
    assert "생애 단계" in r4.assumptions[0]


def test_r8_fires_when_saving_rate_below_stage_minimum():
    profile = make_profile(
        [], monthly_income=3_000_000, fixed_expenses=2_500_000, variable_expenses=400_000,
    )
    params = make_params()
    cap = compute_capacity(profile, [])
    assert not any(c.rule_id == "R8" for c in evaluate_rules(profile, [], cap, params, today=TODAY))

    cards = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    r8 = next(c for c in cards if c.rule_id == "R8")
    assert r8.priority == 55
    assert r8.numbers["saving_rate"] < STAGE_THRESHOLDS["min_saving_rate"]
    assert r8.chip is None


def test_r8_absent_when_saving_rate_meets_minimum():
    profile = make_profile([], monthly_income=5_000_000, fixed_expenses=1_000_000, variable_expenses=500_000)
    params = make_params()
    cap = compute_capacity(profile, [])
    cards = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    assert not any(c.rule_id == "R8" for c in cards)


def test_r9_fires_when_dsr_above_stage_max_and_suppressed_without_thresholds():
    loans = [make_loan(id="r9-1", annual_rate=9.0, remaining_months=24, balance=27_000_000, principal=27_000_000)]
    profile = make_profile(
        loans, monthly_income=3_000_000, fixed_expenses=1_200_000, variable_expenses=300_000,
        emergency_fund=5_000_000,
    )
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    assert cap.debt_service_ratio > 0.40
    assert cap.band != CapacityBand.NEGATIVE and cap.debt_service_ratio < 0.7  # R0 안전모드는 안 걸림

    cards_without = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY)
    assert not any(c.rule_id == "R9" for c in cards_without)

    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    r9 = next(c for c in cards if c.rule_id == "R9")
    assert r9.priority == 25
    assert r9.chip is not None
    assert r9.chip.intent == "compare"
    assert r9.chip.text == "내 조건으로 공시 비교"


def test_r9_suppressed_in_safe_mode():
    loans = [make_loan(id="r9-2", annual_rate=9.0, remaining_months=24, balance=27_000_000, principal=27_000_000)]
    profile = make_profile(
        loans, monthly_income=3_000_000, fixed_expenses=1_200_000, variable_expenses=300_000,
        emergency_fund=5_000_000, flags=["delinquency_signal"],
    )
    params = make_params()
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    assert any(c.rule_id == "R0" for c in cards)
    assert not any(c.rule_id == "R9" for c in cards)


def test_r10_fires_for_age_50plus_below_coverage_minimum():
    profile = make_profile(
        [], age=55, monthly_income=4_000_000, fixed_expenses=2_500_000, variable_expenses=500_000,
        assets=Assets(pension=PensionAssets(national_pension_months_paid=120)),
    )
    params = make_params(national_pension_a_value=3_190_000)
    cap = compute_capacity(profile, [])
    assert not any(c.rule_id == "R10" for c in evaluate_rules(profile, [], cap, params, today=TODAY))

    cards = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    r10 = next(c for c in cards if c.rule_id == "R10")
    assert r10.priority == 35
    assert r10.numbers["coverage_ratio"] < STAGE_THRESHOLDS["min_coverage_ratio"]
    assert r10.chip.intent == "lifecycle"
    assert r10.chip.text == "노후자금 시뮬레이션 보기"


def test_r10_absent_under_age_50():
    profile = make_profile(
        [], age=45, monthly_income=4_000_000, fixed_expenses=2_500_000, variable_expenses=500_000,
        assets=Assets(pension=PensionAssets(national_pension_months_paid=120)),
    )
    params = make_params(national_pension_a_value=3_190_000)
    cap = compute_capacity(profile, [])
    cards = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    assert not any(c.rule_id == "R10" for c in cards)


def test_r10_absent_when_expected_national_pension_covers_expense():
    profile = make_profile(
        [], age=60, monthly_income=4_000_000, fixed_expenses=1_000_000, variable_expenses=500_000,
        assets=Assets(pension=PensionAssets(expected_national_pension_monthly=2_000_000)),
    )
    params = make_params(national_pension_a_value=3_190_000)
    cap = compute_capacity(profile, [])
    cards = evaluate_rules(profile, [], cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    assert not any(c.rule_id == "R10" for c in cards)


def test_r0_suppression_of_r2_r3_r9_unchanged_when_thresholds_passed():
    """R0 안전모드일 때 R2(추가상환)·R3(대환)·R9(원리금상환비율 공시비교)는 thresholds를
    넘겨도 여전히 생성되지 않아야 한다(기존 R0 억제 규칙은 그대로, R4/R8/R10처럼 정보성
    안내는 안전모드에서도 계속 보여준다는 설계는 유지). 이 픽스처는 R0 조건(연체 신호)과
    동시에 R9 조건(DSR > 40%)도 만족하도록 만들어 억제가 실제로 작동하는지 확인한다."""
    loans = [
        make_loan(id="a1", annual_rate=9.0, remaining_months=24, balance=27_000_000, principal=27_000_000),
    ]
    profile = make_profile(
        loans, monthly_income=3_000_000, fixed_expenses=1_200_000, variable_expenses=300_000,
        emergency_fund=5_000_000, flags=["delinquency_signal"],
    )
    params = make_params(rate_cut_request_min_rate=6.0, refi_rate_gap_min_pct=1.0, emergency_fund_months=1)
    cap = compute_capacity(profile, build_schedules(loans))
    cards = evaluate_rules(profile, build_schedules(loans), cap, params, today=TODAY, thresholds=STAGE_THRESHOLDS)
    assert any(c.rule_id == "R0" and c.safe_mode for c in cards)
    assert not any(c.rule_id in ("R2", "R3", "R9") for c in cards)
