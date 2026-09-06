"""이전 결과 대비 변화(CompareDelta) 테스트 (SPEC 2.14).

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처(신용대출 4건:
가상은행A 5.2%, 가상은행B 6.8%, 가상저축은행A 8.5%, 가상저축은행B 9.1%, 기본
lender_groups=[bank, savings_bank]라 전부 후보에 든다)를 재사용한다.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_api import _clear_session, client

from app.main import app


def _prepared_credit_ctx(amount: int = 20_000_000, term_months: int = 36) -> dict:
    r = client.post(
        "/api/compare/prepare",
        json={"intent": "compare", "params": {"category": "credit", "amount": amount, "term_months": term_months}},
    )
    assert r.status_code == 200
    ctx = r.json()
    ctx["user_confirmed"] = True
    return ctx


def test_same_conditions_twice_have_no_changed_fields_and_top_unchanged():
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r1 = client.post("/api/compare/run", json=ctx)
        assert r1.status_code == 200
        decision_id_1 = r1.json()["decision_id"]

        r2 = client.post(f"/api/compare/run?previous_decision_id={decision_id_1}", json=ctx)
        assert r2.status_code == 200
        result2 = r2.json()

        delta = result2["delta"]
        assert delta is not None
        assert delta["previous_decision_id"] == decision_id_1
        assert delta["changed_fields"] == []
        assert delta["top_changed"] is False
        assert delta["candidates_before"] == delta["candidates_after"]
        assert delta["top_before_label"] == delta["top_after_label"]
        assert delta["top_total_interest_before"] == delta["top_total_interest_after"]
        assert delta["top_monthly_before"] == delta["top_monthly_after"]
    finally:
        _clear_session()


def test_adding_max_rate_shows_changed_field_and_fewer_candidates():
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r1 = client.post("/api/compare/run", json=ctx)
        assert r1.status_code == 200
        result1 = r1.json()
        decision_id_1 = result1["decision_id"]
        candidates_before = result1["candidates_total"]
        assert candidates_before == 4  # 픽스처 신용대출 4건 전부(기본 lender_groups)

        ctx2 = dict(ctx)
        ctx2["max_rate"] = 6.0  # 5.2%만 남기고 6.8/8.5/9.1%는 제외된다
        r2 = client.post(f"/api/compare/run?previous_decision_id={decision_id_1}", json=ctx2)
        assert r2.status_code == 200
        result2 = r2.json()

        delta = result2["delta"]
        assert delta is not None
        assert "금리 상한" in delta["changed_fields"]
        assert result2["candidates_total"] < candidates_before
        assert delta["candidates_before"] == candidates_before
        assert delta["candidates_after"] == result2["candidates_total"]
    finally:
        _clear_session()


def test_previous_decision_from_other_session_yields_no_delta():
    """`decisions.get`은 세션(sid) 소유 검사를 하므로 다른 브라우저 세션(별도 TestClient)의
    decision_id를 넘기면 delta가 만들어지지 않는다(SPEC 2.14)."""
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r1 = client.post("/api/compare/run", json=ctx)
        assert r1.status_code == 200
        decision_id_1 = r1.json()["decision_id"]

        other_client = TestClient(app)
        ctx_prep = other_client.post(
            "/api/compare/prepare", json={"intent": "compare", "params": {"category": "credit"}},
        ).json()
        ctx_prep["user_confirmed"] = True
        r2 = other_client.post(f"/api/compare/run?previous_decision_id={decision_id_1}", json=ctx_prep)
        assert r2.status_code == 200
        assert r2.json()["delta"] is None
    finally:
        _clear_session()


def test_different_category_previous_decision_yields_no_delta():
    """카테고리가 다르면(예: 신용대출 -> 주담대) 이전 결정이 있어도 delta는 None이다."""
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r1 = client.post("/api/compare/run", json=ctx)
        assert r1.status_code == 200
        decision_id_1 = r1.json()["decision_id"]

        mortgage_ctx = client.post(
            "/api/compare/prepare", json={"intent": "compare", "params": {"category": "mortgage"}},
        ).json()
        mortgage_ctx["user_confirmed"] = True
        r2 = client.post(f"/api/compare/run?previous_decision_id={decision_id_1}", json=mortgage_ctx)
        assert r2.status_code == 200
        assert r2.json()["delta"] is None
    finally:
        _clear_session()


def test_replay_still_matches_with_delta_present():
    """result_hash/fingerprint는 delta 유무와 무관하게 그대로 재현되어야 한다(재현성 불변)."""
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r1 = client.post("/api/compare/run", json=ctx)
        assert r1.status_code == 200
        decision_id_1 = r1.json()["decision_id"]

        r2 = client.post(f"/api/compare/run?previous_decision_id={decision_id_1}", json=ctx)
        assert r2.status_code == 200
        result2 = r2.json()
        assert result2["delta"] is not None

        decision_id_2 = result2["decision_id"]
        r3 = client.post(f"/api/decisions/{decision_id_2}/replay")
        assert r3.status_code == 200
        replay = r3.json()
        assert replay["match"] is True
        assert replay["result_hash"] == replay["replay_hash"] == result2["result_hash"]

        # 저장된 결정 기록에도 delta가 그대로 남아 있어야 한다(SPEC 2.14: result JSON에 저장).
        stored = client.get(f"/api/decisions/{decision_id_2}").json()
        assert stored["result"]["delta"] is not None
    finally:
        _clear_session()


def test_no_previous_decision_id_query_leaves_delta_none():
    _clear_session()
    try:
        ctx = _prepared_credit_ctx()
        r = client.post("/api/compare/run", json=ctx)
        assert r.status_code == 200
        assert r.json()["delta"] is None
    finally:
        _clear_session()
