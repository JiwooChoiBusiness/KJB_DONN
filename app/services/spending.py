"""소비 패턴 분석 서비스 (SPEC 2.6). app.core.spending의 순수 함수를 조합하고
db.spending_features 테이블에 프로필별 최신 분석 결과 1건을 저장/조회/삭제한다.

프라이버시(D3/D4): 원본 거래내역(transactions)은 이 모듈이 절대 저장하지 않는다.
저장하는 것은 계산된 SpendingSummary/SpendingFeatures뿐이다(개인신용정보 원장을
서버에 쌓지 않는다는 PoC 결정 - docs/DONN_ADDENDUM_v0.1.md 2.2절 P-A/P-C 절충).
동의(D8): 분석은 사용자가 파일을 업로드하거나 합성 데이터를 명시적으로 선택했을 때만
실행되므로, 이 시점(analyze 호출 시점)을 `spending_consent_at`으로 기록한다.

브라우저별 세션 분리: `save`/`load`/`clear`에 넘기는 `profile_id`는 호출부(routes.py)
그대로이고, 이 모듈이 내부적으로 `f"{sid}:{profile_id}"`(sid는
`app.services.session.current_sid()`)를 실제 저장 키로 써서 다른 브라우저 세션의
분석 결과와 섞이지 않게 한다.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional

from app.core import loan as loan_core
from app.core import spending as spending_core
from app.data import db, synthetic
from app.models import (
    Loan,
    SavingOpportunity,
    SpendingFeatures,
    SpendingLinkedAction,
    SpendingSummary,
    Transaction,
    UserProfile,
)
from app.services import session

# app/services/actions.py·app/services/explain.py의 같은 이름 상수와 동일한 내용을
# 각 모듈이 필요한 만큼만 복제해 쓰는 기존 패턴을 따른다(모듈 간 private 심볼 의존을 피함).
_LOAN_TYPE_LABELS_KR: dict[str, str] = {
    "credit": "신용대출",
    "mortgage": "주택담보대출",
    "jeonse": "전세자금대출",
    "student": "학자금대출",
    "card_loan": "카드론",
    "overdraft": "마이너스통장",
    "policy": "정책상품대출",
    "other": "기타 대출",
}


def analyze(
    transactions: list[Transaction],
    profile: UserProfile,
    *,
    end: date,
    months: int = 3,
    consent_at: Optional[datetime] = None,
) -> tuple[SpendingSummary, SpendingFeatures]:
    """거래내역을 집계하고(app.core.spending.aggregate) 생애 이벤트를 감지해 합친 뒤
    피처를 계산한다. consent_at을 생략하면 호출 시각을 동의 시각으로 기록한다(D8)."""
    summary = spending_core.aggregate(transactions, end=end, months=months)
    life_events = spending_core.detect_life_events(transactions, profile)
    summary = summary.model_copy(update={"profile_id": profile.id, "life_events": life_events})

    features = spending_core.compute_features(summary, profile)
    features = features.model_copy(update={"spending_consent_at": consent_at or datetime.now()})
    return summary, features


def _scoped_profile_id(profile_id: str) -> str:
    return f"{session.current_sid()}:{profile_id}"


def save(profile_id: str, summary: SpendingSummary, features: SpendingFeatures) -> None:
    """프로필당(현재 브라우저 세션 기준) 최신 분석 결과 1건만 유지한다(같은 profile_id면
    덮어쓴다)."""
    now = datetime.now().isoformat(timespec="seconds")
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO spending_features(profile_id, features_json, summary_json, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (_scoped_profile_id(profile_id), features.model_dump_json(), summary.model_dump_json(), now),
        )
        conn.commit()
    finally:
        conn.close()


def load(profile_id: str) -> Optional[tuple[SpendingSummary, SpendingFeatures]]:
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT summary_json, features_json FROM spending_features WHERE profile_id = ?",
            (_scoped_profile_id(profile_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    summary = SpendingSummary.model_validate_json(row["summary_json"])
    features = SpendingFeatures.model_validate_json(row["features_json"])
    return summary, features


def clear(profile_id: str) -> bool:
    conn = db.get_conn()
    try:
        cur = conn.execute("DELETE FROM spending_features WHERE profile_id = ?", (_scoped_profile_id(profile_id),))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def load_synthetic(
    profile_id: str, months: int = 3, seed: int = 42, end: Optional[date] = None
) -> list[Transaction]:
    """app.data.synthetic.generate_transactions를 그대로 위임한다(합성 데이터 경로,
    페르소나가 아니면 ValueError - 라우트가 404로 변환한다)."""
    return synthetic.generate_transactions(profile_id, months=months, seed=seed, end=end or date.today())


def _pick_highest_rate_loan(profile: UserProfile) -> Optional[Loan]:
    if not profile.loans:
        return None
    return sorted(profile.loans, key=lambda l: (-l.annual_rate, l.id))[0]


def _emergency_fund_target(profile: UserProfile) -> int:
    """비상금 목표(부족분). 프로필에 emergency 목표가 있으면 그 잔여분, 없으면 고정지출
    3개월분에서 현재 비상금을 뺀 값(SPEC 2.15: "목표가 없으면 생활비 3개월분 기준")."""
    emergency_goal = next((g for g in profile.goals if g.kind == "emergency"), None)
    if emergency_goal is not None:
        return max(emergency_goal.target_amount - emergency_goal.saved_amount, 0)
    return max(profile.fixed_expenses * 3 - profile.emergency_fund, 0)


def link_savings_to_debt(
    profile: UserProfile, opportunities: list[SavingOpportunity], *, today: date,
) -> list[SpendingLinkedAction]:
    """지출 절감 후보를 최고금리 대출의 추가 상환 효과로 연결한다(SPEC 2.15).

    대출이 있으면 `extra_payment_effect(loan, monthly_saving)`로 단축 개월·절감 이자를
    계산해 문장을 만든다. 대출이 없으면 비상금 목표까지 걸리는 개월 수 문장을 만든다.
    0원 이하인 기회는 건너뛴다.
    """
    target_loan = _pick_highest_rate_loan(profile)
    out: list[SpendingLinkedAction] = []

    for opp in opportunities:
        if opp.monthly_saving <= 0:
            continue

        if target_loan is not None:
            effect = loan_core.extra_payment_effect(target_loan, opp.monthly_saving)
            loan_label = _LOAN_TYPE_LABELS_KR.get(target_loan.loan_type.value, "기타 대출")
            sentence = (
                f"{opp.label}(월 {opp.monthly_saving:,}원)을 줄여 {loan_label}에 더 갚으면 "
                f"{effect['months_saved']}개월 빨리 끝나고 이자 {effect['interest_saved']:,}원을 아껴요."
            )
            out.append(SpendingLinkedAction(
                opportunity=opp, target_loan_label=loan_label, months_saved=effect["months_saved"],
                interest_saved=effect["interest_saved"], new_months=effect["new_months"], sentence=sentence,
            ))
        else:
            target = _emergency_fund_target(profile)
            months_needed = math.ceil(target / opp.monthly_saving) if target > 0 else 0
            sentence = f"월 {opp.monthly_saving:,}원을 비상금으로 모으면 {months_needed}개월 만에 목표 비상금에 닿아요."
            out.append(SpendingLinkedAction(
                opportunity=opp, target_loan_label=None, months_saved=0, interest_saved=0,
                new_months=None, sentence=sentence,
            ))

    return out
