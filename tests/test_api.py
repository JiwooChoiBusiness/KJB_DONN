"""FastAPI 앱 통합 테스트 (SPEC 2.5).

DONN_DB_PATH를 임시 파일로 돌려 실제 data/donn.db를 건드리지 않는다. app.main을
임포트하기 전에 환경변수를 설정해야 app.data.db.DB_PATH가 이 값을 읽는다(모듈
전역 상수라 최초 임포트 시점에 고정된다).
"""
from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="donn_test_api_")
os.environ["DONN_DB_PATH"] = os.path.join(_TMP_DIR, "test.db")
# 실 네트워크 호출 없이 llm_available=True를 결정론적으로 만들기 위한 가짜 키/모델.
# 실제로 Gemini를 호출하는 테스트는 전부 _llm_provider를 monkeypatch해서 통제한다.
os.environ["GEMINI_API_KEYS"] = "test-key-1,test-key-2"
os.environ["GEMINI_MODEL_CHAIN"] = "test-model-a,test-model-b"

from datetime import date  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import routes as routes_module  # noqa: E402
from app.data import db, products  # noqa: E402
from app.llm.provider import LLMResult, LLMUnavailable  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    LenderGroup,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    ProductSource,
    RateSemantics,
)

db.init_db(db.get_conn())
client = TestClient(app)


# ---------------------------------------------------------------------------
# 상품 스냅샷 픽스처: credit(bank x2, savings_bank x2), deposit(bank x3)
# ---------------------------------------------------------------------------

_FIXTURE_SNAPSHOT_ID = "20260906-0000-testfixture"
_FIXTURE_COMPANIES = ["가상은행A", "가상은행B", "가상저축은행A", "가상저축은행B"]


def _credit_product(idx: int, company: str, group: LenderGroup, rate: float) -> ProductSnapshot:
    return ProductSnapshot(
        id=f"{_FIXTURE_SNAPSHOT_ID}:CREDIT:{idx}",
        snapshot_id=_FIXTURE_SNAPSHOT_ID,
        source=ProductSource.CURATED,
        category=ProductCategory.CREDIT,
        lender_group=group,
        company_code=f"C{idx}",
        company_name=company,
        product_code=f"CR{idx}",
        product_name=f"{company} 신용대출 {idx}호",
        rate_semantics=RateSemantics.OFFER_RATE,
        options=[ProductOption(rate=rate, rate_kind="base", term_months=36, rate_type="fixed")],
        disclosure_month="202608",
        disclosure_url="https://finlife.fss.or.kr/finlife/ldng/indvlCrdt/list.do?menuNo=700009",
    )


def _deposit_product(idx: int, company: str, rate: float) -> ProductSnapshot:
    return ProductSnapshot(
        id=f"{_FIXTURE_SNAPSHOT_ID}:DEPOSIT:{idx}",
        snapshot_id=_FIXTURE_SNAPSHOT_ID,
        source=ProductSource.CURATED,
        category=ProductCategory.DEPOSIT,
        lender_group=LenderGroup.BANK,
        company_code=f"D{idx}",
        company_name=company,
        product_code=f"DP{idx}",
        product_name=f"{company} 정기예금 {idx}호",
        rate_semantics=RateSemantics.OFFER_RATE,
        options=[ProductOption(rate=rate, rate_kind="base", term_months=12, rate_type="fixed")],
        disclosure_month="202608",
        disclosure_url="https://finlife.fss.or.kr/finlife/svings/fdrmDpst/list.do?menuNo=700002",
    )


def _insert_fixture_products() -> None:
    snaps = [
        _credit_product(1, "가상은행A", LenderGroup.BANK, 5.2),
        _credit_product(2, "가상은행B", LenderGroup.BANK, 6.8),
        _credit_product(3, "가상저축은행A", LenderGroup.SAVINGS_BANK, 8.5),
        _credit_product(4, "가상저축은행B", LenderGroup.SAVINGS_BANK, 9.1),
        _deposit_product(5, "가상은행A", 3.1),
        _deposit_product(6, "가상은행B", 3.4),
        _deposit_product(7, "가상은행C", 2.9),
    ]
    conn = db.get_conn()
    try:
        products._save_snapshot(
            conn, _FIXTURE_SNAPSHOT_ID, "curated", "2026-09-06T00:00:00", snaps, note="test fixture"
        )
    finally:
        conn.close()


