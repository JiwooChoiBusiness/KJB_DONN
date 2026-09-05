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

from app.core.capacity import compute_capacity
from app.core.rules import evaluate_rules
from app.core.schedule import build_schedule
from app.models import ActionCard, PolicyParams, UserProfile

# app/core/rules.py의 R0~R6 numbers 키를 모두 다룬다. 새 규칙이 새 키를 추가하면
# format_action_numbers의 else 분기가 방어적으로 처리한다(라벨 없이 원시값 노출).
_WON_LABELS: dict[str, str] = {
    "net_monthly": "이번 달 남는 돈",
    "payoff_amount": "예상 정리 금액",
    "emergency_fund": "현재 비상금",
    "target_emergency_fund": "목표 비상금",
    "gap": "부족한 금액",
    "balance": "잔액",
    "monthly_income_x2": "월소득의 2배",
    "extra_monthly": "매월 추가상환액",
    "interest_saved": "절감 이자",
    "cost": "신청 비용",
    "monthly_interest_saving": "월 이자 절감",
    "prepay_fee": "중도상환수수료",
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
_RATIO_PCT_LABELS: dict[str, str] = {"debt_service_ratio": "상환 비율"}


def format_action_numbers(numbers: dict[str, Any]) -> dict[str, str]:
    """ActionCard.numbers(영문 키, 원시 숫자)를 "한글 라벨": "표시 문자열"로 바꾼다."""
    out: dict[str, str] = {}
    for key, value in numbers.items():
        if value is None:
            continue
        if key in _RATIO_PCT_LABELS:
            out[_RATIO_PCT_LABELS[key]] = f"{round(value * 100)}%"
        elif key in _PCT_LABELS:
            out[_PCT_LABELS[key]] = f"{value:.4g}%"
        elif key in _MONTHS_LABELS:
            out[_MONTHS_LABELS[key]] = f"{value:,}개월"
        elif key in _WON_LABELS:
            out[_WON_LABELS[key]] = f"{value:,}원"
        elif isinstance(value, bool):
            out[key] = "예" if value else "아니오"
        elif isinstance(value, (int, float)):
            out[key] = f"{value:,}"
        else:
            out[key] = str(value)
    return out


def list_actions(profile: UserProfile, params: PolicyParams, *, today: date) -> list[ActionCard]:
    """프로필의 대출 스케줄과 여력을 계산해 R0~R6 규칙을 평가하고, numbers를
    화면 표시용 한국어 라벨로 포맷해 돌려준다(priority 오름차순, evaluate_rules 유지)."""
    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    cards = evaluate_rules(profile, schedules, capacity, params, today=today)
    return [card.model_copy(update={"numbers": format_action_numbers(card.numbers)}) for card in cards]
