"""행동 카드 조회 서비스 (SPEC 2.3).

`app/core/rules.py`가 만드는 `ActionCard.numbers`는 영문 스네이크케이스 키의
원시 숫자다(core는 순수 계산만 담당하고 화면 표시 형식을 모른다). 화면 계약상
`ActionCard.numbers`는 "라벨 값" 배지로 그대로 렌더링되므로, 여기서 한국어
라벨 + 화면 표시용 문자열로 변환해 돌려준다("포맷은 services에서, core는 원시값
유지"). `app/services/insights.py`도 top_action을 그대로 재사용하기 위해 이
포맷터를 공유한다.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.core import lifecycle as lifecycle_core
from app.core.capacity import compute_capacity
from app.core.rules import evaluate_rules
from app.core.schedule import build_schedule
from app.models import ActionCard, PolicyParams, UserProfile
from app.services import lifecycle as lifecycle_service

# app/core/rules.py의 R0~R6 numbers 키를 모두 다룬다. 새 규칙이 새 키를 추가하면
# format_action_numbers의 else 분기가 방어적으로 처리한다(라벨 없이 원시값 노출).
_WON_LABELS: dict[str, str] = {
    "net_monthly": "이번 달 남는 돈",
    "payoff_amount": "예상 정리 금액",
    "emergency_fund": "현재 비상금",
    "target_emergency_fund": "목표 비상금",
    "gap": "부족한 금액",
    "balance": "잔액",
    "monthly_income_x2": "월소득의 두 배",
    "extra_monthly": "매월 추가상환액",
    "interest_saved": "절감 이자",
    "cost": "신청 비용",
    "monthly_interest_saving": "월 이자 절감",
    "prepay_fee": "중도상환수수료",
    "guaranteed_income": "확정소득",  # R10: 국민연금 등 노후 확정소득 추정액(월)
    "essential_expense": "필수지출",  # R10: 노후 필수지출(월)
}
_PCT_LABELS: dict[str, str] = {
    "target_rate": "대상 금리",
    "rate": "금리",
    "current_rate": "현재 금리",
    "assumed_rate_gap": "가정 금리차",
}
_MONTHS_LABELS: dict[str, str] = {
    "remaining_months": "남은 개월",
    "grace_months": "거치 개월",
    "months_saved": "단축 개월",
    "new_months": "변경 후 개월",
    "breakeven_months": "손익분기 개월",
}
_RATIO_PCT_LABELS: dict[str, str] = {
    "debt_service_ratio": "상환 비율",
    "saving_rate": "저축률",              # R8
    "min_saving_rate": "최소 저축률 기준",  # R8
    "coverage_ratio": "노후소득 충당률",    # R10
    "min_coverage_ratio": "최소 충당률 기준",  # R10
    "max_debt_service_ratio": "원리금상환비율 기준",  # R9 (2026-09-06 리뷰: 라벨 누락)
}


def format_action_number(key: str, value: Any) -> str:
    """ActionCard.numbers의 원시 키/값 하나를 화면 표시용 문자열로 포맷한다.

    `format_action_numbers`(한글 라벨 딕셔너리 생성)와 `app/services/explain.py`
    (슬롯 필링 values, SPEC 2.8)가 같은 포맷 규칙을 공유하기 위해 분리했다.
    """
    if key in _RATIO_PCT_LABELS:
        return f"{round(value * 100)}%"
    if key in _PCT_LABELS:
        return f"{value:.4g}%"
    if key in _MONTHS_LABELS:
        return f"{value:,}개월"
    if key in _WON_LABELS:
        return f"{value:,}원"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (int, float)):
        return f"{value:,}"
    return str(value)


def format_action_numbers(numbers: dict[str, Any]) -> dict[str, str]:
    """ActionCard.numbers(영문 키, 원시 숫자)를 "한글 라벨": "표시 문자열"로 바꾼다."""
    out: dict[str, str] = {}
    for key, value in numbers.items():
        if value is None:
            continue
        if key in _RATIO_PCT_LABELS:
            label = _RATIO_PCT_LABELS[key]
        elif key in _PCT_LABELS:
            label = _PCT_LABELS[key]
        elif key in _MONTHS_LABELS:
            label = _MONTHS_LABELS[key]
        elif key in _WON_LABELS:
            label = _WON_LABELS[key]
        else:
            label = key
        out[label] = format_action_number(key, value)
    return out


def _raw_actions(profile: UserProfile, params: PolicyParams, *, today: date) -> list[ActionCard]:
    """포맷 전(numbers가 영문 키의 원시 숫자 그대로인) 행동 카드 목록.

    `list_actions`가 이 결과의 numbers만 한국어 라벨로 포맷해 돌려준다(아래).
    `app/services/explain.py`의 `explain_action`(SPEC 2.8)은 원시 숫자가 필요해
    (플레이스홀더 값을 직접 포맷해야 하므로) 이 함수를 그대로 재사용한다.
    """
    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    all_thresholds = lifecycle_service.load_thresholds()
    stage_result = lifecycle_core.classify_stage(profile, today=today)
    thresholds = lifecycle_core.thresholds_for_stage(all_thresholds, stage_result.stage)
    return evaluate_rules(profile, schedules, capacity, params, today=today, thresholds=thresholds)


def list_actions(profile: UserProfile, params: PolicyParams, *, today: date) -> list[ActionCard]:
    """프로필의 대출 스케줄과 여력을 계산해 R0~R10 규칙을 평가하고, numbers를
    화면 표시용 한국어 라벨로 포맷해 돌려준다(priority 오름차순, evaluate_rules 유지).

    R8(저축률 미달)·R9(원리금상환비율 초과)·R10(노후소득 충당률 미달)은 생애 단계별
    임계값(`config/thresholds.yaml`)이 있어야 평가되므로(SPEC 2.7), `_raw_actions`가
    단계를 판정하고 임계값을 로드해 `evaluate_rules`에 넘긴다. 이전에는 `thresholds`를
    넘기지 않아 `/api/actions`·`/api/home`에 R8~R10이 전혀 나타나지 않았다(2026-09-06
    리뷰 지적).
    """
    cards = _raw_actions(profile, params, today=today)
    return [card.model_copy(update={"numbers": format_action_numbers(card.numbers)}) for card in cards]