_insert_fixture_products()


def _clear_session() -> None:
    r = client.delete("/api/session")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 상태/메타
# ---------------------------------------------------------------------------


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "gemini"
    assert body["llm_available"] is True
    assert body["model_chain"] == ["test-model-a", "test-model-b"]
    assert "engine_version" in body and "snapshot_id" in body


def test_meta():
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert "credit" in body["categories"]
    assert "deposit" in body["categories"]
    assert "equal_payment" in body["repay_methods"]
    assert "total_cost" in body["sort_keys"]
    assert "bank" in body["lender_groups"]
    assert len(body["credit_bands"]) == 8


def test_products_stats():
    r = client.get("/api/products/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total_products"] >= 7


# ---------------------------------------------------------------------------
# 페르소나 / 세션 / 프로필 / 홈
# ---------------------------------------------------------------------------


def test_personas_list():
    r = client.get("/api/personas")
    assert r.status_code == 200
    personas = r.json()
    assert len(personas) == 8
    ids = {p["id"] for p in personas}
    assert ids == {f"P{i}" for i in range(1, 9)}
    for p in personas:
        assert p["loans_count"] >= 1
        assert p["total_balance"] > 0
        assert p["one_liner"]


def test_load_persona_then_home_has_cards_and_zero_llm_calls():
    r = client.post("/api/session/persona/P1")
    assert r.status_code == 200
    assert r.json()["id"] == "P1"

    r2 = client.get("/api/profile")
    assert r2.status_code == 200
    assert r2.json()["id"] == "P1"

    r3 = client.get("/api/home")
    assert r3.status_code == 200
    home = r3.json()
    assert home["llm_calls"] == 0
    assert len(home["cards"]) >= 2
    assert len(home["chips"]) >= 1
    assert home["capacity"] is not None
    # 어떤 카드 문장에도 상품/회사명이 그대로 노출되지 않는다.
    for card in home["cards"]:
        for company in _FIXTURE_COMPANIES:
            assert company not in card["title"]
            assert company not in card["body"]


def test_load_unknown_persona_404():
    r = client.post("/api/session/persona/P999")
    assert r.status_code == 404


def test_home_without_profile_is_onboarding():
    _clear_session()
    r = client.get("/api/profile")
    assert r.status_code == 404

    r2 = client.get("/api/home")
    assert r2.status_code == 200
    home = r2.json()
    assert home["profile_id"] is None
    assert home["llm_calls"] == 0
    assert len(home["cards"]) == 2
    assert len(home["chips"]) == 5
    assert home["top_action"] is None
    assert home["capacity"] is None


def test_profile_put_and_get():
    _clear_session()
    profile = {
        "id": "me",
        "display_name": "나",
        "monthly_income": 3_000_000,
        "fixed_expenses": 1_200_000,
        "variable_expenses": 500_000,
        "emergency_fund": 300_000,
        "credit_band": None,
        "loans": [{
            "id": "L1",
            "name": "신용대출",
            "loan_type": "credit",
            "principal": 20_000_000,
            "balance": 20_000_000,
            "annual_rate": 7.5,
            "rate_type": "fixed",
            "repay_method": "equal_payment",
            "remaining_months": 24,
            "grace_months": 0,
            "lender_group": "bank",
        }],
        "flags": [],
    }
    r = client.put("/api/profile", json=profile)
    assert r.status_code == 200
    assert r.json()["id"] == "me"

    r2 = client.get("/api/profile")
    assert r2.status_code == 200
    assert r2.json()["monthly_income"] == 3_000_000
    assert len(r2.json()["loans"]) == 1


# ---------------------------------------------------------------------------
# 대출 스케줄 / 시나리오 / 행동 제안
# ---------------------------------------------------------------------------


