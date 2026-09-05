"""생애주기 단계 판정과 단계별 콘텐츠 (순수 함수). I/O 없음.

문서(`docs/reference/lifecycle_domain_v1.txt`) 2.1~2.5절을 구현한다. "연령만으로 상품·추천을
정하지 않는다"는 핵심 원칙(2.1절)에 따라 나이 구간을 기본값으로 삼되, 자금 사용시점(목표
목표일)·소득 안정성·부양가족·손실감내도(플래그)·공적연금 준비 신호로 보정한다.

계좌 역할·우선순위 문구는 특정 상품·회사명 없이 제도 일반론으로만 쓴다("추천" 대신 "안내",
"비교", "살펴보기"). 임계값 파일(`config/thresholds.yaml`)은 이 모듈이 직접 읽지 않는다
(I/O 금지) - 이미 파싱된 dict를 받는 `parse_thresholds`만 여기 있고, 실제 파일 읽기는
`app/data/policy.py`가 한다.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from app.core.retirement import coverage_ratio, national_pension_estimate
from app.core.schedule import build_schedule
from app.core.capacity import compute_capacity
from app.models import (
    LIFE_STAGE_LABELS_KR,
    LifeStage,
    LifeStageResult,
    PensionAssets,
    PolicyParams,
    UserProfile,
)

# ---------------------------------------------------------------------------
# 임계값 파일 파싱 (순수 함수, 이미 파싱된 dict만 받는다)
# ---------------------------------------------------------------------------

_THRESHOLD_FIELDS = ("min_liquidity_months", "min_saving_rate", "max_debt_service_ratio", "min_coverage_ratio")


def parse_thresholds(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """`config/thresholds.yaml`을 `yaml.safe_load`한 dict를 stage 값(str) -> 임계값 dict로
    정규화한다. 파일을 직접 읽지 않는 순수 함수(I/O는 app/data/policy.py가 담당)."""
    raw_stages: dict[str, Any] = (data or {}).get("stages", {}) or {}
    out: dict[str, dict[str, Any]] = {}
    for stage_key, fields in raw_stages.items():
        fields = dict(fields or {})
        entry: dict[str, Any] = {}
        for f in _THRESHOLD_FIELDS:
            if fields.get(f) is not None:
                entry[f] = float(fields[f])
        entry["needs_verification"] = bool(fields.get("needs_verification", True))
        entry["note"] = str(fields.get("note", ""))
        out[str(stage_key)] = entry
    return out


def thresholds_for_stage(thresholds: dict[str, dict[str, Any]], stage: LifeStage) -> dict[str, Any]:
    """편의 함수: 전체 임계값 dict에서 특정 단계의 항목만 꺼낸다(없으면 빈 dict)."""
    return thresholds.get(stage.value, {})


# ---------------------------------------------------------------------------
# 2.1 단계 판정
# ---------------------------------------------------------------------------

_AGE_BANDS: list[tuple[int, int, LifeStage]] = [
    (0, 29, LifeStage.EARLY_CAREER),
    (30, 39, LifeStage.FAMILY_FORMATION),
    (40, 49, LifeStage.ASSET_BUILDING),
    (50, 54, LifeStage.PRE_RETIREMENT),
    (55, 64, LifeStage.RETIREMENT_TRANSITION),
    (65, 74, LifeStage.ACTIVE_RETIREMENT),
    (75, 200, LifeStage.LATE_RETIREMENT),
]

_STAGE_ORDER: list[LifeStage] = [band[2] for band in _AGE_BANDS]

_FAMILY_GOAL_KINDS = {"wedding", "childbirth"}
_FAMILY_GOAL_WINDOW_DAYS = 730  # 2년


def _age_band_stage(age: Optional[int]) -> tuple[LifeStage, str]:
    if age is None:
        return LifeStage.EARLY_CAREER, "나이 정보가 없어 사회초년기를 기본값으로 사용했습니다."
    for lo, hi, stage in _AGE_BANDS:
        if lo <= age <= hi:
            band_label = f"{lo}~{hi}세" if hi < 200 else f"{lo}세 이상"
            return stage, f"나이 {age}세는 {LIFE_STAGE_LABELS_KR[stage]} 연령대({band_label})입니다."
    return LifeStage.LATE_RETIREMENT, f"나이 {age}세는 후기은퇴기 연령대입니다."


def _find_family_goal(profile: UserProfile, today: date) -> Optional[str]:
    """2년 이내 목표일인 결혼·출산 목표가 있으면 설명 문장을, 없으면 None을 돌려준다."""
    best: Optional[tuple[int, str]] = None
    for g in profile.goals:
        if g.kind not in _FAMILY_GOAL_KINDS:
            continue
        days = (g.target_date - today).days
        if 0 <= days <= _FAMILY_GOAL_WINDOW_DAYS:
            if best is None or days < best[0]:
                best = (days, f"{g.label}(목표일까지 {days}일 남음) 목표가 있어 가족형성기 우선순위를 반영합니다.")
    return best[1] if best else None


def classify_stage(profile: UserProfile, *, today: date) -> LifeStageResult:
    """나이 구간을 기본값으로 삼고 문서 2.1절 신호로 보정해 생애주기 7단계 중 하나를 정한다.

    적용 순서(뒤에 적용되는 신호가 필요하면 앞선 판정을 덮어쓴다):
    1. 나이 구간(정보 없으면 사회초년기 기본값)
    2. 부양가족이 있는 사회초년기 -> 가족형성기로 보정
    3. 2년 이내 결혼·출산 목표 -> 가족형성기로 보정
    4. `retirement_near` 플래그 -> 최소 은퇴준비기로 상향(이미 그 이후 단계면 유지)
    5. 무소득 + 55세 이상 -> 은퇴전환기
    6. `life_stage_override`가 있으면 위 판정과 무관하게 그 값을 최종 채택(사용자 지정이 최우선)
    """
    stage, reason = _age_band_stage(profile.age)
    reasons = [reason]

    if profile.dependents and profile.dependents >= 1 and stage == LifeStage.EARLY_CAREER:
        stage = LifeStage.FAMILY_FORMATION
        reasons.append(f"부양가족 {profile.dependents}명이 있어 가족형성기 우선순위를 함께 반영합니다.")

    family_goal_reason = _find_family_goal(profile, today)
    if family_goal_reason is not None:
        stage = LifeStage.FAMILY_FORMATION
        reasons.append(family_goal_reason)

    if "retirement_near" in profile.flags:
        if _STAGE_ORDER.index(stage) < _STAGE_ORDER.index(LifeStage.PRE_RETIREMENT):
            stage = LifeStage.PRE_RETIREMENT
        reasons.append("은퇴가 가깝다는 신호(retirement_near)가 있어 은퇴준비기 우선순위를 반영합니다.")

    if profile.income_type == "none" and profile.age is not None and profile.age >= 55:
        stage = LifeStage.RETIREMENT_TRANSITION
        reasons.append("55세 이상이며 현재 소득이 없어 은퇴전환기로 판단했습니다.")

    if profile.life_stage_override:
        try:
            override_stage = LifeStage(profile.life_stage_override)
        except ValueError:
            override_stage = None
        if override_stage is not None:
            stage = override_stage
            reasons.append(
                f"사용자가 지정한 생애 단계({LIFE_STAGE_LABELS_KR[override_stage]})를 자동 판정보다 우선 적용했습니다."
            )

    priorities, avoid, accounts_note = stage_priorities(stage)
    return LifeStageResult(
        stage=stage,
        label=LIFE_STAGE_LABELS_KR[stage],
        reasons=reasons,
        priorities=priorities,
        avoid=avoid,
        accounts_note=accounts_note,
    )


# ---------------------------------------------------------------------------
# 2.3 7단계 생애주기 실행표 (교육 문구, 상품·회사명 없음)
# ---------------------------------------------------------------------------

_STAGE_CONTENT: dict[LifeStage, tuple[list[str], list[str], str]] = {
    LifeStage.EARLY_CAREER: (
        ["비상자금 마련", "학자금·신용대출 관리", "국민연금 가입기간 확보"],
        ["생활자금을 IRP에 과도하게 묶어두기"],
        "연금계좌(IRP·연금저축)는 장기 노후 자금, ISA는 중기 목적자금, 일반 계좌는 생활 유동성 "
        "용도로 역할을 나눠볼 수 있습니다. DC형 퇴직연금이 있다면 운용 현황을 확인해보고, 소액으로 "
        "연금저축·IRP 가입 여부를 살펴볼 수 있습니다.",
    ),
    LifeStage.FAMILY_FORMATION: (
        ["주거·출산·육아 자금 준비", "노후 저축과 병행하기"],
        ["주택 자금 마련에 비상자금과 연금자산을 모두 투입하기"],
        "ISA는 결혼·주거 등 중기 목적자금으로, 연금계좌는 은퇴 전용으로 구분해서 관리하는 것을 "
        "살펴볼 수 있습니다.",
    ),
    LifeStage.ASSET_BUILDING: (
        ["소득 증가분 저축 늘리기", "교육비 지출 상한 정하기"],
        ["자녀 학비 지원을 이유로 은퇴 저축을 장기간 중단하기"],
        "DC형 퇴직연금·IRP·연금저축 납입을 늘리는 방법을 검토해볼 수 있고, ISA는 만기 시 연금계좌로 "
        "옮기는 방법(연금계좌 이체)도 비교해볼 수 있습니다.",
    ),
    LifeStage.PRE_RETIREMENT: (
        ["퇴직 시점과 국민연금 개시 전 소득공백 계산", "안정자산 비중을 계획적으로 늘리기"],
        ["퇴직 직전에 투자 위험을 급격히 높이기"],
        "퇴직급여 수령 방법(일시금 또는 연금)을 미리 비교해보는 시기입니다.",
    ),
    LifeStage.RETIREMENT_TRANSITION: (
        ["퇴직급여·재취업 소득·연금을 월 현금흐름으로 연결", "국민연금 수급 개시 시점 결정"],
        ["퇴직금을 한 번에 소비하거나 한 곳에 집중 투자하기"],
        "IRP 연금 수령 방식과 생활비 용도별 자금 구분(버킷)을 살펴보는 시기입니다.",
    ),
    LifeStage.ACTIVE_RETIREMENT: (
        ["생활비·여행·의료비 균형 관리"],
        ["자산 가격이 오른 시기에 생활수준을 영구적으로 높이기"],
        "종신소득(국민연금 등)을 필수 생활비에 먼저 연결하고, 주택연금 같은 제도를 살펴볼 수 있습니다.",
    ),
    LifeStage.LATE_RETIREMENT: (
        ["간병·주거·의사결정 지원 준비"],
        ["구조가 복잡하거나 위험이 높은 상품을 다수 유지하기"],
        "자동이체 방식의 현금흐름과 의료비·상속 관련 자금 구조를 단순하게 정리해두는 것이 도움이 됩니다.",
    ),
}


def stage_priorities(stage: LifeStage) -> tuple[list[str], list[str], str]:
    """단계별 (재무 우선순위, 피해야 할 행동, 계좌 역할 안내) 3종을 돌려준다(문서 2.3절 표)."""
    return _STAGE_CONTENT[stage]


# ---------------------------------------------------------------------------
# 2.4 글라이드패스 참고 모델
# ---------------------------------------------------------------------------

_GLIDE_PATH_POINTS: list[tuple[int, float]] = [(25, 80.0), (35, 75.0), (45, 65.0), (55, 50.0), (65, 35.0), (75, 25.0)]


def glide_path_reference(age: Optional[int]) -> dict[str, Any]:
    """문서 2.4절 TDF 글라이드패스 표를 나이에 맞춰 선형보간한 "참고 모델" 값을 돌려준다.

    특정 자산배분을 권하는 것이 아니라 교육용 예시임을 명시한다(문서 2.4 각주, DONN 규칙 2).
    """
    a = age if age is not None else 40
    pts = _GLIDE_PATH_POINTS
    if a <= pts[0][0]:
        pct = pts[0][1]
    elif a >= pts[-1][0]:
        pct = pts[-1][1]
    else:
        pct = pts[-1][1]
        for (a0, p0), (a1, p1) in zip(pts, pts[1:]):
            if a0 <= a <= a1:
                pct = p0 + (p1 - p0) * (a - a0) / (a1 - a0)
                break
    return {
        "age": a,
        "growth_asset_pct": round(pct, 1),
        "label": "참고 모델(TDF 글라이드패스 교육용 예시)",
        "note": "운용사·상품별 실제 비중은 다를 수 있으며 특정 자산배분 비중을 권하지 않습니다.",
    }


# ---------------------------------------------------------------------------
# 2.5 소득공백 지도 (50세 이상)
# ---------------------------------------------------------------------------

NATIONAL_PENSION_START_AGE_ASSUMED = 65  # 내부 가정(문서 2.5절 구간 정의용, 확인 필요)


def _policy_value(params: PolicyParams, key: str, default: Any) -> tuple[Any, bool]:
    p = params.params.get(key)
    if p is None:
        return default, True
    return p.value, p.needs_verification


def income_gap_map(
    profile: UserProfile, params: PolicyParams, *, today: date
) -> Optional[list[dict[str, Any]]]:
    """문서 2.5절 소득공백 지도(4개 구간). 50세 미만 프로필에는 의미가 없어 None을 돌려준다."""
    if profile.age is None or profile.age < 50:
        return None

    retirement_age = profile.retirement_age or NATIONAL_PENSION_START_AGE_ASSUMED
    schedules = [build_schedule(loan) for loan in profile.loans]
    capacity = compute_capacity(profile, schedules)
    pension = profile.assets.pension if profile.assets is not None else PensionAssets()

    a_value, _needs_verification = _policy_value(params, "national_pension_a_value", 3_190_000)
    if pension.expected_national_pension_monthly is not None:
        guaranteed = pension.expected_national_pension_monthly
    else:
        guaranteed = national_pension_estimate(
            a_value, profile.monthly_income, pension.national_pension_months_paid,
            pension.national_pension_months_paid,
        )

    essential_expense = profile.fixed_expenses
    bridge_years = max(NATIONAL_PENSION_START_AGE_ASSUMED - retirement_age, 0)
    pension_fund = pension.db_dc_balance + pension.irp_pension_savings_balance + pension.isa_balance
    cov_ratio = coverage_ratio(guaranteed, essential_expense) if essential_expense > 0 else None

    return [
        {
            "period": "퇴직 전",
            "inflow": "급여·보너스",
            "outflow": "저축·부채상환",
            "key_question": "마지막 5년 저축률을 얼마나 높일 수 있는가?",
            "numbers": {
                "월 급여": profile.monthly_income,
                "월 부채상환": capacity.debt_service,
                "월 저축여력": max(capacity.net_monthly, 0),
            },
        },
        {
            "period": "퇴직~국민연금 전",
            "inflow": "퇴직연금·재취업 소득·개인연금",
            "outflow": "생활비·건강보험료",
            "key_question": "몇 년의 브리지 자금이 필요한가?",
            "numbers": {
                "브리지 필요 기간(년)": bridge_years,
                "퇴직연금 등 자산": pension_fund,
            },
        },
        {
            "period": "국민연금 개시 후",
            "inflow": "국민연금·퇴직연금·개인연금",
            "outflow": "생활비·의료비·주거비",
            "key_question": "필수지출 중 종신소득 충당 비율은?",
            "numbers": {
                "확정소득 추정": guaranteed,
                "필수지출": essential_expense,
                "충당률": cov_ratio,
            },
        },
        {
            "period": "후기은퇴",
            "inflow": "연금·주택연금 활용 가능",
            "outflow": "의료비·간병비",
            "key_question": "자산관리 단순화와 지원체계는 준비되어 있는가?",
            "numbers": {
                "부양가족 수": profile.dependents,
            },
        },
    ]
