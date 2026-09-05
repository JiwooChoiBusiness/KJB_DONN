"""생애주기 층(P7) 조합 서비스: ratios/lifecycle/retirement/scenarios core를 묶어
`LifecycleView`를 만든다 (SPEC 2.7). LLM 호출 없음(전부 코드 계산).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

from app.core.lifecycle import classify_stage, glide_path_reference, income_gap_map, parse_thresholds, thresholds_for_stage
from app.core.ratios import compute_ratios
from app.core.retirement import LATE_LIFE_END_AGE, retirement_gap_projection
from app.core.schedule import build_schedule
from app.core.scenarios import run_lifecycle_projection
from app.models import LIFECYCLE_DISCLAIMER, LifecycleView, PolicyParams, UserProfile

DEFAULT_THRESHOLDS_PATH = "config/thresholds.yaml"


def load_thresholds(path: str = DEFAULT_THRESHOLDS_PATH) -> dict[str, dict[str, Any]]:
    """생애 단계별 재무비율 임계값(config/thresholds.yaml)을 읽어 정규화한다.

    파일 I/O만 이 함수가 담당하고, 파싱·정규화는 순수 함수인
    `app.core.lifecycle.parse_thresholds`에 위임한다(SPEC: I/O는 core 밖에서, app/services가
    core와 조합한다).
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return parse_thresholds(data)


def _goal_view(profile: UserProfile) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in sorted(profile.goals, key=lambda x: (x.priority, x.target_date, x.id)):
        progress_pct = round(min(g.saved_amount / g.target_amount, 1.0) * 100, 1) if g.target_amount > 0 else None
        out.append({
            "id": g.id,
            "kind": g.kind,
            "label": g.label,
            "target_amount": g.target_amount,
            "saved_amount": g.saved_amount,
            "target_date": g.target_date.isoformat(),
            "priority": g.priority,
            "progress_pct": progress_pct,
        })
    return out


def build_lifecycle_view(
    profile: UserProfile,
    params: PolicyParams,
    thresholds: dict[str, dict[str, Any]],
    *,
    today: date,
) -> LifecycleView:
    """재무비율, 생애 단계, 노후자금 시나리오(낙관/기준/비관), 소득공백 지도(50세 이상),
    순자산 경로, 목표 진행률을 한데 모은다. 표시용 문자열과 원시 숫자를 함께 담고,
    사용한 모든 가정과 확인 필요 여부를 `assumptions`에 남긴다.
    """
    stage_result = classify_stage(profile, today=today)
    stage_thresholds = thresholds_for_stage(thresholds, stage_result.stage)

    schedules = [build_schedule(loan) for loan in profile.loans]
    ratios = compute_ratios(profile, schedules, stage_thresholds)

    retirement = retirement_gap_projection(profile, params, today=today)
    gap_map = income_gap_map(profile, params, today=today)
    glide = glide_path_reference(profile.age)

    scenario_returns = {p.scenario: p.real_return for p in retirement}
    start_age = profile.age if profile.age is not None else 40
    until_age = max(LATE_LIFE_END_AGE, start_age + 1)
    net_worth_path = run_lifecycle_projection(
        profile, params, today=today, until_age=until_age, scenario_returns=scenario_returns,
    )

    goals_view = _goal_view(profile)

    assumptions: list[str] = []
    assumptions.append(f"생애 단계 판정 근거: {' '.join(stage_result.reasons)}")
    if stage_thresholds:
        note = stage_thresholds.get("note", "")
        verify = "확인 필요" if stage_thresholds.get("needs_verification", True) else "확인됨"
        assumptions.append(f"{stage_result.label} 재무비율 기준값 출처: {note} ({verify})")
    else:
        assumptions.append(f"{stage_result.label}에 대한 생애 단계별 임계값이 설정되어 있지 않습니다.")

    seen = set(assumptions)
    for proj in retirement:
        for a in proj.assumptions:
            if a not in seen:
                assumptions.append(a)
                seen.add(a)

    assumptions.append(glide["note"])
    assumptions.append(
        "순자산 경로는 연 단위 근사이며, 부채 잔액은 현재 원리금상환액을 기준으로 선형 근사했습니다."
    )

    return LifecycleView(
        profile_id=profile.id,
        ratios=ratios,
        stage=stage_result,
        retirement=retirement,
        income_gap_map=gap_map,
        net_worth_path=net_worth_path,
        goals=goals_view,
        assumptions=assumptions,
        disclaimer=LIFECYCLE_DISCLAIMER,
    )
