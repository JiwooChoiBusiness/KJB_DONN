"""핵심 재무비율 5종 계산 (순수 함수). I/O 없음, app.models만 import한다.

문서(`docs/reference/lifecycle_domain_v1.txt`) 1.5절과 5.2절 의사코드를 따른다.
- 유동성비율(개월) = 유동자산 / 월생활비(고정+변동)
- 저축률 = (연소득 - 연지출 - 연원리금상환) / 연소득  (DONN_LIFECYCLE_PLAN.md가 명시한 대로
  문서 5.2 의사코드의 "연저축액 = 연소득 - 연지출"에 원리금상환액을 더 빼서 capacity.py의
  net_monthly 개념과 맞춘다)
- 부채비율 = 총부채 / 총자산
- 원리금상환비율 = 연원리금상환액 / 연소득
- 투자자산비율 = 투자자산 / 순자산

각 비율은 분모가 0이거나(소득 0 등) 자산 정보 자체가 없으면 값이 None이고 flags에 "na"가
찍힌다. 생애 단계별 임계값(`thresholds_for_stage`, `config/thresholds.yaml` 파싱 결과 1건)이
있으면 해당 비율에 "ok"/"warn" 플래그를 매긴다. 부채비율·투자자산비율은 문서에도 절대
임계값이 없어(1.5절 "개발 참고") thresholds에 대응 키가 없으며, 계산 가능하면 항상 "ok"다.
"""
from __future__ import annotations

from typing import Any

from app.models import FinancialRatios, LoanSchedule, PensionAssets, UserProfile


def _pension_fund_value(pension: PensionAssets) -> int:
    """연금자산 중 잔액(현재가치)이 있는 항목만 합산한다. 국민연금은 잔액이 아니라
    가입월수·예상연금월액만 있어 자산 합계에 포함하지 않는다(3층 연금구조 중 1층은
    확정소득으로, 2~3층만 자산으로 다룬다)."""
    return pension.db_dc_balance + pension.irp_pension_savings_balance + pension.isa_balance


