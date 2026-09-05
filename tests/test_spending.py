"""app/core/spending.py, app/services/spending.py, /api/spending 엔드포인트 테스트 (P5).

DB가 필요한 API 라운드트립 테스트만 tests/test_api.py와 같은 패턴(임시 DB 경로를
app.main import 전에 환경변수로 지정)을 쓴다. app.data.db.DB_PATH는 프로세스
전역에서 최초 import 시점에 고정되므로, 전체 스위트를 함께 돌리면 tests/test_api.py가
먼저 지정한 임시 DB를 그대로 공유한다(무해함 - 이 파일은 spending_features 테이블만
쓰고, 세션 프로필은 각 테스트가 명시적으로 다시 지정한다). 이 파일만 단독 실행하면
아래에서 지정하는 이 파일 전용 임시 DB가 쓰인다.
"""
from __future__ import annotations

import os
import tempfile
from datetime import date

_TMP_DIR = tempfile.mkdtemp(prefix="donn_test_spending_")
os.environ.setdefault("DONN_DB_PATH", os.path.join(_TMP_DIR, "test.db"))
os.environ.setdefault("GEMINI_API_KEYS", "")
os.environ.setdefault("GEMINI_MODEL_CHAIN", "")

import pytest  # noqa: E402

from app.core import spending as spending_core  # noqa: E402
from app.data import synthetic  # noqa: E402
from app.models import (  # noqa: E402
    Loan,
    LoanType,
    RateType,
    RepayMethod,
    SpendingCategory,
    Transaction,
    UserProfile,
)

END = date(2026, 9, 6)


def make_tx(id_: str, d: date, amount: int, merchant: str, kind: str = "card",
            category: str | None = None, memo: str = "") -> Transaction:
    return Transaction(id=id_, date=d, amount=amount, merchant=merchant, category=category,
                        kind=kind, memo=memo)


def make_profile(**overrides) -> UserProfile:
    defaults = dict(
        id="test-profile", display_name="테스트", monthly_income=3_000_000,
        fixed_expenses=1_000_000, variable_expenses=500_000, loans=[], flags=[],
    )
    defaults.update(overrides)
    return UserProfile(**defaults)


# ---------------------------------------------------------------------------
# categorize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("merchant, kind, expected", [
    ("스타벅스", "card", SpendingCategory.CAFE),
    ("이디야커피", "card", SpendingCategory.CAFE),
    ("GS25", "card", SpendingCategory.FOOD),
    ("이마트24", "card", SpendingCategory.FOOD),
    ("배달의민족", "card", SpendingCategory.FOOD),
    ("김밥천국", "card", SpendingCategory.FOOD),
    ("카카오T", "card", SpendingCategory.TRANSPORT),
    ("티머니 교통카드", "bank", SpendingCategory.TRANSPORT),
    ("SKT", "bank", SpendingCategory.TELECOM),
    ("KT", "bank", SpendingCategory.TELECOM),
    ("무슨무슨텔레콤", "bank", SpendingCategory.TELECOM),
    ("넷플릭스", "card", SpendingCategory.SUBSCRIPTION),
    ("유튜브프리미엄", "card", SpendingCategory.SUBSCRIPTION),
    ("온누리약국", "card", SpendingCategory.MEDICAL),
    ("정형외과", "card", SpendingCategory.MEDICAL),
    ("아파트관리비", "bank", SpendingCategory.HOUSING),
    ("월세", "bank", SpendingCategory.HOUSING),
    ("도시가스요금", "bank", SpendingCategory.HOUSING),
    ("삼성생명보험", "bank", SpendingCategory.INSURANCE),
    ("영어학원", "card", SpendingCategory.EDUCATION),
    ("가족송금", "bank", SpendingCategory.TRANSFER),
    ("토스이체", "bank", SpendingCategory.TRANSFER),
    ("경조사비", "bank", SpendingCategory.TRANSFER),
    ("신용대출", "bank", SpendingCategory.DEBT_REPAYMENT),
    ("카드대금(연체)", "bank", SpendingCategory.DEBT_REPAYMENT),
    ("마이너스통장", "bank", SpendingCategory.DEBT_REPAYMENT),
    ("카드론", "bank", SpendingCategory.DEBT_REPAYMENT),
    ("현금서비스", "card", SpendingCategory.CASH_ADVANCE),
    ("급여", "bank", SpendingCategory.SALARY),
    ("이번달월급", "bank", SpendingCategory.SALARY),
    ("전혀모르는가맹점", "card", SpendingCategory.OTHER),
])
def test_categorize_examples(merchant: str, kind: str, expected: SpendingCategory) -> None:
    assert spending_core.categorize(merchant, 10_000, kind) == expected