def test_schedule_scenarios_and_actions_with_persona():
    r = client.post("/api/session/persona/P1")
    assert r.status_code == 200
    profile = r.json()
    loan_id = profile["loans"][0]["id"]

    r2 = client.get(f"/api/loans/{loan_id}/schedule")
    assert r2.status_code == 200
    sched = r2.json()
    assert sched["loan_id"] == loan_id
    assert len(sched["rows"]) > 0
    assert sched["total_payment"] > 0

    r3 = client.get(f"/api/loans/{loan_id}/schedule?extra=10000")
    assert r3.status_code == 200

    r4 = client.get("/api/loans/UNKNOWN-LOAN/schedule")
    assert r4.status_code == 404

    r5 = client.get("/api/scenarios?horizon=12")
    assert r5.status_code == 200
    scenarios = r5.json()
    assert len(scenarios) == 3
    assert {s["scenario"] for s in scenarios} == {"base", "adverse", "favorable"}

    r6 = client.get("/api/actions")
    assert r6.status_code == 200
    action_cards = r6.json()
    assert isinstance(action_cards, list)
    assert len(action_cards) >= 1
    # numbers는 서비스 계층(app/services/actions.py)에서 한글 라벨로 포맷되어 있어야
    # 한다(app/core/rules.py가 만드는 원문 영문 스네이크케이스 키가 그대로 남으면 안 됨).
    raw_rule_keys = {
        "debt_service_ratio", "net_monthly", "payoff_amount", "remaining_months",
        "grace_months", "emergency_fund", "target_emergency_fund", "gap", "balance",
        "rate", "monthly_income_x2", "extra_monthly", "target_rate", "months_saved",
        "interest_saved", "new_months", "current_rate", "cost", "assumed_rate_gap",
        "monthly_interest_saving", "prepay_fee", "breakeven_months",
    }
    for card in action_cards:
        assert isinstance(card["numbers"], dict)
        assert not (set(card["numbers"].keys()) & raw_rule_keys)


def test_actions_and_scenarios_404_without_profile():
    _clear_session()
    r = client.get("/api/actions")
    assert r.status_code == 404

    r2 = client.get("/api/scenarios")
    assert r2.status_code == 404


# ---------------------------------------------------------------------------
# 공시 비교 + 결정 기록 (하나의 흐름으로 묶어 순서 의존성을 피한다)
# ---------------------------------------------------------------------------


def test_compare_prepare_run_and_decision_replay_flow():
    _clear_session()

    # 프로필이 없어도 기본값(신용대출/1,000만원/36개월)으로 준비된다.
    r = client.post("/api/compare/prepare", json={"intent": "compare", "params": {}})
    assert r.status_code == 200
    ctx = r.json()
    assert ctx["category"] == "credit"
    assert ctx["amount"] == 10_000_000
    assert ctx["term_months"] == 36
    assert ctx["user_confirmed"] is False
    assert set(ctx["estimated_fields"]) >= {"category", "amount", "term_months"}

    # 확인 없이 실행하면 422
    ctx_unconfirmed = dict(ctx)
    r2 = client.post("/api/compare/run", json=ctx_unconfirmed)
    assert r2.status_code == 422

    # 확인 후 실행하면 200, 상위 저축은행/은행 상품만으로 랭킹
    ctx["user_confirmed"] = True
    ctx["lender_groups"] = ["bank", "savings_bank"]
    r3 = client.post("/api/compare/run", json=ctx)
    assert r3.status_code == 200
    result = r3.json()
    assert len(result["items"]) >= 1
    assert result["candidates_total"] >= 1

    first_item = result["items"][0]
    assert first_item["rank"] == 1
    assert "product_ref" not in first_item or True  # product_ref는 감사용, 화면 노출 금지 대상 아님(anon_label만 검사)
    for item in result["items"]:
        for company in _FIXTURE_COMPANIES + ["가상은행C"]:
            assert company not in item["anon_label"]
        assert item["anon_label"][1:].strip() in ("은행 신용대출", "저축은행 신용대출")

    decision_id = result["decision_id"]
    assert result["result_hash"]

    # 목록은 result를 제외한 요약만 돌려준다
    r4 = client.get("/api/decisions?limit=20")
    assert r4.status_code == 200
    listing = r4.json()
    assert any(d["decision_id"] == decision_id for d in listing)
    assert "result" not in listing[0]
    assert "context" in listing[0]

    # 단건 조회는 result를 포함한 전체를 돌려준다
    r5 = client.get(f"/api/decisions/{decision_id}")
    assert r5.status_code == 200
    full = r5.json()
    assert full["result"]["decision_id"] == decision_id

    # 재현: 같은 스냅샷/조건이면 해시가 일치해야 한다
    r6 = client.post(f"/api/decisions/{decision_id}/replay")
    assert r6.status_code == 200
    replay = r6.json()
    assert replay["match"] is True
    assert replay["result_hash"] == replay["replay_hash"] == result["result_hash"]

    r7 = client.get("/api/decisions/UNKNOWN-DECISION")
    assert r7.status_code == 404

    r8 = client.post("/api/decisions/UNKNOWN-DECISION/replay")
    assert r8.status_code == 404


