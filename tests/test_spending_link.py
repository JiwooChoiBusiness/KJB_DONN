"""지출 절감을 상환 효과로 연결하기 (SPEC 2.15) 테스트.

`tests.test_api`가 모듈 임포트 시 만들어 둔 공유 client/DB/픽스처를 재사용한다(패턴은
tests/test_chat_attach.py와 같다).
"""
from __future__ import annotations

import math
from datetime import date

from tests.test_api import _clear_session, client

from app.core import loan as loan_core
from app.core import spending as spending_core
from app.data import synthetic
from app.models import SavingOpportunity, UserProfile
from app.services import spending as spending_service


def _p3_summary_features():
    profile = synthetic.get_persona("P3")
    transactions = synthetic.generate_transactions("P3", months=3, seed=42, end=date.today())
    summary, features = spending_service.analyze(transactions, profile, end=date.today(), months=3)
    return profile, transactions, summary, features


# ---------------------------------------------------------------------------
# (a) 합성 거래내역(P3)으로 opportunities 1개 이상 · 가맹점명 없음 · 금액 내림차순
# ---------------------------------------------------------------------------


def test_savings_opportunities_p3_are_nonempty_merchant_free_and_descending():
    profile, transactions, summary, features = _p3_summary_features()
    merchants = {t.merchant for t in transactions}

    opportunities = spending_core.savings_opportunities(summary, features)
    assert 1 <= len(opportunities) <= 3

    amounts = [o.monthly_saving for o in opportunities]
    assert amounts == sorted(amounts, reverse=True)
    assert all(a > 0 for a in amounts)

    for opp in opportunities:
        for merchant in merchants:
            assert merchant not in opp.label
            assert merchant not in opp.basis


def test_savings_opportunities_kinds_are_distinct_and_known():
    _, _, summary, features = _p3_summary_features()
    opportunities = spending_core.savings_opportunities(summary, features)
    kinds = [o.kind for o in opportunities]
    assert len(kinds) == len(set(kinds))  # 종류(a/b/c)당 최대 1개
    assert set(kinds) <= {"subscriptions", "spike", "discretionary"}


# ---------------------------------------------------------------------------
# (b) linked_actions의 수치가 extra_payment_effect와 일치
# ---------------------------------------------------------------------------


def test_link_savings_to_debt_matches_extra_payment_effect():
    profile, _, summary, features = _p3_summary_features()
    opportunities = spending_core.savings_opportunities(summary, features)
    assert opportunities

    linked = spending_service.link_savings_to_debt(profile, opportunities, today=date.today())
    assert len(linked) == len(opportunities)

    target_loan = sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]
    for action, opp in zip(linked, opportunities):
        expected = loan_core.extra_payment_effect(target_loan, opp.monthly_saving)
        assert action.opportunity == opp
        assert action.months_saved == expected["months_saved"]
        assert action.interest_saved == expected["interest_saved"]
        assert action.new_months == expected["new_months"]
        assert action.target_loan_label is not None
        assert f"{opp.monthly_saving:,}원" in action.sentence
        assert f"{expected['months_saved']}개월" in action.sentence
        assert f"{expected['interest_saved']:,}원" in action.sentence


def test_link_savings_to_debt_skips_zero_saving_opportunity():
    profile = synthetic.get_persona("P1")
    zero_opp = SavingOpportunity(kind="spike", label="식비 급증분", monthly_saving=0, basis="0원 테스트")
    assert spending_service.link_savings_to_debt(profile, [zero_opp], today=date.today()) == []


# ---------------------------------------------------------------------------
# (c) 대출 없는 프로필의 비상금 문장
# ---------------------------------------------------------------------------