def test_categorize_never_crashes_on_empty_or_none() -> None:
    assert spending_core.categorize("", 0, "card") == SpendingCategory.OTHER
    assert spending_core.categorize(None, 0, "bank") == SpendingCategory.OTHER  # type: ignore[arg-type]
    assert spending_core.categorize("   ", 100, "card") == SpendingCategory.OTHER


# ---------------------------------------------------------------------------
# aggregate: 합성 P1 데이터로 총계 일관성 확인
# ---------------------------------------------------------------------------


def test_aggregate_synthetic_p1_consistent_totals() -> None:
    txs = synthetic.generate_transactions("P1", months=3, seed=42, end=END)
    summary = spending_core.aggregate(txs, end=END, months=3)

    assert summary.months == 3
    assert summary.period_end == END
    assert summary.total_spend > 0
    assert sum(c.amount for c in summary.categories) == summary.total_spend
    assert sum(c.count for c in summary.categories) == len([
        t for t in txs
        if summary.period_start <= t.date <= END
        and spending_core.categorize(t.merchant, t.amount, t.kind) not in (SpendingCategory.SALARY, SpendingCategory.TRANSFER)
    ])
    assert summary.fixed_spend + summary.variable_spend + summary.discretionary_spend == summary.total_spend
    assert summary.avg_monthly_spend == round(summary.total_spend / 3)
    assert all(c.category != SpendingCategory.TRANSFER for c in summary.categories)
    assert all(c.category != SpendingCategory.SALARY for c in summary.categories)
    # share는 0~1 사이이고 합은 1에 근접해야 한다
    assert abs(sum(c.share for c in summary.categories) - 1.0) < 0.01


def test_aggregate_is_deterministic() -> None:
    txs = synthetic.generate_transactions("P2", months=3, seed=42, end=END)
    s1 = spending_core.aggregate(txs, end=END, months=3)
    s2 = spending_core.aggregate(txs, end=END, months=3)
    assert s1.model_dump_json() == s2.model_dump_json()


def test_aggregate_all_personas_totals_consistent() -> None:
    for persona in synthetic.PERSONAS:
        txs = synthetic.generate_transactions(persona.id, months=3, seed=42, end=END)
        summary = spending_core.aggregate(txs, end=END, months=3)
        assert sum(c.amount for c in summary.categories) == summary.total_spend, persona.id
        assert (summary.fixed_spend + summary.variable_spend + summary.discretionary_spend
                == summary.total_spend), persona.id


# ---------------------------------------------------------------------------
# 구독 탐지
# ---------------------------------------------------------------------------


def test_subscription_detection_same_merchant_near_equal_amount() -> None:
    txs = [
        make_tx("t1", date(2026, 7, 5), 13_500, "넷플릭스"),
        make_tx("t2", date(2026, 8, 5), 13_900, "넷플릭스"),
        make_tx("t3", date(2026, 9, 5), 13_500, "넷플릭스"),
        make_tx("t4", date(2026, 8, 10), 50_000, "다이소"),  # 1회성, 구독 아님
    ]
    summary = spending_core.aggregate(txs, end=date(2026, 9, 6), months=3)
    assert len(summary.subscriptions) == 1
    sub = summary.subscriptions[0]
    assert sub.merchant == "넷플**"
    assert sub.months_seen == 3
    assert sub.category == SpendingCategory.SUBSCRIPTION
    assert 13_000 <= sub.amount <= 14_000


def test_subscription_requires_consecutive_months() -> None:
    """7월, 9월에만 나타나고 8월은 비면(연속 아님) 구독으로 잡지 않는다."""
    txs = [
        make_tx("g1", date(2026, 7, 5), 20_000, "왓챠"),
        make_tx("g2", date(2026, 9, 5), 20_000, "왓챠"),
    ]
    summary = spending_core.aggregate(txs, end=date(2026, 9, 6), months=3)
    assert summary.subscriptions == []


def test_subscription_not_detected_for_single_month() -> None:
    txs = [make_tx("x1", date(2026, 9, 1), 9_900, "멜론")]
    summary = spending_core.aggregate(txs, end=date(2026, 9, 6), months=1)
    assert summary.subscriptions == []


# ---------------------------------------------------------------------------
# 이상치 탐지
# ---------------------------------------------------------------------------


def test_anomaly_detection_plus_40_pct_month() -> None:
    txs = [
        make_tx("a1", date(2026, 7, 10), 150_000, "무신사"),
        make_tx("a2", date(2026, 8, 10), 210_000, "무신사"),  # +40%, +60,000원
    ]
    summary = spending_core.aggregate(txs, end=date(2026, 8, 31), months=2)
    assert len(summary.anomalies) == 1
    anomaly = summary.anomalies[0]
    assert anomaly.category == SpendingCategory.SHOPPING
    assert anomaly.month == "2026-08"
    assert anomaly.amount == 210_000
    assert anomaly.prev_amount == 150_000
    assert anomaly.change_pct == pytest.approx(40.0)
    assert "—" not in anomaly.note


