"""화폐의 시간가치·노후자금·연금 계산 (순수 함수). I/O 없음.

문서(`docs/reference/lifecycle_domain_v1.txt`) 1.6, 3.3~3.13절의 산식을 구현한다. 전제(문서
0.3): "수치·산식 예시는 교육용 시나리오이며 ... 산식의 구조와 변수 관계를 정확히 구현하는
것이 핵심"이므로, 여기 값들은 전부 "참고 시나리오"이며 특정 상품이나 자산배분을 권하지
않는다(`models.LIFECYCLE_DISCLAIMER`).

반올림은 원 단위 round half up. 이자율은 연 실효/명목 소수(0.05 = 5%)로 받는다(schedule.py의
연 % 표기와 다르니 호출부에서 단위를 맞춘다).

같은 패키지(app.core.schedule, app.core.capacity)만 추가로 import한다(SPEC 순수 함수 규칙,
rules.py가 loan.py를 쓰는 것과 같은 패턴).
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Optional

from app.core.capacity import compute_capacity
from app.core.schedule import build_schedule
from app.models import PensionAssets, PolicyParams, RetirementProjection, UserProfile

_ONE = Decimal("1")

# 인출 종료 연령(내부 가정). 장수위험(문서 3.14절)을 고려해 실제로는 개인차가 크므로
# "참고 시나리오"로만 쓴다. 은퇴연령이 이보다 높으면 최소 1년은 인출 기간으로 본다.
LATE_LIFE_END_AGE = 90

# 낙관/기준/비관 3개 시나리오의 실질수익률과 물가상승률(문서 3.7~3.10 예시와 동일한 값).
# 규제 수치가 아니라 이 서비스가 정의하는 교육용 시나리오 구조이므로 policy_params.yaml이
# 아니라 이 모듈 상수로 둔다(scenarios.py의 FAVORABLE_RATE_DELTA_PCT와 같은 패턴).
DEFAULT_RETIREMENT_SCENARIOS: dict[str, dict[str, float]] = {
    "낙관": {"real_return": 0.05, "inflation": 0.02},
    "기준": {"real_return": 0.03, "inflation": 0.02},
    "비관": {"real_return": 0.01, "inflation": 0.02},
}

DEFAULT_RETIREMENT_AGE = 65


def _round_won(value: Decimal | float) -> int:
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return int(value.quantize(_ONE, rounding=ROUND_HALF_UP))


def _policy_value(params: PolicyParams, key: str, default: Any) -> tuple[Any, bool]:
    p = params.params.get(key)
    if p is None:
        return default, True
    return p.value, p.needs_verification


# ---------------------------------------------------------------------------
# 1.6 화폐의 시간가치
# ---------------------------------------------------------------------------


def fv_lump(pv: float, annual_rate: float, years: float) -> int:
    """일시금의 미래가치. FV = PV x (1+r)^n."""
    if pv == 0:
        return 0
    return _round_won(Decimal(str(pv)) * (_ONE + Decimal(str(annual_rate))) ** Decimal(str(years)))


def pv_lump(fv: float, annual_rate: float, years: float) -> int:
    """일시금의 현재가치. PV = FV / (1+r)^n."""
    if fv == 0:
        return 0
    return _round_won(Decimal(str(fv)) / (_ONE + Decimal(str(annual_rate))) ** Decimal(str(years)))


def real_rate(nominal: float, inflation: float) -> float:
    """실질수익률 = (1+명목수익률) / (1+물가상승률) - 1. 예: real_rate(0.05, 0.02) ~= 0.0294."""
    return float((_ONE + Decimal(str(nominal))) / (_ONE + Decimal(str(inflation))) - _ONE)


def real_value(nominal_amount: float, inflation: float, years: float) -> int:
    """명목금액의 실질가치(현재가치 환산). 실질가치 = 명목금액 / (1+물가상승률)^n."""
    return pv_lump(nominal_amount, inflation, years)


def retirement_living_cost(current_monthly: float, inflation: float, years_to_retirement: float) -> int:
    """은퇴시점 생활비 = 현재 생활비 x (1+물가상승률)^은퇴까지 연수.

    예: retirement_living_cost(3,000,000, 0.02, 20) ~= 4,460,000.
    """
    return fv_lump(current_monthly, inflation, years_to_retirement)


# ---------------------------------------------------------------------------
# 3.7~3.8 연금현재가치·적립식 복리·필요월적립액
# ---------------------------------------------------------------------------


def annuity_pv(monthly_gap: float, real_annual_rate: float, years: float) -> int:
    """은퇴 후 매월 monthly_gap을 real_annual_rate(연 실질수익률)로 years년 인출하기 위해
    은퇴시점에 필요한 자금(PV). PV = PMT x [1 - (1+i)^-N] / i, i=월 실질수익률, N=개월 수.

    i가 0이면(실질수익률 0%) PV = PMT x N. 예: annuity_pv(1,500,000, 0.03, 25) ~= 316,000,000.
    """
    if monthly_gap <= 0 or years <= 0:
        return 0
    months = Decimal(str(years)) * Decimal(12)
    i = Decimal(str(real_annual_rate)) / Decimal(12)
    pmt = Decimal(str(monthly_gap))
    if i == 0:
        return _round_won(pmt * months)
    factor = _ONE - (_ONE + i) ** (-months)
    return _round_won(pmt * factor / i)


def fv_monthly_saving(pmt: float, annual_rate: float, years: float) -> int:
    """매월 pmt를 annual_rate(연 명목수익률)로 years년 적립했을 때의 미래가치.
    FV = PMT x [(1+r/12)^(12n) - 1] / (r/12).

    예: fv_monthly_saving(500,000, 0.05, 30) ~= 416,000,000.
    """
    if pmt <= 0 or years <= 0:
        return 0
    months = Decimal(str(years)) * Decimal(12)
    r = Decimal(str(annual_rate)) / Decimal(12)
    pmt_d = Decimal(str(pmt))
    if r == 0:
        return _round_won(pmt_d * months)
    factor = (_ONE + r) ** months - _ONE
    return _round_won(pmt_d * factor / r)


def required_monthly_saving(target_fv: float, annual_rate: float, years: float) -> int:
    """목표 미래가치 target_fv를 annual_rate(연 명목수익률)로 years년 만에 모으기 위한 월적립액.
    PMT = FV x (r/12) / [(1+r/12)^(12n) - 1].

    예: required_monthly_saving(400,000,000, 0.05, 20) ~= 970,000.
    """
    if target_fv <= 0 or years <= 0:
        return 0
    months = Decimal(str(years)) * Decimal(12)
    r = Decimal(str(annual_rate)) / Decimal(12)
    fv = Decimal(str(target_fv))
    if r == 0:
        return _round_won(fv / months)
    factor = (_ONE + r) ** months - _ONE
    return _round_won(fv * r / factor)


# ---------------------------------------------------------------------------
# 3.11 인출전략
# ---------------------------------------------------------------------------


def coverage_ratio(guaranteed_income: float, essential_expense: float) -> float:
    """필수지출 충당률 = (국민연금+종신형연금+기타확정소득) / 월 필수지출.

    예: coverage_ratio(1,800,000, 2,500,000) = 0.72.
    """
    if essential_expense <= 0:
        return 0.0
    return float(Decimal(str(guaranteed_income)) / Decimal(str(essential_expense)))


def withdrawal_rate(first_year_withdrawal: float, assets: float) -> float:
    """첫해 인출률 = 첫해 인출액 / 은퇴시점 투자자산. 예: withdrawal_rate(16,000,000, 400,000,000) = 0.04."""
    if assets <= 0:
        return 0.0
    return float(Decimal(str(first_year_withdrawal)) / Decimal(str(assets)))


# ---------------------------------------------------------------------------
# 3.13 전략적 자산배분·리밸런싱 (계산기만, 배분 권고 아님)
# ---------------------------------------------------------------------------


def rebalance_amounts(
    total: float, target_weights: dict[str, float], current_amounts: dict[str, int]
) -> dict[str, int]:
    """목표비중 대비 이동 금액(리밸런싱금액) = 목표금액 - 현재금액. 양수면 채워 넣을 금액,
    음수면 덜어낼 금액이다(자산배분 자체를 권고하지 않고 "참고 시나리오 예시"로만 쓴다).

    예: rebalance_amounts(100,000,000, {"성장":0.6,"안정":0.4}, {"성장":70,000,000,"안정":30,000,000})
    -> {"성장": -10,000,000, "안정": 10,000,000} (성장자산 1천만원을 안정자산으로 이동).
    """
    total_d = Decimal(str(total))
    out: dict[str, int] = {}
    for key, weight in target_weights.items():
        target_amount = _round_won(total_d * Decimal(str(weight)))
        current = current_amounts.get(key, 0)
        out[key] = target_amount - current
    return out


# ---------------------------------------------------------------------------
# 3.3 국민연금 산식 (교육용 추정)
# ---------------------------------------------------------------------------


def _default_payout_rate(months_paid: int) -> float:
    """지급률: 가입 10년(120개월) 50%, 이후 1개월마다 5/12%p 증가, 100% 상한.

    120개월 미만 구간은 법정 최소 가입기간(10년) 미달로 실제로는 노령연금 수급 자체가
    안 되는 경우가 많지만(확인 필요), 이 함수는 "구조적 정확성"을 위해 0에서 50%까지
    선형으로 근사한다(교육용 단순화, 실제 수급 여부와는 다를 수 있음).
    """
    if months_paid <= 0:
        return 0.0
    if months_paid < 120:
        return float(Decimal("0.5") * Decimal(months_paid) / Decimal(120))
    extra_months = months_paid - 120
    rate = Decimal("0.5") + Decimal(extra_months) * (Decimal(5) / Decimal(12)) / Decimal(100)
    return float(min(rate, _ONE))


def national_pension_estimate(
    a_value: float,
    b_value: float,
    months_paid: int,
    months_after_2026: Optional[int] = None,
    payout_rate_rule: Optional[Callable[[int], float]] = None,
) -> int:
    """문서 3.3절 국민연금 기본연금액 산식의 교육용 추정치(월액).

    기본연금액(연) = 1.29 x (A+B) x (P21/P) x (1 + 0.05n/12) x 지급률, 월액 = 연액/12.
    A: 수급 전 3년간 전체 가입자 평균소득월액 평균, B: 본인 가입기간 중 기준소득월액 평균,
    P21/P: 전체 가입월수 중 2026년 이후 가입월수 비율(월수 세분 정보가 없으면 1로 취급),
    n: 20년(240개월) 초과 가입월수, 지급률: `_default_payout_rate` 또는 `payout_rate_rule`.

    예: A=3,190,000, B=3,000,000, 240개월(전부 2026년 이후) -> 월 약 666,000원,
    480개월 -> 월 약 1,332,000원(문서 3.3절 예시, +-0.5% 이내).
    """
    total_months = max(int(months_paid), 0)
    if total_months == 0:
        return 0
    after_2026 = total_months if months_after_2026 is None else max(min(int(months_after_2026), total_months), 0)
    ratio_p21_p = Decimal(after_2026) / Decimal(total_months)
    n = max(total_months - 240, 0)

    rate_fn = payout_rate_rule or _default_payout_rate
    payout_rate = Decimal(str(rate_fn(total_months)))

    a = Decimal(str(a_value))
    b = Decimal(str(b_value))
    growth = _ONE + (Decimal("0.05") * Decimal(n) / Decimal(12))
    annual = Decimal("1.29") * (a + b) * ratio_p21_p * growth * payout_rate
    return _round_won(annual / Decimal(12))


# ---------------------------------------------------------------------------
# 노후자금 부족액 시뮬레이션 (낙관/기준/비관)
# ---------------------------------------------------------------------------


def retirement_gap_projection(
    profile: UserProfile,
    params: PolicyParams,
    *,
    today: date,
    scenarios: Optional[dict[str, dict[str, float]]] = None,
) -> list[RetirementProjection]:
    """낙관/기준/비관 시나리오별 노후자금 격차를 계산한다(문서 2.6, 3.7~3.11).

    - 은퇴시점 생활비: `profile.target_retirement_monthly_expense`가 있으면 그 값을,
      없으면 현재 고정+변동지출을 "현재 생활비"로 보고 물가상승률로 은퇴시점까지 불린다.
    - 확정소득: `profile.assets.pension.expected_national_pension_monthly`가 있으면 그 값을,
      없으면 `national_pension_estimate`로 policy의 A값과 본인 소득(B값 근사)으로 추정한다.
    - 필요자금(PV): 월 부족액을 시나리오 실질수익률로 `LATE_LIFE_END_AGE`까지 인출한다고 보고
      `annuity_pv`로 계산한다.
    - 적립 예상액(FV): 현재 연금성 자산(DB/DC+IRP/연금저축+ISA)이 실질수익률로 불어난 값과,
      현재 저축여력(capacity.net_monthly, 음수면 0)을 은퇴까지 전액 적립한다고 가정한 값의 합.
    - shortfall = 필요자금 - 적립 예상액(양수면 부족, 음수면 여유). 부족하면 격차를 닫기 위한
      추가 월 저축액도 함께 계산한다.

    모든 가정은 반환되는 `RetirementProjection.assumptions`에 적힌다. 특정 상품·자산배분을
    권하지 않는다(`models.LIFECYCLE_DISCLAIMER`).
    """
    scenarios = scenarios or DEFAULT_RETIREMENT_SCENARIOS

    retirement_age = profile.retirement_age or DEFAULT_RETIREMENT_AGE
    age = profile.age if profile.age is not None else retirement_age
    years_to_retirement = max(retirement_age - age, 0)
    withdrawal_years = max(LATE_LIFE_END_AGE - retirement_age, 1)

    current_monthly_cost = profile.target_retirement_monthly_expense
    cost_is_estimated = current_monthly_cost is None
    if current_monthly_cost is None:
        current_monthly_cost = profile.fixed_expenses + profile.variable_expenses

    pension = profile.assets.pension if profile.assets is not None else PensionAssets()
    current_pension_fund = (
        pension.db_dc_balance + pension.irp_pension_savings_balance + pension.isa_balance
    )

    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    savings_capacity = max(capacity.net_monthly, 0)

    a_value, a_needs_verification = _policy_value(params, "national_pension_a_value", 3_190_000)
    verify_note = "확인 필요" if a_needs_verification else "확인됨"

    results: list[RetirementProjection] = []
    for name, cfg in scenarios.items():
        rr = float(cfg["real_return"])
        inflation = float(cfg.get("inflation", 0.02))

        living_cost = retirement_living_cost(current_monthly_cost, inflation, years_to_retirement)

        assumptions = [
            f"{name} 시나리오: 은퇴 후 실질수익률 연 {rr * 100:.1f}%, 물가상승률 연 {inflation * 100:.1f}% 가정"
            "(내부 참고 시나리오, 문서 3.7~3.10 예시 기준).",
            f"은퇴연령 {retirement_age}세, 인출 종료 연령 {LATE_LIFE_END_AGE}세(내부 가정, 장수위험 고려 시 조정 필요).",
        ]
        if cost_is_estimated:
            assumptions.append("목표 은퇴 생활비를 입력하지 않아 현재 고정+변동지출을 현재 생활비로 가정했습니다.")

        if pension.expected_national_pension_monthly is not None:
            guaranteed = pension.expected_national_pension_monthly
            assumptions.append("국민연금 예상액은 프로필에 입력된 값을 그대로 사용했습니다.")
        else:
            guaranteed = national_pension_estimate(
                a_value, profile.monthly_income, pension.national_pension_months_paid,
                pension.national_pension_months_paid,
            )
            assumptions.append(
                f"국민연금 예상액은 policy: national_pension_a_value={a_value:,}원({verify_note})과 "
                "현재 월소득을 B값으로 근사해 문서 3.3절 산식으로 추정한 교육용 값입니다."
            )

        monthly_gap = max(living_cost - guaranteed, 0)
        required_fund_pv = annuity_pv(monthly_gap, rr, withdrawal_years) if monthly_gap > 0 else 0

        projected_fund_fv = fv_lump(current_pension_fund, rr, years_to_retirement) + fv_monthly_saving(
            savings_capacity, rr, years_to_retirement
        )
        assumptions.append(
            f"적립 예상액은 현재 연금성 자산 {current_pension_fund:,}원과 현재 저축여력(월 "
            f"{savings_capacity:,}원)을 은퇴까지 전액 적립한다고 가정한 값의 합입니다."
        )

        shortfall = required_fund_pv - projected_fund_fv
        if shortfall > 0 and years_to_retirement > 0:
            required_saving = required_monthly_saving(shortfall, rr, years_to_retirement)
        else:
            required_saving = 0

        results.append(
            RetirementProjection(
                scenario=name,
                real_return=rr,
                inflation=inflation,
                years_to_retirement=years_to_retirement,
                retirement_age=retirement_age,
                retirement_living_cost=living_cost,
                guaranteed_income_monthly=guaranteed,
                monthly_gap=monthly_gap,
                required_fund_pv=required_fund_pv,
                projected_fund_fv=projected_fund_fv,
                shortfall=shortfall,
                required_monthly_saving=required_saving,
                assumptions=assumptions,
            )
        )
    return results
