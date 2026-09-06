"""생애주기 층(P7) 통합 테스트: classify_stage, scenarios의 목표 처리, build_lifecycle_view,
그리고 GET /api/lifecycle·POST /api/chat API 계약을 확인한다.

API 테스트는 tests/test_api.py와 별도로 자체 임시 DB·TestClient를 구성한다(기존
test_api.py는 이 작업 범위에서 수정하지 않는 파일이라 그 모듈의 client를 공유하지 않고
독립적으로 검증한다).
"""
from __future__ import annotations

import os
import re
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
from app.core.scenarios import run_lifecycle_projection, run_scenarios  # noqa: E402
from app.core.schedule import build_schedule  # noqa: E402
from app.data import db, policy, synthetic  # noqa: E402
from app.models import (  # noqa: E402
    Assets,
    Goal,
    LifeStage,
    Loan,
    LoanType,
    PensionAssets,
    PolicyParams,
    RateType,
    RepayMethod,
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
    assert any("은퇴가 가깝다" in r for r in result.reasons)


def test_classify_stage_58_no_income_is_retirement_transition():
    profile = make_profile(age=58, monthly_income=0, income_type="none")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.RETIREMENT_TRANSITION
    assert any("소득이 없어" in r for r in result.reasons)


def test_classify_stage_signals_never_move_stage_backward_income_none():
    """SEV3 #16: 무소득 신호는 단계를 '최소 은퇴전환기로' 앞당길 뿐, 나이로 이미 더
    나중 단계(70세 -> 활동은퇴기)로 판정된 것을 은퇴전환기로 되돌리면 안 된다."""
    profile = make_profile(age=70, monthly_income=0, income_type="none")
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.ACTIVE_RETIREMENT


def test_classify_stage_signals_never_move_stage_backward_wedding_goal():
    """SEV3 #16: 2년 이내 결혼 목표 신호는 단계를 '최소 가족형성기로' 앞당길 뿐, 나이로
    이미 더 나중 단계(45세 -> 자산축적기)로 판정된 것을 가족형성기로 되돌리면 안 된다."""
    profile = make_profile(
        age=45,
        goals=[Goal(id="g1", kind="wedding", label="결혼자금", target_amount=30_000_000,
                    target_date=date(2027, 6, 1))],
    )
    result = classify_stage(profile, today=TODAY)
    assert result.stage == LifeStage.ASSET_BUILDING


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
# run_lifecycle_projection: 원금만 잔액에서 차감(SEV3 #18)
# ---------------------------------------------------------------------------


def test_run_lifecycle_projection_debt_after_year1_uses_principal_only():
    """1년 뒤 부채 잔액은 원리금(전체 납입액)이 아니라 그 해 스케줄의 "원금" 합계만큼만
    줄어야 한다(이자는 잔액을 줄이지 않는다)."""
    loan = Loan(
        id="L1", name="신용대출", loan_type=LoanType.CREDIT,
        principal=10_000_000, balance=10_000_000, annual_rate=8.0,
        rate_type=RateType.FIXED, repay_method=RepayMethod.EQUAL_PAYMENT,
        remaining_months=36,
    )
    profile = make_profile(
        id="proj-test", age=40, monthly_income=3_000_000,
        fixed_expenses=1_000_000, variable_expenses=300_000, loans=[loan],
    )
    params = PolicyParams(version="t", params={})
    sched = build_schedule(loan)
    year1_principal = sum(r.principal for r in sched.rows[:12])
    assert 0 < year1_principal < loan.balance  # 상식적인 범위인지 먼저 확인

    rows = run_lifecycle_projection(
        profile, params, today=TODAY, until_age=42, scenario_returns={"base": 0.0},
    )
    row_age41 = next(r for r in rows if r["scenario"] == "base" and r["age"] == 41)
    assert row_age41["debt_balance"] == loan.balance - year1_principal
    # 원리금(원금+이자) 전체를 뺐다면 이보다 훨씬 작아야 하므로, 회귀 확인 차 부등식도 남긴다.
    total_first_year_payment = sum(r.payment for r in sched.rows[:12])
    assert year1_principal < total_first_year_payment
    assert row_age41["debt_balance"] > loan.balance - total_first_year_payment


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


def test_chat_retirement_reply_reframes_unaffordable_required_saving_for_p6():
    """SEV3 #25: P6은 은퇴(55세)가 국민연금 개시(65세)보다 훨씬 빨라 required_monthly_saving이
    이번 달 실제 여력(capacity.net_monthly)을 크게 넘는다. 이런 경우 "매달 이만큼
    저축하세요"라는 실행 불가능한 지시 대신 부족액/필요 자금 크기만 알리고 생애 흐름
    화면을 보라고 안내해야 한다."""
    client.post("/api/session/persona/P6")
    r = client.post("/api/chat", json={"message": "노후 자금 얼마나 부족해?"})
    assert r.status_code == 200
    data = r.json()
    reply = data["reply_text"]
    assert "월 부족액" in reply
    assert "필요 자금" in reply
    assert "생애 흐름" in reply
    assert "추가로 저축하는 방법이 안내됩니다" not in reply, (
        "실제로 감당 불가능한 저축액을 지시문처럼 제시하면 안 됨"
    )


def test_chat_liquidity_question_returns_ratio_numbers():
    client.post("/api/session/persona/P1")
    r = client.post("/api/chat", json={"message": "비상금 충분한지 궁금해"})
    assert r.status_code == 200
    data = r.json()
    assert "개월분" in data["reply_text"]
    assert data["action"] == {"type": "open_view", "payload": {"view": "lifecycle"}}


def test_actions_api_for_p6_includes_r8_or_r10_with_formatted_numbers():
    """SEV3 #6: list_actions가 이제 생애 단계 임계값을 로드해 evaluate_rules에 넘기므로
    (이전에는 thresholds를 넘기지 않아 R8/R9/R10이 /api/actions·/api/home에 전혀 나타나지
    않았다), P6(52세, 은퇴가 가까움) 프로필에서 R8(저축률 미달) 또는 R10(노후소득 충당률
    미달)이 나타나야 한다. SEV3 #21: 그 카드의 numbers는 영문 스네이크케이스 키나 원시
    비율(0.19 같은) 없이 한글 라벨과 %/원이 붙은 표시용 문자열이어야 한다."""
    r = client.post("/api/session/persona/P6")
    assert r.status_code == 200

    r2 = client.get("/api/actions")
    assert r2.status_code == 200
    cards = r2.json()
    rule_ids = {c["rule_id"] for c in cards}
    assert rule_ids & {"R8", "R10"}, f"P6에 R8/R10 중 아무것도 없음: {rule_ids}"

    raw_english_keys = {
        "saving_rate", "min_saving_rate", "coverage_ratio", "min_coverage_ratio",
        "guaranteed_income", "essential_expense",
    }
    for card in cards:
        if card["rule_id"] not in ("R8", "R10"):
            continue
        keys = set(card["numbers"].keys())
        assert not (keys & raw_english_keys), f"{card['rule_id']}: 영문 키 노출 -> {keys}"
        for label, value in card["numbers"].items():
            assert isinstance(value, str)
            assert not re.fullmatch(r"-?\d*\.\d+", value), f"{card['rule_id']}.{label}: 원시 비율 노출 -> {value!r}"
            assert value.endswith(("%", "원")), f"{card['rule_id']}.{label}: 단위 표시 없음 -> {value!r}"

    # /api/home에도 같은 배선(list_actions)을 타므로 R8/R10이 top_action이면 함께 반영된다.
    r3 = client.get("/api/home")
    assert r3.status_code == 200


def test_chat_saving_question_without_profile_prompts_onboarding():
    r = client.post("/api/chat", json={"message": "저축률 어떻게 올려?"})
    assert r.status_code == 200
    data = r.json()
    assert data["action"] is None
    assert any(c["intent"] == "onboarding" for c in data["chips"])
