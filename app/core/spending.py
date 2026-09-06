"""소비 패턴 분석 (P5, SPEC 2.6) 순수 함수. I/O 없음, app.models만 import한다.

- `categorize`: 가맹점명 키워드 규칙으로 SpendingCategory를 매긴다(카드 파일 업종 컬럼이
  없는 PoC 환경 가정). 실제 원장의 `Transaction.category` 값은 신뢰하지 않고 항상
  merchant 문자열에서 다시 계산한다(업로드 데이터마다 원본 카테고리 표기가 다를 수 있어
  단일 규칙으로 통일한다).
- `aggregate`: 월별 버킷으로 나눠 카테고리 합계·구독·이상치·상위 가맹점을 계산한다.
  프로필을 받지 않으므로 `profile_id`와 `life_events`는 채우지 않는다(빈 값으로 둔다).
  서비스 레이어(`app/services/spending.py`)가 `detect_life_events` 결과와 profile_id를
  합쳐 최종 SpendingSummary를 만든다.
- `detect_life_events`: 생애주기 이벤트 프록시(문서 4.4절)를 거래내역 키워드와
  프로필 플래그로 추정한다.
- `compute_features`: SpendingSummary + UserProfile로 추천 엔진 입력 피처를 계산한다.
  `spending_consent_at`은 항상 None으로 둔다(동의 시각은 서비스 레이어가 분석 실행
  시점에 채운다 - D8). 이 함수는 시계(now())를 전혀 읽지 않으므로 같은 입력이면
  항상 같은 JSON을 낸다.
- `build_spending_cards`: addendum 2.9 규칙 중 이번 스코프에서 지원하는 IC01~IC06을
  카드로 만든다. 이 IC01~IC06 번호는 addendum 2.9의 전역 카탈로그(IC01~IC13) 번호와
  다른 이번 기능 전용 로컬 번호다(주석에서만 구분, 화면에는 노출하지 않음).

금칙어(문서 2.9): 비난·낙인·공포 표현, 권유형 어미("~하세요"), "추천", em dash. 이 파일의
모든 한국어 문장은 이 규칙을 따른다(관찰형 어미 "~해요/~있어요"만 사용).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from app.models import (
    AnomalyItem,
    CategoryTotal,
    Chip,
    InsightCard,
    LifeEventSignal,
    LoanType,
    SpendingCategory,
    SpendingFeatures,
    SpendingSummary,
    SubscriptionItem,
    Transaction,
    UserProfile,
)

# ---------------------------------------------------------------------------
# 택소노미: 가맹점 키워드 -> SpendingCategory (우선순위 순서로 검사한다)
# ---------------------------------------------------------------------------

_SALARY_TOKENS = ("급여", "월급", "상여금", "상여")
_TRANSFER_TOKENS = ("이체", "송금", "경조사")
_CASH_ADVANCE_TOKENS = ("현금서비스", "현금 서비스", "캐시서비스")
_DEBT_TOKENS = ("대출", "이자", "상환", "카드대금", "리볼빙", "마이너스통장", "카드론", "할부")
_SUBSCRIPTION_TOKENS = (
    "넷플릭스", "유튜브", "멜론", "왓챠", "웨이브", "디즈니", "지니뮤직", "밀리의서재", "쿠팡와우", "구독",
)
_CAFE_TOKENS = ("스타벅스", "카페", "이디야", "커피", "빽다방", "투썸", "폴바셋")
_FOOD_TOKENS = (
    "배달", "김밥", "치킨", "본죽", "죽", "마트", "편의점", "GS25", "CU", "세븐일레븐",
    "이마트", "맘스터치", "버거", "식당", "분식", "쿠팡이츠", "요기요",
)
_TRANSPORT_TOKENS = ("택시", "카카오T", "카카오t", "지하철", "버스", "티머니", "SRT", "KTX", "쏘카", "그린카")
_TELECOM_TOKENS = ("SKT", "KT", "LG유플러스", "텔레콤", "유플러스")
_MEDICAL_TOKENS = ("정형외과", "약국", "치과", "병원", "의원", "진료비", "산부인과")
_HOUSING_TOKENS = ("관리비", "도시가스", "한국전력", "월세", "전세", "가스요금", "전기요금", "수도요금", "임대료")
_INSURANCE_TOKENS = ("보험",)
_EDUCATION_TOKENS = ("학원", "과외", "인강", "학습지", "등록금")
_LEISURE_TOKENS = ("CGV", "야놀자", "골프존", "여기어때", "영화", "여행", "항공권", "호텔")
_SHOPPING_TOKENS = ("쿠팡", "무신사", "올리브영", "다이소", "가전", "노트북")

# 소비(=total_spend)에 포함되는 카테고리를 고정/변동/재량으로 분류한다(addendum 2.6:
# TRANSFER_INTERNAL은 제외, INCOME은 별도 집계). 대출상환/현금서비스는 addendum
# 원안에서는 DEBT_SERVICE로 소비와 분리하지만, 이번 P5 PoC 범위에서는 상환 여력이
# app.core.capacity가 이미 별도로 다루므로 소비 집계 화면에서는 "고정" 성격의 지출로
# 단순화해 함께 보여준다(SPEC 2.6에 명시).
_FIXED_CATEGORIES = {
    SpendingCategory.HOUSING, SpendingCategory.TELECOM, SpendingCategory.SUBSCRIPTION,
    SpendingCategory.INSURANCE, SpendingCategory.DEBT_REPAYMENT,
}
_VARIABLE_CATEGORIES = {
    SpendingCategory.FOOD, SpendingCategory.TRANSPORT, SpendingCategory.MEDICAL,
    SpendingCategory.EDUCATION, SpendingCategory.CASH_ADVANCE,
}
_DISCRETIONARY_CATEGORIES = {
    SpendingCategory.CAFE, SpendingCategory.SHOPPING, SpendingCategory.LEISURE, SpendingCategory.OTHER,
}

_EXCLUDED_FROM_SPEND = (SpendingCategory.SALARY, SpendingCategory.TRANSFER)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(tok in text for tok in tokens)


def categorize(merchant: str, amount: int, kind: str) -> SpendingCategory:
    """가맹점명 키워드로 SpendingCategory를 매긴다. 빈 문자열이어도 예외 없이 기타를 낸다.

    amount/kind는 현재 규칙에서는 분기에 쓰지 않지만(순수 키워드 매칭), 추후 금액대·
    거래 종류 기반 보정을 붙일 수 있도록 시그니처에 유지한다(SPEC 2.6).
    """
    text = (merchant or "").strip()
    if not text:
        return SpendingCategory.OTHER

    if _contains_any(text, _SALARY_TOKENS):
        return SpendingCategory.SALARY
    if _contains_any(text, _TRANSFER_TOKENS):
        return SpendingCategory.TRANSFER
    if _contains_any(text, _CASH_ADVANCE_TOKENS):
        return SpendingCategory.CASH_ADVANCE
    if _contains_any(text, _DEBT_TOKENS):
        return SpendingCategory.DEBT_REPAYMENT
    if _contains_any(text, _SUBSCRIPTION_TOKENS):
        return SpendingCategory.SUBSCRIPTION
    if _contains_any(text, _CAFE_TOKENS):
        return SpendingCategory.CAFE
    if _contains_any(text, _FOOD_TOKENS):
        return SpendingCategory.FOOD
    if _contains_any(text, _TRANSPORT_TOKENS):
        return SpendingCategory.TRANSPORT
    if _contains_any(text, _TELECOM_TOKENS):
        return SpendingCategory.TELECOM
    if _contains_any(text, _MEDICAL_TOKENS):
        return SpendingCategory.MEDICAL
    if _contains_any(text, _HOUSING_TOKENS):
        return SpendingCategory.HOUSING
    if _contains_any(text, _INSURANCE_TOKENS):
        return SpendingCategory.INSURANCE
    if _contains_any(text, _EDUCATION_TOKENS):
        return SpendingCategory.EDUCATION
    if _contains_any(text, _LEISURE_TOKENS):
        return SpendingCategory.LEISURE
    if _contains_any(text, _SHOPPING_TOKENS):
        return SpendingCategory.SHOPPING
    return SpendingCategory.OTHER


def taxonomy_info() -> dict[str, Any]:
    """GET /api/spending/taxonomy 응답용: 카테고리 목록과 키워드 규칙."""
    return {
        "categories": [c.value for c in SpendingCategory],
        "rules": {
            SpendingCategory.SALARY.value: list(_SALARY_TOKENS),
            SpendingCategory.TRANSFER.value: list(_TRANSFER_TOKENS),
            SpendingCategory.CASH_ADVANCE.value: list(_CASH_ADVANCE_TOKENS),
            SpendingCategory.DEBT_REPAYMENT.value: list(_DEBT_TOKENS),
            SpendingCategory.SUBSCRIPTION.value: list(_SUBSCRIPTION_TOKENS),
            SpendingCategory.CAFE.value: list(_CAFE_TOKENS),
            SpendingCategory.FOOD.value: list(_FOOD_TOKENS),
            SpendingCategory.TRANSPORT.value: list(_TRANSPORT_TOKENS),
            SpendingCategory.TELECOM.value: list(_TELECOM_TOKENS),
            SpendingCategory.MEDICAL.value: list(_MEDICAL_TOKENS),
            SpendingCategory.HOUSING.value: list(_HOUSING_TOKENS),
            SpendingCategory.INSURANCE.value: list(_INSURANCE_TOKENS),
            SpendingCategory.EDUCATION.value: list(_EDUCATION_TOKENS),
            SpendingCategory.LEISURE.value: list(_LEISURE_TOKENS),
            SpendingCategory.SHOPPING.value: list(_SHOPPING_TOKENS),
        },
    }


# ---------------------------------------------------------------------------
# 월 버킷 도우미 (app.data.synthetic과 독립적인 자체 구현 - core는 data를 import할 수 없다)
# ---------------------------------------------------------------------------


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _window_months(end: date, months: int) -> list[str]:
    keys = []
    for i in range(months - 1, -1, -1):
        yy, mm = _shift_month(end.year, end.month, -i)
        keys.append(f"{yy:04d}-{mm:02d}")
    return keys


def _period_start(end: date, months: int) -> date:
    yy, mm = _shift_month(end.year, end.month, -(months - 1))
    return date(yy, mm, 1)


def _is_consecutive(a: str, b: str) -> bool:
    """a, b가 "YYYY-MM" 형식이고 b가 a의 바로 다음 달이면 True."""
    ay, am = int(a[:4]), int(a[5:7])
    by, bm = int(b[:4]), int(b[5:7])
    return (ay * 12 + am) + 1 == (by * 12 + bm)


def _mask_merchant(name: str) -> str:
    s = (name or "").strip()
    if not s:
        return "**"
    return s[:2] + "**"


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------


def aggregate(transactions: list[Transaction], *, end: date, months: int = 3) -> SpendingSummary:
    """월별 버킷으로 나눠 카테고리 합계/구독/이상치/상위 가맹점을 계산한다.

    이체(TRANSFER)와 급여(SALARY)는 소비 집계에서 제외한다(addendum 2.6: TRANSFER_INTERNAL
    제외, INCOME 별도). 그 외 14개 카테고리의 합이 total_spend와 정확히 같다(카테고리
    목록에 없는 소비는 없다). fixed_spend + variable_spend + discretionary_spend도
    항상 total_spend와 같다(카테고리 분류가 세 집합을 정확히 분할하기 때문).

    profile_id와 life_events는 이 함수가 채우지 않는다(프로필을 받지 않는 시그니처이기
    때문 - SPEC 2.6). 호출부(app/services/spending.py)가 model_copy로 채운다.
    """
    months = max(1, months)
    period_start = _period_start(end, months)
    month_keys = _window_months(end, months)
    in_window = [t for t in transactions if period_start <= t.date <= end]

    cat_month_amount: dict[SpendingCategory, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    cat_month_count: dict[SpendingCategory, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    income_deposits = 0
    merchant_amounts: dict[str, int] = defaultdict(int)
    merchant_month_amounts: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for t in in_window:
        cat = categorize(t.merchant, t.amount, t.kind)
        mk = _month_key(t.date)
        if cat == SpendingCategory.SALARY:
            income_deposits += t.amount
            continue
        if cat == SpendingCategory.TRANSFER:
            continue
        cat_month_amount[cat][mk] += t.amount
        cat_month_count[cat][mk] += 1
        merchant_amounts[t.merchant] += t.amount
        merchant_month_amounts[t.merchant][mk].append(t.amount)

    last_key = month_keys[-1]
    prev_key = month_keys[-2] if len(month_keys) >= 2 else None

    categories: list[CategoryTotal] = []
    for cat in SpendingCategory:
        if cat in _EXCLUDED_FROM_SPEND:
            continue
        by_month = cat_month_amount.get(cat, {})
        amount = sum(by_month.values())
        count = sum(cat_month_count.get(cat, {}).values())
        if amount == 0 and count == 0:
            continue
        prev_amt = by_month.get(prev_key, 0) if prev_key else 0
        last_amt = by_month.get(last_key, 0)
        change_pct = round((last_amt - prev_amt) / prev_amt * 100, 1) if prev_amt > 0 else None
        categories.append(CategoryTotal(
            category=cat, amount=amount, count=count, share=0.0,
            prev_amount=prev_amt, change_pct=change_pct,
        ))

    total_spend = sum(c.amount for c in categories)
    if total_spend > 0:
        categories = [c.model_copy(update={"share": round(c.amount / total_spend, 4)}) for c in categories]
    categories.sort(key=lambda c: (-c.amount, c.category.value))

    fixed_spend = sum(c.amount for c in categories if c.category in _FIXED_CATEGORIES)
    variable_spend = sum(c.amount for c in categories if c.category in _VARIABLE_CATEGORIES)
    discretionary_spend = sum(c.amount for c in categories if c.category in _DISCRETIONARY_CATEGORIES)

    # 이상치: 연속된 두 달 사이 카테고리별 +30% 이상 그리고 +50,000원 이상 증가.
    anomalies: list[AnomalyItem] = []
    for cat in SpendingCategory:
        if cat in _EXCLUDED_FROM_SPEND:
            continue
        by_month = cat_month_amount.get(cat, {})
        for i in range(1, len(month_keys)):
            cur_amt = by_month.get(month_keys[i], 0)
            prev_amt2 = by_month.get(month_keys[i - 1], 0)
            if prev_amt2 <= 0:
                continue
            diff = cur_amt - prev_amt2
            pct = diff / prev_amt2 * 100
            if pct >= 30 and diff >= 50_000:
                anomalies.append(AnomalyItem(
                    category=cat, month=month_keys[i], amount=cur_amt, prev_amount=prev_amt2,
                    change_pct=round(pct, 1),
                    note=f"{cat.value} 지출이 전월보다 {diff:,}원 늘었어요.",
                ))

    # 구독: 같은 원 가맹점이 서로 인접한 두 달 이상에서 비슷한 "월 합계" 금액(±10%)으로
    # 반복. 월별 총액(sum)을 비교해야 한다 - 월별 평균(건당 단가)을 쓰면 스타벅스처럼
    # 매달 방문 횟수는 달라도 건당 단가는 늘 비슷한 고빈도 변동 가맹점이 구독으로
    # 오탐된다(2026-09-06 실측, P1 합성 데이터로 확인).
    subscriptions: list[SubscriptionItem] = []
    for merchant, by_month in merchant_month_amounts.items():
        seen_keys = sorted(k for k in by_month if k in month_keys)
        if len(seen_keys) < 2:
            continue
        month_totals = [sum(by_month[k]) for k in seen_keys]
        overall_avg = sum(month_totals) / len(month_totals)
        if overall_avg <= 0:
            continue
        near_equal = all(abs(a - overall_avg) <= overall_avg * 0.10 + 1 for a in month_totals)
        has_consecutive = any(
            _is_consecutive(seen_keys[i], seen_keys[i + 1]) for i in range(len(seen_keys) - 1)
        )
        if near_equal and has_consecutive:
            subscriptions.append(SubscriptionItem(
                merchant=_mask_merchant(merchant),
                amount=round(overall_avg),
                months_seen=len(seen_keys),
                category=categorize(merchant, round(overall_avg), "card"),
            ))
    subscriptions.sort(key=lambda s: (-s.amount, s.merchant))

    top_merchants_masked = [
        _mask_merchant(m) for m, _ in sorted(merchant_amounts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    avg_monthly_spend = round(total_spend / months)

    return SpendingSummary(
        profile_id="",
        period_start=period_start,
        period_end=end,
        months=months,
        total_spend=total_spend,
        avg_monthly_spend=avg_monthly_spend,
        fixed_spend=fixed_spend,
        variable_spend=variable_spend,
        discretionary_spend=discretionary_spend,
        income_deposits=income_deposits,
        categories=categories,
        subscriptions=subscriptions,
        anomalies=anomalies,
        life_events=[],
        top_merchants_masked=top_merchants_masked,
    )


# ---------------------------------------------------------------------------
# detect_life_events (문서 lifecycle_domain_v1.txt 4.4절 프록시)
# ---------------------------------------------------------------------------

_WEDDING_TOKENS = ("예식장", "웨딩", "스튜디오", "혼수")
_CHILDBIRTH_TOKENS = ("산부인과", "유아", "기저귀", "분유")
_REFINANCE_LOAN_TYPES = (LoanType.CREDIT.value, LoanType.CARD_LOAN.value, LoanType.OVERDRAFT.value)


def _salary_by_month(transactions: list[Transaction]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for t in transactions:
        if categorize(t.merchant, t.amount, t.kind) == SpendingCategory.SALARY:
            out[_month_key(t.date)] += t.amount
    return dict(out)


def _all_months(transactions: list[Transaction]) -> list[str]:
    return sorted({_month_key(t.date) for t in transactions})


def detect_life_events(transactions: list[Transaction], profile: UserProfile) -> list[LifeEventSignal]:
    """생애주기 이벤트 신호를 소비 프록시와 프로필 플래그로 추정한다(문서 4.4절).

    evidence는 규칙이 만든 범주 수준 설명 문자열만 담는다(가맹점 원문 노출 금지, SPEC D3/D4).
    """
    signals: list[LifeEventSignal] = []

    wedding_hits = [t for t in transactions if _contains_any(t.merchant or "", _WEDDING_TOKENS)]
    if wedding_hits:
        amt = sum(t.amount for t in wedding_hits)
        confidence = round(min(0.4 + 0.15 * len(wedding_hits), 0.9), 2)
        signals.append(LifeEventSignal(
            kind="wedding", confidence=confidence,
            evidence=[f"결혼 준비 관련 결제 {len(wedding_hits)}건, 합계 {amt:,}원이 확인돼요"],
        ))

    childbirth_hits = [t for t in transactions if _contains_any(t.merchant or "", _CHILDBIRTH_TOKENS)]
    if childbirth_hits:
        amt = sum(t.amount for t in childbirth_hits)
        confidence = round(min(0.4 + 0.15 * len(childbirth_hits), 0.9), 2)
        signals.append(LifeEventSignal(
            kind="childbirth", confidence=confidence,
            evidence=[f"출산·육아 준비 관련 결제 {len(childbirth_hits)}건, 합계 {amt:,}원이 확인돼요"],
        ))

    # 급여 입금 중단/감소는 "급여가 관측된 달"끼리만 비교하면 안 된다 - 급여 거래가
    # 아예 없어진 달도 다른 거래(카드 이용 등)는 있을 수 있으므로, 거래가 존재하는
    # 모든 달의 시계열에서 마지막/그 이전 달을 비교하고 급여가 없는 달은 0으로 본다.
    salary_by_month = _salary_by_month(transactions)
    all_months = _all_months(transactions)
    income_drop_detected = False
    if len(all_months) >= 2:
        last_key, prev_key = all_months[-1], all_months[-2]
        last, prev = salary_by_month.get(last_key, 0), salary_by_month.get(prev_key, 0)
        if prev > 0 and last == 0:
            signals.append(LifeEventSignal(
                kind="job_change", confidence=0.6,
                evidence=["최근 달에는 급여 입금이 확인되지 않았어요(이전 달에는 있었어요)"],
            ))
            income_drop_detected = True
        elif prev > 0 and last < prev * 0.7:
            drop_pct = round((prev - last) / prev * 100, 1)
            signals.append(LifeEventSignal(
                kind="income_drop", confidence=0.55,
                evidence=[f"급여 입금액이 전월보다 {drop_pct}% 줄었어요"],
            ))
            income_drop_detected = True

    if profile.age is not None and profile.age >= 55 and (income_drop_detected or "retirement_near" in profile.flags):
        signals.append(LifeEventSignal(
            kind="retirement_near", confidence=0.5,
            evidence=["55세 이상이며 소득 변화 신호 또는 은퇴 임박 표시가 함께 있어요"],
        ))

    near_maturity_loans = [l for l in profile.loans if l.remaining_months <= 6]
    high_rate_credit = [
        l for l in profile.loans if l.loan_type.value in _REFINANCE_LOAN_TYPES and l.annual_rate >= 7.0
    ]
    if near_maturity_loans or ("income_up" in profile.flags and high_rate_credit):
        reason: list[str] = []
        if near_maturity_loans:
            reason.append(f"만기가 6개월 이내로 가까운 대출이 {len(near_maturity_loans)}건 있어요")
        if "income_up" in profile.flags and high_rate_credit:
            reason.append(f"소득 증가 신호와 함께 금리 연 7% 이상인 대출이 {len(high_rate_credit)}건 있어요")
        signals.append(LifeEventSignal(kind="refinance_window", confidence=0.5, evidence=reason))

    return signals


# ---------------------------------------------------------------------------
# compute_features
# ---------------------------------------------------------------------------


def compute_features(summary: SpendingSummary, profile: UserProfile) -> SpendingFeatures:
    """SpendingSummary + UserProfile로 추천 엔진 입력 피처를 계산한다(순수, 시계 미사용).

    spending_consent_at은 항상 None(서비스 레이어가 분석 실행 시각으로 채운다 - D8).
    computed_at은 summary.period_end를 쓴다(같은 입력이면 항상 같은 JSON을 내기 위함).
    """
    total = summary.total_spend
    fixed_ratio = round(summary.fixed_spend / total, 4) if total > 0 else 0.0
    discretionary_ratio = round(summary.discretionary_spend / total, 4) if total > 0 else 0.0

    subscription_total = sum(s.amount for s in summary.subscriptions)
    subscription_count = len(summary.subscriptions)

    top_categories = [c.category.value for c in sorted(summary.categories, key=lambda c: -c.amount)[:3]]

    # 카테고리별 전월 대비 변화율을 이전 달 금액으로 가중평균한 값(월별 총지출 시계열을
    # 별도로 들고 있지 않으므로 카테고리 단위 변화율의 가중평균으로 근사한다).
    weighted_pairs = [
        (c.prev_amount, c.change_pct) for c in summary.categories
        if c.change_pct is not None and c.prev_amount > 0
    ]
    if weighted_pairs:
        weight_sum = sum(w for w, _ in weighted_pairs)
        spend_trend_pct = round(sum(w * p for w, p in weighted_pairs) / weight_sum, 1) if weight_sum else None
    else:
        spend_trend_pct = None

    if profile.monthly_income > 0:
        income_regularity = round(min(1.0, summary.income_deposits / (profile.monthly_income * summary.months)), 4)
    else:
        income_regularity = 0.0

    # 저축 여력(수입 - 지출 - 상환). avg_monthly_spend에는 대출상환 카테고리가 이미
    # 포함돼 있으므로(fixed_spend에 DEBT_REPAYMENT 포함) 수입에서 한 번만 빼면 된다.
    income_basis = summary.income_deposits // summary.months if summary.income_deposits > 0 else profile.monthly_income
    savings_capacity = income_basis - summary.avg_monthly_spend

    life_event_kinds = [e.kind for e in summary.life_events]

    total_count = sum(c.count for c in summary.categories)
    other_count = next((c.count for c in summary.categories if c.category == SpendingCategory.OTHER), 0)
    classification_quality = round(1 - other_count / total_count, 4) if total_count > 0 else 1.0

    data_coverage_days = (summary.period_end - summary.period_start).days + 1
    net_cash_flow_monthly = income_basis - summary.avg_monthly_spend

    return SpendingFeatures(
        profile_id=summary.profile_id,
        computed_at=summary.period_end,
        months=summary.months,
        avg_monthly_spend=summary.avg_monthly_spend,
        fixed_ratio=fixed_ratio,
        discretionary_ratio=discretionary_ratio,
        subscription_total=subscription_total,
        subscription_count=subscription_count,
        top_categories=top_categories,
        spend_trend_pct=spend_trend_pct,
        anomaly_count=len(summary.anomalies),
        income_regularity=income_regularity,
        savings_capacity=savings_capacity,
        life_event_kinds=life_event_kinds,
        spending_consent_at=None,
        income_monthly_est=income_basis,
        net_cash_flow_monthly=net_cash_flow_monthly,
        data_coverage_days=data_coverage_days,
        classification_quality=classification_quality,
    )


# ---------------------------------------------------------------------------
# build_spending_cards (addendum 2.9 IC01~IC06, 이번 기능 로컬 번호)
# ---------------------------------------------------------------------------

_LIFE_EVENT_LABELS: dict[str, str] = {
    "wedding": "결혼 준비 관련 지출 패턴이 보여요",
    "childbirth": "출산·육아 준비 관련 지출 패턴이 보여요",
    "job_change": "최근 급여 입금 패턴에 변화가 있었어요",
    "income_drop": "최근 급여 입금액에 변화가 있었어요",
    "retirement_near": "은퇴 준비 시기에 가까워지고 있어요",
    "refinance_window": "대출 조건을 다시 살펴볼 시점일 수 있어요",
}


def _spending_chip(chip_id: str, text: str, intent: str, params: dict[str, Any]) -> Chip:
    return Chip(id=chip_id, text=text, tier=1, intent=intent, params=params)


def build_spending_cards(
    features: SpendingFeatures, summary: SpendingSummary, profile: UserProfile
) -> list[InsightCard]:
    """addendum 2.9 규칙 중 지원하는 IC01~IC06을 우선순위 순서로 만든다(비난·낙인·공포
    표현, 권유형 어미, "추천", em dash를 쓰지 않는다). 조건이 안 맞는 카드는 건너뛴다."""
    cards: list[InsightCard] = []

    # IC05 생애 이벤트 신호(질문 카드, 상품 언급 금지) - 신뢰도가 가장 높은 신호 하나만.
    if summary.life_events:
        top_event = sorted(summary.life_events, key=lambda e: -e.confidence)[0]
        label = _LIFE_EVENT_LABELS.get(top_event.kind, "최근 지출 패턴에 변화가 있었어요")
        evidence = {"신호": top_event.evidence[0] if top_event.evidence else label,
                    "신뢰도": f"{round(top_event.confidence * 100)}%"}
        cards.append(InsightCard(
            id="card-spending-ic05", kind="spending", tone="neutral",
            title="요즘 변화가 있으신가요?",
            body=f"{label}. 관련 내용을 같이 살펴볼까요?",
            evidence=evidence,
            chip=_spending_chip("chip-spending-ic05", "소비 패턴 보기", "spending",
                                {"life_event": top_event.kind}),
            source_rule="IC05",
            explain="결혼·출산·소득 변화 같은 생애 이벤트 신호를 거래 내역과 프로필 정보로 감지했어요.",
        ))

    # IC01 소비 급증 카테고리
    if summary.anomalies:
        top = sorted(summary.anomalies, key=lambda a: -a.change_pct)[0]
        cat_label = top.category.value
        cards.append(InsightCard(
            id="card-spending-ic01", kind="spending", tone="neutral",
            title="지출이 늘어난 카테고리가 있어요",
            body=f"{cat_label} 지출이 지난달보다 늘었어요. 어디서 늘었는지 같이 볼까요?",
            evidence={
                f"{cat_label} 이번 달": f"{top.amount:,}원",
                f"{cat_label} 지난달": f"{top.prev_amount:,}원",
                "변화율": f"{round(top.change_pct)}%",
            },
            chip=_spending_chip("chip-spending-ic01", "소비 패턴 보기", "spending",
                                {"category": top.category.value}),
            source_rule="IC01",
            explain=f"최근 {summary.months}개월 동안 카테고리별 지출을 월별로 모아 봤을 때, 전월보다 "
                    "30% 이상, 5만원 이상 늘어난 카테고리를 찾았어요.",
        ))

    # IC06 소득 불규칙. 급여 입금이 아예 관측되지 않은 경우(summary.income_deposits == 0)는
    # "불규칙"이 아니라 "데이터 없음"이므로 이 카드를 내지 않는다(오해 방지).
    if profile.monthly_income > 0 and summary.income_deposits > 0 and features.income_regularity < 0.7:
        cards.append(InsightCard(
            id="card-spending-ic06", kind="spending", tone="neutral",
            title="소득이 불규칙한 편이에요",
            body=f"최근 {summary.months}개월 급여 입금 패턴이 고르지 않아요. "
                 "평균 기준으로 계획을 세워보는 것도 방법이에요.",
            evidence={"소득 규칙성": f"{round(features.income_regularity * 100)}%"},
            chip=_spending_chip("chip-spending-ic06", "소비 패턴 보기", "spending", {"view": "income"}),
            source_rule="IC06",
            explain="최근 급여 입금 합계를 등록하신 월소득과 비교한 비율이에요.",
        ))

    # IC04 저축 여력(수입 - 지출 - 상환)
    if summary.total_spend > 0:
        cap = features.savings_capacity
        if cap >= 0:
            body = f"수입에서 지출과 상환을 뺀 이번 달 저축 여력은 약 {cap:,}원이에요."
            tone = "positive"
        else:
            body = f"이번 달은 수입보다 지출과 상환이 약 {abs(cap):,}원 더 많았어요."
            tone = "neutral"
        cards.append(InsightCard(
            id="card-spending-ic04", kind="spending", tone=tone,
            title="이번 달 저축 여력",
            body=body,
            evidence={"저축 여력": f"{cap:,}원", "월 평균 지출": f"{summary.avg_monthly_spend:,}원"},
            chip=_spending_chip("chip-spending-ic04", "시나리오로 확인하기", "scenario",
                                {"extra_repayment_amount": max(cap, 0)}),
            source_rule="IC04",
            explain="등록된 월소득(또는 급여 입금 합계)에서 최근 월평균 지출을 뺀 값이에요.",
        ))

    # IC03 고정지출 비율
    if summary.total_spend > 0:
        pct = round(features.fixed_ratio * 100)
        cards.append(InsightCard(
            id="card-spending-ic03", kind="spending", tone="neutral",
            title="고정지출 비율",
            body=f"최근 {summary.months}개월 지출 중 고정지출이 차지하는 비율은 {pct}%예요. "
                 "나머지는 재량 소비로 조정할 여지가 있어요.",
            evidence={"고정지출 비율": f"{pct}%", "월 평균 지출": f"{summary.avg_monthly_spend:,}원"},
            chip=_spending_chip("chip-spending-ic03", "시나리오로 확인하기", "scenario",
                                {"discretionary_cut_pct": 10}),
            source_rule="IC03",
            explain="고정지출(주거·통신·구독·보험·대출상환) 합계를 총지출로 나눈 값이에요.",
        ))

    # IC02 구독 합계
    if features.subscription_count > 0:
        cards.append(InsightCard(
            id="card-spending-ic02", kind="spending", tone="neutral",
            title="정기 결제 현황",
            body=f"현재 {features.subscription_count}건의 정기 결제가 확인돼요. "
                 f"월 합계는 약 {features.subscription_total:,}원이에요.",
            evidence={"정기 결제 건수": f"{features.subscription_count}건",
                      "월 합계": f"{features.subscription_total:,}원"},
            chip=_spending_chip("chip-spending-ic02", "소비 패턴 보기", "spending", {"view": "subscriptions"}),
            source_rule="IC02",
            explain="같은 가맹점에서 비슷한 금액이 인접한 두 달 이상 반복된 결제를 모았어요.",
        ))

    return cards