def test_anomaly_not_triggered_when_absolute_diff_too_small() -> None:
    """비율은 30% 이상이지만 절대 증가액이 5만원 미만이면 이상치가 아니다."""
    txs = [
        make_tx("b1", date(2026, 7, 10), 100_000, "무신사"),
        make_tx("b2", date(2026, 8, 10), 135_000, "무신사"),  # +35%, +35,000원
    ]
    summary = spending_core.aggregate(txs, end=date(2026, 8, 31), months=2)
    assert summary.anomalies == []


def test_anomaly_not_triggered_below_pct_threshold() -> None:
    txs = [
        make_tx("c1", date(2026, 7, 10), 200_000, "무신사"),
        make_tx("c2", date(2026, 8, 10), 240_000, "무신사"),  # +20%
    ]
    summary = spending_core.aggregate(txs, end=date(2026, 8, 31), months=2)
    assert summary.anomalies == []


# ---------------------------------------------------------------------------
# 생애 이벤트 감지
# ---------------------------------------------------------------------------


def test_life_event_wedding_detected_without_leaking_merchant_name() -> None:
    txs = [
        make_tx("w1", date(2026, 7, 15), 500_000, "행복웨딩홀"),
        make_tx("w2", date(2026, 8, 1), 300_000, "OO스튜디오"),
    ]
    profile = make_profile()
    events = spending_core.detect_life_events(txs, profile)
    wedding = next((e for e in events if e.kind == "wedding"), None)
    assert wedding is not None
    assert 0 < wedding.confidence <= 1
    assert wedding.evidence
    for ev in wedding.evidence:
        assert "웨딩홀" not in ev
        assert "스튜디오" not in ev


def test_life_event_income_drop_detected() -> None:
    txs = [
        make_tx("s1", date(2026, 7, 25), 3_000_000, "급여"),
        make_tx("s2", date(2026, 8, 25), 1_800_000, "급여"),  # -40%
    ]
    events = spending_core.detect_life_events(txs, make_profile())
    assert any(e.kind == "income_drop" for e in events)


def test_life_event_job_change_when_salary_stops() -> None:
    txs = [
        make_tx("s1", date(2026, 7, 25), 3_000_000, "급여"),
        make_tx("s2", date(2026, 8, 1), 10_000, "GS25"),  # 8월에는 급여 거래 없음
    ]
    events = spending_core.detect_life_events(txs, make_profile())
    assert any(e.kind == "job_change" for e in events)


def test_life_event_none_for_flat_transactions() -> None:
    txs = [make_tx("n1", date(2026, 8, 10), 10_000, "GS25")]
    events = spending_core.detect_life_events(txs, make_profile())
    assert events == []


def test_life_event_refinance_window_for_near_maturity_loan() -> None:
    loan = Loan(
        id="L1", name="신용대출", loan_type=LoanType.CREDIT, principal=5_000_000, balance=5_000_000,
        annual_rate=8.0, rate_type=RateType.FIXED, repay_method=RepayMethod.BULLET, remaining_months=3,
    )
    profile = make_profile(loans=[loan])
    events = spending_core.detect_life_events([], profile)
    assert any(e.kind == "refinance_window" for e in events)


# ---------------------------------------------------------------------------
# compute_features: 결정론
# ---------------------------------------------------------------------------


def test_features_determinism() -> None:
    txs = synthetic.generate_transactions("P3", months=3, seed=42, end=END)
    profile = synthetic.get_persona("P3")
    summary = spending_core.aggregate(txs, end=END, months=3)
    events = spending_core.detect_life_events(txs, profile)
    summary = summary.model_copy(update={"profile_id": profile.id, "life_events": events})

    f1 = spending_core.compute_features(summary, profile)
    f2 = spending_core.compute_features(summary, profile)
    assert f1.model_dump_json() == f2.model_dump_json()
    assert f1.spending_consent_at is None  # core는 시각을 채우지 않는다(서비스 레이어 책임)
    assert f1.profile_id == "P3"
    assert 0.0 <= f1.fixed_ratio <= 1.0
    assert 0.0 <= f1.discretionary_ratio <= 1.0


def test_compute_features_handles_zero_spend_without_crash() -> None:
    summary = spending_core.aggregate([], end=END, months=3)
    features = spending_core.compute_features(summary, make_profile())
    assert features.avg_monthly_spend == 0
    assert features.fixed_ratio == 0.0
    assert features.subscription_count == 0


