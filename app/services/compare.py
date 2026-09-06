"""공시 비교(M0) 서비스: 조건 추정, 실행, 결정 기록 저장 (SPEC 2.3).

스냅샷 처리 메모: `app/data/products.query(category)`(snapshot_id 생략)는 이
category를 담은 각 source(finlife/datago 등)의 최신 스냅샷을 모두 모아 합친다
(예: MORTGAGE = finlife 은행 주담대 스냅샷 + datago 디딤돌 스냅샷). 이 서비스는
실제 실행 시 그 합쳐진 결과를 쓰되, 재현을 위해 실제로 쓰인 스냅샷 id들을
"+"로 이어붙인 문자열을 `CompareResult.snapshot_id`/`DecisionRecord.versions
["snapshot_id"]`에 남긴다. `decisions.replay`는 이 문자열을 다시 "+"로 나눠
각 스냅샷 id로 `products.query(category, snapshot_id=...)`를 호출해 정확히 같은
상품 집합을 복원한다(단일 snapshot_id 전역 최신값을 그대로 쓰면, 예를 들어
가장 최근 적재가 datago였을 때 finlife에만 있는 credit 카테고리가 통째로
비어버리는 오류가 생길 수 있어 이 방식을 쓴다).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Optional

from app.core import hashing, ranking
from app.core.schedule import build_schedule
from app.data import policy as policy_data
from app.data import products
from app.models import (
    ENGINE_VERSION,
    CompareContext,
    CompareResult,
    DecisionRecord,
    LenderGroup,
    Loan,
    LoanType,
    ProductCategory,
    RepayMethod,
    SortKey,
    UserProfile,
)
from app.services import decisions

# 대출 종류 -> 비교 카테고리. R3(대환 후보) 칩과 홈 화면 파생 칩이 함께 쓴다.
LOAN_TYPE_TO_CATEGORY: dict[LoanType, ProductCategory] = {
    LoanType.CREDIT: ProductCategory.CREDIT,
    LoanType.CARD_LOAN: ProductCategory.CREDIT,
    LoanType.OVERDRAFT: ProductCategory.CREDIT,
    LoanType.STUDENT: ProductCategory.CREDIT,
    LoanType.MORTGAGE: ProductCategory.MORTGAGE,
    LoanType.JEONSE: ProductCategory.JEONSE,
    LoanType.POLICY: ProductCategory.POLICY,
    LoanType.OTHER: ProductCategory.CREDIT,
}

_CREDIT_LIKE_TYPES = (LoanType.CREDIT, LoanType.CARD_LOAN, LoanType.OVERDRAFT, LoanType.STUDENT)
_DEFAULT_LENDER_GROUPS = [LenderGroup.BANK.value, LenderGroup.SAVINGS_BANK.value]

# 결정 D5: 예·적금은 "더 나은 상품 순위"라는 개념 자체가 성립하지 않는 상품(금리가 곧
# 원금 손실 위험과 직결되지 않고, 예금자보호 한도·중도해지 조건 등 순위화로 단순화하면
# 안 되는 요소가 많다)이라 M0 순위 비교 대상에서 제외한다. 공시 열람(금감원 금융상품
# 한눈에 링크)만 제공한다.
_NO_RANKING_CATEGORIES = (ProductCategory.DEPOSIT, ProductCategory.SAVING)
NO_RANKING_CATEGORY_MESSAGE = "예·적금은 순위 비교 대상이 아닙니다. 공시 열람만 제공합니다."

# CompareContext.estimated_fields의 영문 키를 화면 안내 문장에 쓸 한글 라벨로 바꾼다.
# 목록에 없는 키(채팅 경로 등에서 넘어온 값)는 원문 그대로 보여준다(방어적 처리).
_ESTIMATED_FIELD_LABELS_KR: dict[str, str] = {
    "amount": "금액",
    "term_months": "기간",
    "category": "카테고리",
    "credit_band": "신용 구간",
    "repay_method": "상환방식",
    "target_loan_id": "대상 대출",
}


def _format_disclosure_month(raw: str) -> str:
    """"202608" 같은 YYYYMM 문자열을 "2026년 8월"로 바꾼다. 형식이 다르면 원문 그대로 둔다."""
    if len(raw) == 6 and raw.isdigit():
        return f"{raw[:4]}년 {int(raw[4:6])}월"
    return raw


def _pick_target_loan(
    profile: Optional[UserProfile], target_loan_id: Optional[str]
) -> tuple[Optional[Loan], bool]:
    """target_loan_id가 있으면 그 대출을, 없으면 최고금리 대출(신용성 우선)을 고른다.
    두 번째 반환값은 target_loan_id 자체를 추정했는지 여부."""
    if not profile or not profile.loans:
        return None, False
    if target_loan_id:
        found = next((l for l in profile.loans if l.id == target_loan_id), None)
        if found is not None:
            return found, False
    credit_like = [l for l in profile.loans if l.loan_type in _CREDIT_LIKE_TYPES]
    pool = credit_like or list(profile.loans)
    best = sorted(pool, key=lambda l: (-l.annual_rate, l.id))[0]
    return best, True


def prepare_context(profile: Optional[UserProfile], intent_params: dict[str, Any]) -> CompareContext:
    """SPEC 2.3: target_loan=target_loan_id 또는 최고금리 대출, amount=잔액,
    term=남은 개월, credit_band=프로필 값. 추정한 필드는 estimated_fields에 남기고
    user_confirmed=False로 돌려준다. 프로필이 없거나 params가 비어 있어도(공통
    기본값 신용대출/1,000만원/36개월) 항상 쓸 수 있는 컨텍스트를 만든다.
    """
    params = dict(intent_params or {})
    estimated: list[str] = []

    target_loan_id_in = params.get("target_loan_id")
    target_loan, target_estimated = _pick_target_loan(profile, target_loan_id_in)
    if target_loan is not None and target_estimated:
        estimated.append("target_loan_id")
    target_loan_id = target_loan.id if target_loan is not None else None

    category: Optional[ProductCategory] = None
    category_raw = params.get("category")
    if category_raw:
        try:
            category = ProductCategory(category_raw)
        except ValueError:
            category = None
    if category is None:
        if target_loan is not None:
            category = LOAN_TYPE_TO_CATEGORY.get(target_loan.loan_type, ProductCategory.CREDIT)
        else:
            category = ProductCategory.CREDIT
        estimated.append("category")
    elif target_loan is not None and LOAN_TYPE_TO_CATEGORY.get(target_loan.loan_type, ProductCategory.CREDIT) != category:
        # 사용자가 지정한 카테고리와 다른 대출(예: 전세 비교인데 신용대출)의 잔액·기간을 가져오면
        # "전세 4개월" 같은 오해를 만든다. 이 경우 대상 대출을 쓰지 않고 기본값으로 추정한다.
        target_loan = None
        target_loan_id = None
        if "target_loan_id" in estimated:
            estimated.remove("target_loan_id")

    amount = params.get("amount")
    if amount is None:
        amount = target_loan.balance if target_loan is not None else 10_000_000
        estimated.append("amount")

    term_months = params.get("term_months")
    if term_months is None:
        term_months = target_loan.remaining_months if target_loan is not None else 36
        estimated.append("term_months")
    term_months = max(int(term_months), 1)

    credit_band = params.get("credit_band")
    if (
        credit_band is None
        and profile is not None
        and profile.credit_band
        and category not in _NO_RANKING_CATEGORIES
    ):
        # 예·적금(DEPOSIT/SAVING)은 신용점수 구간별 금리 차등이 없는 상품이라(D5,
        # run_compare가 애초에 순위 비교 자체를 거부한다) 프로필의 credit_band를
        # 추정해 끼워 넣지 않는다.
        credit_band = profile.credit_band
        estimated.append("credit_band")

    repay_method_raw = params.get("repay_method")
    repay_method: Any
    if repay_method_raw:
        try:
            repay_method = RepayMethod(repay_method_raw)
        except ValueError:
            repay_method = RepayMethod.EQUAL_PAYMENT
    else:
        # 비교 대상(새 대출)은 원리금균등 기준으로 계산한다. 대상 대출이 리볼빙·만기일시여도
        # 대환 후에는 분할상환이 일반적이므로, 방식이 다르면 추정 필드로 표시해 사용자가 바꿀 수 있게 한다.
        repay_method = RepayMethod.EQUAL_PAYMENT
        if target_loan is not None and target_loan.repay_method != RepayMethod.EQUAL_PAYMENT:
            estimated.append("repay_method")

    sort_key_raw = params.get("sort_key")
    try:
        sort_key = SortKey(sort_key_raw) if sort_key_raw else SortKey.TOTAL_COST
    except ValueError:
        sort_key = SortKey.TOTAL_COST

    rate_type = params.get("rate_type") or None
    max_rate = params.get("max_rate")
    try:
        max_rate = float(max_rate) if max_rate is not None else None
    except (TypeError, ValueError):
        max_rate = None

    exclude_companies = params.get("exclude_companies") or []
    if not isinstance(exclude_companies, list):
        exclude_companies = [exclude_companies]

    lender_groups = params.get("lender_groups") or list(_DEFAULT_LENDER_GROUPS)

    return CompareContext(
        category=category,
        amount=int(amount),
        term_months=term_months,
        sort_key=sort_key,
        repay_method=repay_method,
        rate_type=rate_type,
        credit_band=credit_band,
        lender_groups=lender_groups,
        exclude_companies=list(exclude_companies),
        max_rate=max_rate,
        target_loan_id=target_loan_id,
        estimated_fields=list(dict.fromkeys([*(params.get("estimated_fields") or []), *estimated])),  # 호출자가 넘긴 추정 목록 보존(채팅 경로)
        user_confirmed=False,
    )


def run_compare(ctx: CompareContext, profile: Optional[UserProfile], *, today: date) -> CompareResult:
    """SPEC 2.3: user_confirmed가 False면 ValueError(호출부가 HTTP 422로 변환).
    products.query -> ranking.eligible/rank -> result_hash -> decisions.save.

    결정 D5: category가 예금/적금이면 순위 비교 자체를 거부한다(호출부가 마찬가지로
    422로 변환, 2026-09-06 리뷰).
    """
    if ctx.category in _NO_RANKING_CATEGORIES:
        raise ValueError(NO_RANKING_CATEGORY_MESSAGE)
    if not ctx.user_confirmed:
        raise ValueError("조건 확인(user_confirmed=true) 후에만 비교를 실행할 수 있습니다.")

    params = policy_data.load_policy_params()

    prods = products.query(ctx.category)
    snapshot_ids = sorted({p.snapshot_id for p in prods})
    snapshot_label = "+".join(snapshot_ids) if snapshot_ids else (products.latest_snapshot_id() or "")

    target_loan = None
    if profile is not None and ctx.target_loan_id:
        target_loan = next((l for l in profile.loans if l.id == ctx.target_loan_id), None)
    current_total_interest = (
        build_schedule(target_loan).total_interest if target_loan is not None else None
    )

    eligible_products = ranking.eligible(prods, ctx)
    items = ranking.rank(eligible_products, ctx, params, current_total_interest=current_total_interest)

    disclosure_months = sorted({p.disclosure_month for p in eligible_products if p.disclosure_month})
    disclosure_month_text = (
        ", ".join(_format_disclosure_month(m) for m in disclosure_months)
        if disclosure_months else "확인 필요"
    )

    assumptions = [
        f"공시 자료 기준: {disclosure_month_text}",
        "금액은 원 단위로 반올림했어요.",
        "공시된 금리는 신청 시점의 공시 기준값이고, 실제 승인 금리와 한도는 금융회사 심사 결과에 "
        "따라 달라질 수 있어요.",
    ]
    if ctx.estimated_fields:
        labels = [_ESTIMATED_FIELD_LABELS_KR.get(f, f) for f in ctx.estimated_fields]
        assumptions.append(
            f"다음 조건은 등록된 정보로 추정했어요: {', '.join(labels)}. "
            "조건 확인 화면에서 바꿀 수 있어요."
        )

    versions: dict[str, str] = {
        "engine_version": ENGINE_VERSION,
        "rules_version": params.version,
        "snapshot_id": snapshot_label,
    }

    result_hash_value = hashing.result_hash(items, ctx, snapshot_label, versions)
    decision_id = f"dec-{uuid.uuid4().hex[:16]}"
    created_at = datetime.now()

    result = CompareResult(
        decision_id=decision_id,
        result_hash=result_hash_value,
        mode="M0",
        context=ctx,
        items=items,
        sort_explain=ranking.sort_explain(ctx),
        assumptions=assumptions,
        snapshot_id=snapshot_label,
        rules_version=params.version,
        created_at=created_at,
        candidates_total=len(eligible_products),
    )

    # current_total_interest는 CompareContext 모델 필드가 아니라서 재현(replay)을 위해
    # context_json에 비공개 키로 함께 넣어 둔다. CompareContext.model_validate는 알 수
    # 없는 키를 조용히 무시하므로 다시 읽을 때도 안전하다.
    context_payload = ctx.model_dump(mode="json")
    context_payload["_current_total_interest"] = current_total_interest

    record = DecisionRecord(
        decision_id=decision_id,
        kind="compare",
        input_fingerprint=hashing.fingerprint(ctx),
        result_hash=result_hash_value,
        context=context_payload,
        result=result.model_dump(mode="json"),
        versions=versions,
        created_at=created_at,
    )
    decisions.save(record)

    return result