def test_link_savings_to_debt_no_loans_uses_emergency_fund_sentence():
    profile = UserProfile(
        id="test-no-loan", display_name="테스트", monthly_income=2_000_000,
        fixed_expenses=1_000_000, variable_expenses=300_000, emergency_fund=0, loans=[],
    )
    opp = SavingOpportunity(kind="subscriptions", label="정기 결제 2건", monthly_saving=30_000, basis="테스트 근거")

    linked = spending_service.link_savings_to_debt(profile, [opp], today=date.today())
    assert len(linked) == 1
    action = linked[0]
    assert action.target_loan_label is None
    assert action.months_saved == 0
    assert action.interest_saved == 0
    assert action.new_months is None
    assert "비상금으로 모으면" in action.sentence
    assert "목표 비상금에 닿아요" in action.sentence
    # 목표가 없으므로 고정지출 3개월분(300만원) 기준: ceil(3,000,000 / 30,000) = 100개월
    expected_months = math.ceil((profile.fixed_expenses * 3 - profile.emergency_fund) / opp.monthly_saving)
    assert f"{expected_months}개월" in action.sentence


def test_link_savings_to_debt_no_loans_uses_goal_target_when_present():
    from app.models import Goal
    profile = UserProfile(
        id="test-goal", display_name="테스트", monthly_income=2_000_000,
        fixed_expenses=1_000_000, variable_expenses=300_000, emergency_fund=0, loans=[],
        goals=[Goal(id="g1", kind="emergency", label="비상자금", target_amount=200_000,
                    target_date=date(2027, 1, 1), saved_amount=50_000)],
    )
    opp = SavingOpportunity(kind="subscriptions", label="정기 결제 1건", monthly_saving=30_000, basis="테스트 근거")
    linked = spending_service.link_savings_to_debt(profile, [opp], today=date.today())
    # 목표 잔여분(200,000 - 50,000 = 150,000) 기준: ceil(150,000 / 30,000) = 5개월
    assert "5개월" in linked[0].sentence


# ---------------------------------------------------------------------------
# (d) /api/spending/analyze-synthetic 응답 필드
# ---------------------------------------------------------------------------


def test_spending_analyze_synthetic_response_has_opportunities_and_linked_actions():
    _clear_session()
    try:
        assert client.post("/api/session/persona/P3").status_code == 200
        r = client.post("/api/spending/analyze-synthetic", json={"persona_id": "P3"})
        assert r.status_code == 200
        body = r.json()
        assert "opportunities" in body
        assert "linked_actions" in body
        assert isinstance(body["opportunities"], list)
        assert isinstance(body["linked_actions"], list)
        assert len(body["opportunities"]) >= 1
        assert len(body["linked_actions"]) == len(body["opportunities"])

        r_get = client.get("/api/spending")
        assert r_get.status_code == 200
        get_body = r_get.json()
        assert get_body["opportunities"] == body["opportunities"]
        assert get_body["linked_actions"] == body["linked_actions"]
    finally:
        client.delete("/api/spending")
        client.delete("/api/session")


# ---------------------------------------------------------------------------
# (e) attach 답변에 "이렇게 연결돼요" 포함
# ---------------------------------------------------------------------------


def test_chat_attach_answer_includes_linked_section_when_present():
    assert client.post("/api/session/persona/P3").status_code == 200
    try:
        transactions = synthetic.generate_transactions("P3", months=3, seed=42, end=date.today())
        payload = {
            "filename": "P3_거래내역.csv", "months": 3,
            "transactions": [t.model_dump(mode="json") for t in transactions],
        }
        r = client.post("/api/chat/attach", json=payload)
        assert r.status_code == 200
        body = r.json()
        assert "**이렇게 연결돼요**" in body["reply_text"]
        # "이렇게 연결돼요" 아래 목록은 최대 2개다(SPEC 2.15).
        linked_section = body["reply_text"].split("**이렇게 연결돼요**", 1)[1]
        linked_bullets = [line for line in linked_section.splitlines() if line.startswith("- ")]
        assert 1 <= len(linked_bullets) <= 2
        chip_intents = {c["intent"] for c in body["chips"]}
        assert "scenario" in chip_intents
        client.delete(f"/api/chats/{body['chat_id']}")
    finally:
        client.delete("/api/spending")
        client.delete("/api/session")