# ---------------------------------------------------------------------------
# build_spending_cards: 금칙어/em dash 없음, 카드가 최소 1장 이상
# ---------------------------------------------------------------------------


def test_build_spending_cards_has_no_banned_terms() -> None:
    txs = synthetic.generate_transactions("P1", months=3, seed=42, end=END)
    profile = synthetic.get_persona("P1")
    summary = spending_core.aggregate(txs, end=END, months=3)
    events = spending_core.detect_life_events(txs, profile)
    summary = summary.model_copy(update={"profile_id": profile.id, "life_events": events})
    features = spending_core.compute_features(summary, profile)

    cards = spending_core.build_spending_cards(features, summary, profile)
    assert len(cards) >= 1
    for card in cards:
        assert "—" not in card.title and "—" not in card.body
        assert "추천" not in card.title and "추천" not in card.body
        assert "위험" not in card.title and "위험" not in card.body
        assert "과소비" not in card.body
        assert card.kind == "spending"
        assert card.explain
        assert card.chip is not None
        assert card.chip.intent in ("spending", "scenario", "compare")


# ---------------------------------------------------------------------------
# API 라운드트립 (tests/test_api.py와 같은 패턴: 임시 DB + TestClient)
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402

from app.data import db as db_module  # noqa: E402
from app.main import app  # noqa: E402

db_module.init_db(db_module.get_conn())
client = TestClient(app)


def test_spending_taxonomy_endpoint() -> None:
    r = client.get("/api/spending/taxonomy")
    assert r.status_code == 200
    body = r.json()
    assert "식비" in body["categories"]
    assert "카페간식" in body["categories"]
    assert "대출상환" in body["categories"]
    assert isinstance(body["rules"], dict) and body["rules"]


def test_spending_analyze_synthetic_unknown_persona_404() -> None:
    r = client.post("/api/spending/analyze-synthetic", json={"persona_id": "P999"})
    assert r.status_code == 404


def test_spending_analyze_raw_transactions() -> None:
    """/api/spending/analyze는 서버의 오늘 날짜를 end로 쓰므로(스키마에 end가 없음),
    윈도우 경계 문제를 피하려고 거래 날짜를 실제 오늘 날짜에 맞춰 만든다."""
    client.delete("/api/session")
    client.delete("/api/spending")
    today = date.today().isoformat()
    payload = {
        "transactions": [
            {"id": "r1", "date": today, "amount": 13_500, "merchant": "넷플릭스", "kind": "card"},
            {"id": "r2", "date": today, "amount": 10_000, "merchant": "GS25", "kind": "card"},
        ],
        "months": 1,
    }
    r = client.post("/api/spending/analyze", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["summary"]["total_spend"] == 23_500
    assert body["summary"]["profile_id"] == "guest"
    client.delete("/api/spending")


def test_spending_api_roundtrip_persona_p1_then_home_then_delete() -> None:
    r0 = client.post("/api/session/persona/P1")
    assert r0.status_code == 200

    r1 = client.post("/api/spending/analyze-synthetic", json={"persona_id": "P1", "months": 3, "seed": 42})
    assert r1.status_code == 200
    analyzed = r1.json()
    assert analyzed["summary"]["profile_id"] == "P1"
    assert analyzed["features"]["profile_id"] == "P1"
    assert analyzed["summary"]["total_spend"] > 0
    assert isinstance(analyzed["cards"], list) and len(analyzed["cards"]) >= 1
    for card in analyzed["cards"]:
        assert "—" not in card["title"] and "—" not in card["body"]
        assert "추천" not in card["title"] and "추천" not in card["body"]

    r2 = client.get("/api/spending")
    assert r2.status_code == 200
    fetched = r2.json()
    assert fetched["summary"]["total_spend"] == analyzed["summary"]["total_spend"]
    assert fetched["features"] == analyzed["features"]

    r3 = client.get("/api/home")
    assert r3.status_code == 200
    home = r3.json()
    assert home["llm_calls"] == 0
    assert len(home["cards"]) <= 3
    assert any(c["kind"] == "spending" for c in home["cards"])
    assert any(chip["intent"] == "spending" for chip in home["chips"])

    r4 = client.delete("/api/spending")
    assert r4.status_code == 200
    assert r4.json()["ok"] is True

    r5 = client.get("/api/spending")
    assert r5.status_code == 404

    # 홈 화면에서도 소비 카드가 사라진다
    r6 = client.get("/api/home")
    assert r6.status_code == 200
    home2 = r6.json()
    assert not any(c["kind"] == "spending" for c in home2["cards"])

    client.delete("/api/session")
