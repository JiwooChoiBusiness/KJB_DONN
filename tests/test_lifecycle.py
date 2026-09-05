"""생애주기 층(P7) 통합 테스트: classify_stage, scenarios의 목표 처리, build_lifecycle_view,
그리고 GET /api/lifecycle·POST /api/chat API 계약을 확인한다.

API 테스트는 tests/test_api.py와 별도로 자체 임시 DB·TestClient를 구성한다(기존
test_api.py는 이 작업 범위에서 수정하지 않는 파일이라 그 모듈의 client를 공유하지 않고
독립적으로 검증한다).
"""
from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

# ---------------------------------------------------------------------------
# app.main을 import하기 전에 DB 경로를 임시 파일로 돌린다(SPEC: 실제 data/donn.db를
# 건드리지 않는다). 모듈 전역 상수라 최초 import 시점에 고정되므로 다른 import보다 먼저 온다.
# ---------------------------------------------------------------------------
_TMP_DIR = tempfile.mkdtemp(prefix="donn_test_lifecycle_")
os.environ["DONN_DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
os.environ.setdefault("GEMINI_API_KEYS", "")

from fastapi.testclient import TestClient  # noqa: E402

from app.core.lifecycle import (  # noqa: E402
    classify_stage,
    glide_path_reference,
    income_gap_map,
    stage_priorities,
)
from app.core.scenarios import run_scenarios  # noqa: E402
from app.data import db, policy, synthetic  # noqa: E402
from app.models import (  # noqa: E402
    Assets,
    Goal,
    LifeStage,
    PensionAssets,
    PolicyParams,
    UserProfile,
)
from app.main import app  # noqa: E402
from app.services.lifecycle import build_lifecycle_view, load_thresholds  # noqa: E402

TODAY = date(2026, 9, 6)

db.init_db(db.get_conn())
client = TestClient(app)


def make_profile(**overrides) -> UserProfile:
    defaults = dict(
        id="lifecycle-test", display_name="테스트", monthly_income=3_000_000,
        fixed_expenses=1_000_000, variable_expenses=300_000,
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


# ---------------------------------------------------------------------------
# classify_stage
# ---------------------------------------------------------------------------


def test_classify_stage_25_employee_is_early_career():
    profile = make_profile(age=25, employment="employee")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.EARLY_CAREER
    assert result.label == "사회초년기"
    assert result.reasons
    assert result.priorities and result.avoid and result.accounts_note


def test_classify_stage_33_with_wedding_goal_is_family_formation():
    profile = make_profile(
        age=33,
        goals=[Goal(id="g1", kind="wedding", label="결혼자금", target_amount=30_000_000,
                    target_date=date(2027, 6, 1))],
    )
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.FAMILY_FORMATION
    assert any("목표" in r for r in result.reasons)


def test_classify_stage_52_retirement_near_flag_is_pre_retirement():
    profile = make_profile(age=52, flags=["retirement_near"])
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.PRE_RETIREMENT
    assert any("retirement_near" in r for r in result.reasons)


def test_classify_stage_58_no_income_is_retirement_transition():
    profile = make_profile(age=58, monthly_income=0, income_type="none")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.RETIREMENT_TRANSITION
    assert any("소득이 없어" in r for r in result.reasons)


def test_classify_stage_life_stage_override_wins():
    profile = make_profile(age=25, life_stage_override="active_retirement")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.ACTIVE_RETIREMENT
    assert any("사용자가 지정한" in r for r in result.reasons)


def test_classify_stage_invalid_override_falls_back_to_automatic():
    profile = make_profile(age=25, life_stage_override="not-a-real-stage")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.EARLY_CAREER


def test_stage_priorities_have_no_product_or_company_names():
    for stage in LifeStage:
        priorities, avoid, note = stage_priorities(stage)
        for text in [*priorities, *avoid, note]:
            assert "—" not in text
            assert "추천" not in text


def test_glide_path_reference_monotonic_decreasing_with_age():
    values = [glide_path_reference(age)["growth_asset_pct"] for age in (25, 35, 45, 55, 65, 75)]
    assert values == sorted(values, reverse=True)
    assert glide_path_reference(25)["growth_asset_pct"] == pytest.approx(80.0)
    assert glide_path_reference(75)["growth_asset_pct"] == pytest.approx(25.0)


def test_income_gap_map_none_under_50():
    profile = make_profile(age=45)
    params = PolicyParams(version="t", params={})
    assert income_gap_map(profile, params, today=TODAY) is None


def test_income_gap_map_has_four_periods_over_50():
    profile = make_profile(age=55, assets=Assets(pension=PensionAssets(national_pension_months_paid=200)))
    params = PolicyParams(version="t", params={})
    rows = income_gap_map(profile, params, today=TODAY)
    assert rows is not None
    assert len(rows) == 4
    assert [r["period"] for r in rows] == ["퇴직 전", "퇴직~국민연금 전", "국민연금 개시 후", "후기은퇴"]
    for row in rows:
        assert row["key_question"]
        assert row["numbers"]


# ---------------------------------------------------------------------------
# scenarios: 목표(Goal)가 있는 시나리오
# ---------------------------------------------------------------------------


def test_run_scenarios_goal_outflow_lands_in_correct_month():
    goal = Goal(
        id="g1", kind="wedding", label="결혼자금", target_amount=30_000_000,
        target_date=date(2027, 3, 6), saved_amount=10_000_000,
    )
    profile = make_profile(monthly_income=4_000_000, fixed_expenses=1_500_000, variable_expenses=400_000,
                            goals=[goal])
    params = PolicyParams(version="t", params={})
    results = run_scenarios(profile, params, horizon_months=24, today=TODAY, goals=profile.goals)
    base = next(r for r in results if r.scenario.value == "base")

    # today=2026-09-06, target=2027-03-06 -> 달력 월 차이 6개월
    expected_month = 6
    outflow_point = base.points[expected_month - 1]
    normal_net = base.points[0].net
    assert outflow_point.net == normal_net - (30_000_000 - 10_000_000)
    # 다른 달에는 목표 지출이 반영되지 않아야 한다.
    for p in base.points:
        if p.month != expected_month:
            assert p.net == normal_net
    assert any("목표" in a for a in base.assumptions)


def test_run_scenarios_without_today_ignores_goals_for_backward_compat():
    goal = Goal(id="g1", kind="wedding", label="결혼자금", target_amount=30_000_000, target_date=date(2027, 3, 6))
    profile = make_profile(goals=[goal])
    params = PolicyParams(version="t", params={})
    results = run_scenarios(profile, params, horizon_months=12, goals=profile.goals)
    base = next(r for r in results if r.scenario.value == "base")
    assert not any("목표" in a for a in base.assumptions)


def test_run_scenarios_goal_income_change_applies_from_target_month_onward():
    goal = Goal(
        id="g1", kind="childbirth", label="출산 육아", target_amount=0, target_date=date(2027, 1, 6),
        monthly_income_change_pct=-0.2,
    )
    profile = make_profile(monthly_income=4_000_000, goals=[goal])
    params = PolicyParams(version="t", params={})
    results = run_scenarios(profile, params, horizon_months=12, today=TODAY, goals=profile.goals)
    base = next(r for r in results if r.scenario.value == "base")
    change_month = 4  # 2026-09 -> 2027-01
    for p in base.points:
        if p.month < change_month:
            assert p.income == 4_000_000
        else:
            assert p.income == 3_200_000


# ---------------------------------------------------------------------------
# build_lifecycle_view (service)
# ---------------------------------------------------------------------------


def test_build_lifecycle_view_shape_for_persona_p6():
    profile = synthetic.get_persona("P6")
    params = policy.load_policy_params()
    thresholds = load_thresholds()
    view = build_lifecycle_view(profile, params, thresholds, today=TODAY)

    assert view.profile_id == "P6"
    assert view.stage.stage == LifeStage.PRE_RETIREMENT
    assert {p.scenario for p in view.retirement} == {"낙관", "기준", "비관"}
    assert view.income_gap_map is not None and len(view.income_gap_map) == 4
    assert view.net_worth_path
    assert view.goals
    assert view.assumptions
    assert view.disclaimer == "참고 시나리오이며 특정 상품이나 자산 배분을 권하지 않습니다."


def test_build_lifecycle_view_income_gap_map_none_for_young_persona():
    profile = synthetic.get_persona("P1")
    params = policy.load_policy_params()
    thresholds = load_thresholds()
    view = build_lifecycle_view(profile, params, thresholds, today=TODAY)
    assert view.income_gap_map is None


# ---------------------------------------------------------------------------
# API: GET /api/lifecycle, POST /api/chat
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_session():
    client.delete("/api/session")
    yield
    client.delete("/api/session")


def test_get_lifecycle_returns_404_without_profile():
    r = client.get("/api/lifecycle")
    assert r.status_code == 404


def test_get_lifecycle_for_p6_has_ratios_stage_retirement_and_income_gap_map():
    r = client.post("/api/session/persona/P6")
    assert r.status_code == 200

    r = client.get("/api/lifecycle")
    assert r.status_code == 200
    data = r.json()

    assert data["profile_id"] == "P6"
    assert "liquidity_months" in data["ratios"]
    assert data["stage"]["stage"] == "pre_retirement"
    assert data["stage"]["label"] == "은퇴준비기"
    assert len(data["retirement"]) == 3
    assert {s["scenario"] for s in data["retirement"]} == {"낙관", "기준", "비관"}
    assert data["income_gap_map"] is not None
    assert len(data["income_gap_map"]) == 4
    assert data["disclaimer"] == "참고 시나리오이며 특정 상품이나 자산 배분을 권하지 않습니다."


def test_chat_retirement_question_returns_numbers_and_lifecycle_action():
    client.post("/api/session/persona/P6")
    r = client.post("/api/chat", json={"message": "노후 자금 얼마나 부족해?"})
    assert r.status_code == 200
    data = r.json()
    assert data["action"] == {"type": "open_view", "payload": {"view": "lifecycle"}}
    # 숫자가 코드로 계산되어 답변 문장에 포함되어야 한다(쉼표 구분 금액 표기).
    assert any(ch.isdigit() for ch in data["reply_text"])
    assert "원" in data["reply_text"]
    assert any(c["intent"] == "lifecycle" for c in data["chips"])


def test_chat_liquidity_question_returns_ratio_numbers():
    client.post("/api/session/persona/P1")
    r = client.post("/api/chat", json={"message": "비상금 충분한지 궁금해"})
    assert r.status_code == 200
    data = r.json()
    assert "개월분" in data["reply_text"]
    assert data["action"] == {"type": "open_view", "payload": {"view": "lifecycle"}}


def test_chat_saving_question_without_profile_prompts_onboarding():
    r = client.post("/api/chat", json={"message": "저축률 어떻게 올려?"})
    assert r.status_code == 200
    data = r.json()
    assert data["action"] is None
    assert any(c["intent"] == "onboarding" for c in data["chips"])