def compute_ratios(
    profile: UserProfile,
    schedules: list[LoanSchedule],
    thresholds_for_stage: dict[str, Any],
) -> FinancialRatios:
    """5개 재무비율과 총자산·순자산을 계산하고, 비율마다 해석 문장과 flag를 붙인다."""
    thresholds_for_stage = thresholds_for_stage or {}
    assets = profile.assets

    monthly_expense = profile.fixed_expenses + profile.variable_expenses
    annual_income = profile.monthly_income * 12
    debt_service_monthly = sum(s.first_payment for s in schedules)
    annual_debt_service = debt_service_monthly * 12
    total_debt = sum(l.balance for l in profile.loans)

    total_assets = 0
    if assets is not None:
        total_assets = assets.liquid + assets.investment + _pension_fund_value(assets.pension) + assets.real_estate
    net_worth = total_assets - total_debt

    interpretations: dict[str, str] = {}
    flags: dict[str, str] = {}
    thresholds_used: dict[str, float] = {}

    # ---- 유동성비율 (개월) ----
    if assets is None or monthly_expense <= 0:
        liquidity_months = None
        flags["liquidity_months"] = "na"
        interpretations["liquidity_months"] = (
            "자산 정보나 월 생활비 정보가 없어 유동성비율을 계산할 수 없습니다."
        )
    else:
        liquidity_months = assets.liquid / monthly_expense
        interpretations["liquidity_months"] = (
            f"유동자산 {assets.liquid:,}원은 월 생활비 {monthly_expense:,}원의 "
            f"{liquidity_months:.1f}개월분입니다."
        )
        min_months = thresholds_for_stage.get("min_liquidity_months")
        if min_months is not None:
            thresholds_used["min_liquidity_months"] = float(min_months)
            flags["liquidity_months"] = "ok" if liquidity_months >= min_months else "warn"
        else:
            flags["liquidity_months"] = "ok"

    # ---- 저축률 ----
    if annual_income <= 0:
        saving_rate = None
        flags["saving_rate"] = "na"
        interpretations["saving_rate"] = "소득 정보가 없어 저축률을 계산할 수 없습니다."
    else:
        annual_saving = annual_income - (monthly_expense * 12) - annual_debt_service
        saving_rate = annual_saving / annual_income
        interpretations["saving_rate"] = (
            f"연 소득 {annual_income:,}원 중 저축 가능액은 {annual_saving:,}원으로 "
            f"저축률은 {saving_rate * 100:.1f}%입니다."
        )
        min_rate = thresholds_for_stage.get("min_saving_rate")
        if min_rate is not None:
            thresholds_used["min_saving_rate"] = float(min_rate)
            flags["saving_rate"] = "ok" if saving_rate >= min_rate else "warn"
        else:
            flags["saving_rate"] = "ok"

    # ---- 부채비율 ----
    if assets is None or total_assets <= 0:
        debt_ratio = None
        flags["debt_ratio"] = "na"
        interpretations["debt_ratio"] = "자산 정보가 없어 부채비율을 계산할 수 없습니다."
    else:
        debt_ratio = total_debt / total_assets
        interpretations["debt_ratio"] = (
            f"총자산 {total_assets:,}원 중 총부채 {total_debt:,}원으로 부채비율은 "
            f"{debt_ratio * 100:.1f}%입니다."
        )
        flags["debt_ratio"] = "ok"

    # ---- 원리금상환비율 ----
    if annual_income <= 0:
        debt_service_ratio = None
        flags["debt_service_ratio"] = "na"
        interpretations["debt_service_ratio"] = "소득 정보가 없어 원리금상환비율을 계산할 수 없습니다."
    else:
        debt_service_ratio = annual_debt_service / annual_income
        interpretations["debt_service_ratio"] = (
            f"연 소득 {annual_income:,}원 대비 연간 원리금상환액 {annual_debt_service:,}원으로 "
            f"원리금상환비율은 {debt_service_ratio * 100:.1f}%입니다."
        )
        max_ratio = thresholds_for_stage.get("max_debt_service_ratio")
        if max_ratio is not None:
            thresholds_used["max_debt_service_ratio"] = float(max_ratio)
            flags["debt_service_ratio"] = "ok" if debt_service_ratio <= max_ratio else "warn"
        else:
            flags["debt_service_ratio"] = "ok"

    # ---- 투자자산비율 ----
    if assets is None or net_worth <= 0:
        investment_ratio = None
        flags["investment_ratio"] = "na"
        interpretations["investment_ratio"] = (
            "자산 정보가 없거나 순자산이 0 이하라 투자자산비율을 계산할 수 없습니다."
        )
    else:
        investment_ratio = assets.investment / net_worth
        interpretations["investment_ratio"] = (
            f"순자산 {net_worth:,}원 중 투자자산 {assets.investment:,}원으로 "
            f"투자자산비율은 {investment_ratio * 100:.1f}%입니다."
        )
        flags["investment_ratio"] = "ok"

    if thresholds_for_stage.get("min_coverage_ratio") is not None:
        # 충당률 자체는 이 함수가 계산하지 않지만(50대 이상, retirement.py 담당), 화면이
        # 같은 자리에서 임계값을 보여줄 수 있도록 함께 실어 보낸다.
        thresholds_used["min_coverage_ratio"] = float(thresholds_for_stage["min_coverage_ratio"])

    return FinancialRatios(
        liquidity_months=liquidity_months,
        saving_rate=saving_rate,
        debt_ratio=debt_ratio,
        debt_service_ratio=debt_service_ratio,
        investment_ratio=investment_ratio,
        total_assets=total_assets,
        net_worth=net_worth,
        interpretations=interpretations,
        thresholds=thresholds_used,
        flags=flags,
    )