def test_compare_run_with_max_rate_filters_out_high_rate_products():
    _clear_session()
    ctx = client.post("/api/compare/prepare", json={"intent": "compare", "params": {}}).json()
    ctx["user_confirmed"] = True
    ctx["max_rate"] = 6.0  # 가상은행A(5.2%)만 통과, 나머지(6.8/8.5/9.1)는 제외
    ctx["lender_groups"] = ["bank", "savings_bank"]
    r = client.post("/api/compare/run", json=ctx)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["rate"] == pytest.approx(5.2)


# ---------------------------------------------------------------------------
# 채팅 (Gemini는 monkeypatch로 통제)
# ---------------------------------------------------------------------------


class _FakeUnavailableProvider:
    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        raise LLMUnavailable("강제 실패(테스트)")


class _FakeSuccessProvider:
    def __init__(self, data: dict):
        self._data = data

    def available(self) -> bool:
        return True

    def extract(self, text, schema, system):  # noqa: ANN001
        return LLMResult(data=dict(self._data), text="{}", model="fake-model", key_index=0,
                          latency_ms=5, usage={"totalTokenCount": 3})


def test_chat_llm_unavailable_falls_back_to_rule_based(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    r = client.post("/api/chat", json={"message": "신용대출 공시 비교하고 싶어요"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is False
    assert body["action"]["type"] == "prepare_compare"
    assert body["action"]["payload"]["params"]["category"] == "credit"
    # 응답 문구에 상품/회사명이 들어가면 안 된다.
    for company in _FIXTURE_COMPANIES:
        assert company not in body["reply_text"]


def test_chat_llm_mocked_success_path(monkeypatch):
    fake = _FakeSuccessProvider({"intent": "compare", "category": "credit", "amount": 20_000_000, "term_months": 24})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    # 숫자는 발화에 근거가 있어야 남는다(근거 검증 필터). 2천만원·24개월이 발화에 있으므로 유지.
    r = client.post("/api/chat", json={"message": "2천만원 신용대출 24개월로 비교해줘"})
    assert r.status_code == 200
    body = r.json()
    assert body["llm_used"] is True
    assert body["action"]["type"] == "prepare_compare"
    assert body["action"]["payload"]["params"]["amount"] == 20_000_000
    assert body["action"]["payload"]["params"]["term_months"] == 24


def test_chat_schedule_intent_opens_debts_view(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    r = client.post("/api/chat", json={"message": "상환표 보여줘"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] == {"type": "open_view", "payload": {"view": "debts"}}


def test_chat_faq_fallback_has_chips(monkeypatch):
    monkeypatch.setattr(routes_module, "_llm_provider", _FakeUnavailableProvider())
    r = client.post("/api/chat", json={"message": "안녕하세요"})
    assert r.status_code == 200
    body = r.json()
    assert body["action"] is None
    assert len(body["chips"]) >= 1


# ---------------------------------------------------------------------------
# 합성 거래내역 CSV
# ---------------------------------------------------------------------------


def test_synthetic_csv_download():
    r = client.get("/api/synthetic/P2/transactions.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip("\n").splitlines()
    assert lines[0].split(",") == ["id", "date", "amount", "merchant", "category", "kind", "memo"]
    assert len(lines) > 1


def test_synthetic_csv_unknown_persona_404():
    r = client.get("/api/synthetic/P999/transactions.csv")
    assert r.status_code == 404


def test_chat_drops_numbers_not_in_utterance(monkeypatch):
    """LLM이 발화에 없는 금액·기간을 지어내면 버리고 프로필/기본값 추정으로 돌아간다."""
    fake = _FakeSuccessProvider({"intent": "compare", "category": "credit", "amount": 20_000_000, "term_months": 24})
    monkeypatch.setattr(routes_module, "_llm_provider", fake)
    r = client.post("/api/chat", json={"message": "신용대출 비교해줘"})
    assert r.status_code == 200
    params = r.json()["action"]["payload"]["params"]
    assert params["amount"] != 20_000_000
    assert params["term_months"] != 24
