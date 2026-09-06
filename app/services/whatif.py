"""계산형 자유 질의(what-if) 도구 (SPEC 2.13).

순수 계산은 `app/core`(`loan.py`, `retirement.py`)에 있고, 이 서비스는 채팅 슬롯을 골라
그 함수들을 조합해 `WhatIfResult`(app.models)를 만든다. 숫자는 전부 core 계산값이고, 이
모듈은 LLM을 호출하지 않는다(문장 생성은 `app/services/explain.py`가 별도로 맡는다).

대상 대출 선택: `loan_hint`(대출 종류)가 프로필의 대출과 일치하면 그 대출을, 없으면
최고금리 대출을 고른다(은퇴 나이 도구는 대출이 필요 없다).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core import loan as loan_core
from app.core import retirement as retirement_core
from app.core import schedule as schedule_core
from app.models import Loan, PolicyParams, UserProfile, WhatIfResult

_LOAN_TYPE_LABELS_KR: dict[str, str] = {
    "credit": "신용대출",
    "mortgage": "주택담보대출",
    "jeonse": "전세자금대출",
    "student": "학자금대출",
    "card_loan": "카드론",
    "overdraft": "마이너스통장",
    "policy": "정책상품대출",
    "other": "기타 대출",
}


def _pick_target_loan(profile: UserProfile, loan_hint: Optional[str]) -> tuple[Optional[Loan], bool]:
    """loan_hint(대출 종류 문자열)와 일치하는 대출을 우선 고르고, 없으면 최고금리 대출을
    고른다. 두 번째 반환값은 loan_hint로 골랐는지(True) 최고금리로 골랐는지(False)."""
    if not profile.loans:
        return None, False
    if loan_hint:
        found = next((l for l in profile.loans if l.loan_type.value == loan_hint), None)
        if found is not None:
            return found, True
    best = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
    return best, False


def _loan_label(loan: Loan) -> str:
    """실명 없는 대출 라벨. 예: "카드론(잔액 2,400,000원, 17.5%)"."""
    type_label = _LOAN_TYPE_LABELS_KR.get(loan.loan_type.value, "기타 대출")
    return f"{type_label}(잔액 {loan.balance:,}원, {loan.annual_rate:.4g}%)"


def _target_loan_assumption(loan: Loan, by_hint: bool) -> str:
    label = _LOAN_TYPE_LABELS_KR.get(loan.loan_type.value, "기타 대출")
    if by_hint:
        return f"말씀하신 대출 종류({label})를 대상으로 계산했어요."
    return f"어떤 대출인지 특정하지 않아서 금리가 가장 높은 대출({label})을 대상으로 계산했어요."


def _extra_payment_result(profile: UserProfile, loan_hint: Optional[str], extra_monthly: int) -> Optional[WhatIfResult]:
    target_loan, by_hint = _pick_target_loan(profile, loan_hint)
    if target_loan is None:
        return None
    amount = max(int(extra_monthly), 0)
    base_schedule = schedule_core.build_schedule(target_loan)
    effect = loan_core.extra_payment_effect(target_loan, amount)

    return WhatIfResult(
        tool="extra_payment",
        target_loan_label=_loan_label(target_loan),
        inputs={"extra_monthly": amount},
        before={"months": base_schedule.months, "total_interest": base_schedule.total_interest},
        after={
            "months": effect["new_months"],
            "total_interest": base_schedule.total_interest - effect["interest_saved"],
        },
        deltas={"months_saved": effect["months_saved"], "interest_saved": effect["interest_saved"]},
        assumptions=[
            f"매달 {amount:,}원을 원금에 추가로 상환한다고 가정했어요.",
            _target_loan_assumption(target_loan, by_hint),
        ],
        scenario_params={"loan_id": target_loan.id},
    )


def _lump_sum_result(
    profile: UserProfile, params_policy: PolicyParams, loan_hint: Optional[str], amount: int, *, today: date,
) -> Optional[WhatIfResult]:
    target_loan, by_hint = _pick_target_loan(profile, loan_hint)
    if target_loan is None:
        return None
    amount = max(int(amount), 0)
    pay_amount = min(amount, target_loan.balance)
    fee = loan_core.prepay_fee(target_loan, pay_amount, today, params_policy)
    base_schedule = schedule_core.build_schedule(target_loan)
    effect = loan_core.lump_sum_effect(target_loan, amount, fee)

    assumptions = [f"{amount:,}원을 한 번에 갚는다고 가정했어요.", _target_loan_assumption(target_loan, by_hint)]
    if fee > 0:
        assumptions.append(f"중도상환수수료 {fee:,}원을 반영했어요.")

    return WhatIfResult(
        tool="lump_sum",
        target_loan_label=_loan_label(target_loan),
        inputs={"amount": amount},
        before={"months": base_schedule.months, "total_interest": base_schedule.total_interest},
        after={
            "months": effect["new_months"],
            "total_interest": base_schedule.total_interest - effect["interest_saved"],
            "fee": fee,
        },
        deltas={"months_saved": effect["months_saved"], "interest_saved": effect["interest_saved"]},
        assumptions=assumptions,
        scenario_params={"loan_id": target_loan.id},
    )


def _refinance_result(
    profile: UserProfile, params_policy: PolicyParams, loan_hint: Optional[str], new_rate: float, *, today: date,
) -> Optional[WhatIfResult]:
    target_loan, by_hint = _pick_target_loan(profile, loan_hint)
    if target_loan is None:
        return None
    new_rate = float(new_rate)
    fee = loan_core.prepay_fee(target_loan, target_loan.balance, today, params_policy)
    effect = loan_core.refinance_compare(target_loan, new_rate, target_loan.remaining_months, fee)

    assumptions = [
        f"금리를 연 {new_rate:.4g}%로 낮춰 대환한다고 가정했어요.",
        _target_loan_assumption(target_loan, by_hint),
    ]
    if fee > 0:
        assumptions.append(f"중도상환수수료 {fee:,}원을 반영했어요.")

    return WhatIfResult(
        tool="refinance",
        target_loan_label=_loan_label(target_loan),
        inputs={"new_rate": new_rate},
        before={"total_interest": effect["current_total_interest"], "monthly": effect["monthly_before"]},
        after={"total_interest": effect["new_total_interest"], "monthly": effect["monthly_after"], "fee": fee},
        deltas={
            "monthly_delta": effect["monthly_after"] - effect["monthly_before"],
            "total_cost_delta": effect["new_total_interest"] - effect["current_total_interest"],
            "breakeven_months": effect["breakeven_months"],
        },
        assumptions=assumptions,
        scenario_params={
            "loan_id": target_loan.id,
            "amount": target_loan.balance,
            "term_months": target_loan.remaining_months,
        },
    )


def _retirement_age_result(
    profile: UserProfile, params_policy: PolicyParams, age: int, *, today: date,
) -> Optional[WhatIfResult]:
    age = int(age)
    before_list = retirement_core.retirement_gap_projection(profile, params_policy, today=today)
    if not before_list:
        return None
    before = next((p for p in before_list if p.scenario == "기준"), before_list[0])

    new_profile = profile.model_copy(update={"retirement_age": age})
    after_list = retirement_core.retirement_gap_projection(new_profile, params_policy, today=today)
    if not after_list:
        return None
    after = next((p for p in after_list if p.scenario == "기준"), after_list[0])

    return WhatIfResult(
        tool="retirement_age",
        target_loan_label=None,
        inputs={"retirement_age": age},
        before={
            "retirement_age": before.retirement_age,
            "shortfall": before.shortfall,
            "monthly_gap": before.monthly_gap,
            "required_monthly_saving": before.required_monthly_saving,
        },
        after={
            "retirement_age": after.retirement_age,
            "shortfall": after.shortfall,
            "monthly_gap": after.monthly_gap,
            "required_monthly_saving": after.required_monthly_saving,
        },
        deltas={
            "shortfall_delta": after.shortfall - before.shortfall,
            "monthly_gap_delta": after.monthly_gap - before.monthly_gap,
            "required_monthly_saving_delta": after.required_monthly_saving - before.required_monthly_saving,
        },
        assumptions=[f"은퇴 나이를 {age}세로 바꿨을 때를 가정했어요(기준 시나리오 기준입니다)."] + list(after.assumptions[:1]),
        scenario_params={"scenario": after.scenario},
    )


def run_whatif(
    profile: Optional[UserProfile], params_policy: PolicyParams, slots: dict[str, Any], *, today: date,
) -> Optional[WhatIfResult]:
    """SPEC 2.13: 슬롯 중 하나를 골라 해당 도구를 계산한다. 슬롯이 없거나(모두 None) 프로필이
    없으면, 또는 계산에 필요한 대출/자산 정보가 없으면 None을 돌려준다(호출부가 되묻거나
    안내한다).

    우선순위: retirement_age(대출 불필요) -> new_rate(대환) -> lump_sum(일시 상환) ->
    extra_monthly(추가 상환). 슬롯이 여러 개 함께 오는 경우는 규칙 파서·추출 스키마 설계상
    거의 없지만, 방어적으로 이 순서를 고정한다.
    """
    if profile is None:
        return None

    loan_hint = slots.get("loan_hint")
    retirement_age = slots.get("retirement_age")
    new_rate = slots.get("new_rate")
    lump_sum = slots.get("lump_sum")
    extra_monthly = slots.get("extra_monthly")

    if retirement_age not in (None, ""):
        return _retirement_age_result(profile, params_policy, retirement_age, today=today)
    if new_rate not in (None, ""):
        return _refinance_result(profile, params_policy, loan_hint, new_rate, today=today)
    if lump_sum not in (None, ""):
        return _lump_sum_result(profile, params_policy, loan_hint, lump_sum, today=today)
    if extra_monthly not in (None, ""):
        return _extra_payment_result(profile, loan_hint, extra_monthly)
    return None
