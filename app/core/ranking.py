"""공시 상품 비교(M0) 랭킹 (순수 함수).

I/O 없음. app.models와 app.core.schedule(같은 패키지)만 import한다.
"""
from __future__ import annotations

from app.core.schedule import build_schedule
from app.models import (
    CompareContext,
    CompareItem,
    Loan,
    LoanType,
    PolicyParams,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    RateSemantics,
    RateType,
    SortKey,
)

_GROUP_NAME = {
    "bank": "은행",
    "savings_bank": "저축은행",
    "card": "카드사",
    "capital": "캐피탈",
    "insurance": "보험사",
    "policy": "정책상품",
    "other": "기타",
}

_CATEGORY_NAME = {
    "deposit": "정기예금",
    "saving": "적금",
    "mortgage": "주택담보대출",
    "jeonse": "전세자금대출",
    "credit": "신용대출",
    "policy": "정책서민금융",
}

_CATEGORY_TO_LOAN_TYPE = {
    ProductCategory.CREDIT: LoanType.CREDIT,
    ProductCategory.MORTGAGE: LoanType.MORTGAGE,
    ProductCategory.JEONSE: LoanType.JEONSE,
    ProductCategory.POLICY: LoanType.POLICY,
    ProductCategory.DEPOSIT: LoanType.OTHER,
    ProductCategory.SAVING: LoanType.OTHER,
}

_RATE_KIND_RANK = {"avg": 0, "base": 1, "min": 2}


def eligible(products: list[ProductSnapshot], ctx: CompareContext) -> list[ProductSnapshot]:
    """카테고리, 취급 그룹, 제외 회사, 금리 유형, 상한금리, 신용구간 조건을 만족하는 상품."""
    result = []
    for p in products:
        if p.category != ctx.category:
            continue
        if p.lender_group not in ctx.lender_groups:
            continue
        if ctx.exclude_companies and (
            p.company_name in ctx.exclude_companies or p.company_code in ctx.exclude_companies
        ):
            continue
        if ctx.rate_type is not None:
            if not any(o.rate_type == ctx.rate_type.value for o in p.options):
                continue
        if ctx.max_rate is not None:
            if not any(o.rate is not None and o.rate <= ctx.max_rate for o in p.options):
                continue
        if ctx.credit_band is not None:
            has_band = any(o.credit_band == ctx.credit_band for o in p.options)
            has_avg = any(o.rate_kind == "avg" for o in p.options)
            if not (has_band or has_avg):
                continue
        result.append(p)
    return result


def select_option(product: ProductSnapshot, ctx: CompareContext) -> ProductOption | None:
    """credit_band 일치 → term 일치 → rate_kind(avg > base > min) 우선순위로 옵션 선택."""
    candidates = list(product.options)
    if not candidates:
        return None

    if ctx.credit_band is not None:
        band_matches = [o for o in candidates if o.credit_band == ctx.credit_band]
        if band_matches:
            candidates = band_matches

    if ctx.term_months is not None:
        term_matches = [o for o in candidates if o.term_months == ctx.term_months]
        if term_matches:
            candidates = term_matches

    candidates.sort(key=lambda o: _RATE_KIND_RANK.get(o.rate_kind, 3))
    return candidates[0] if candidates else None


def _anon_label(rank: int, product: ProductSnapshot, ctx: CompareContext) -> str:
    letter = chr(ord("A") + rank - 1) if 1 <= rank <= 26 else str(rank)
    group_name = _GROUP_NAME.get(product.lender_group.value, "기타")
    category_name = _CATEGORY_NAME.get(ctx.category.value, "")
    return f"{letter}{group_name} {category_name}".strip()


def _build_temp_loan(product: ProductSnapshot, option: ProductOption, ctx: CompareContext) -> Loan:
    rate_type = RateType.FIXED
    if option.rate_type in (RateType.FIXED.value, RateType.VARIABLE.value):
        rate_type = RateType(option.rate_type)
    loan_type = _CATEGORY_TO_LOAN_TYPE.get(ctx.category, LoanType.OTHER)
    return Loan(
        id=f"__compare__{product.id}",
        name="",
        loan_type=loan_type,
        principal=ctx.amount,
        balance=ctx.amount,
        annual_rate=option.rate or 0.0,
        rate_type=rate_type,
        repay_method=ctx.repay_method,
        remaining_months=ctx.term_months,
        grace_months=0,
        lender_group=product.lender_group,
    )


def rank(
    products: list[ProductSnapshot],
    ctx: CompareContext,
    params: PolicyParams,
    *,
    current_total_interest: int | None = None,
) -> list[CompareItem]:
    """옵션 금리로 월 납입액/총이자를 계산해 sort_key로 정렬하고 상위 10개를 anon_label과 함께 반환."""
    scored: list[tuple[ProductSnapshot, ProductOption, int, int]] = []

    for product in products:
        option = select_option(product, ctx)
        if option is None or option.rate is None:
            continue
        temp_loan = _build_temp_loan(product, option, ctx)
        sched = build_schedule(temp_loan)
        scored.append((product, option, sched.first_payment, sched.total_interest))

    if ctx.sort_key == SortKey.MONTHLY_PAYMENT:
        sort_key_fn = lambda row: (row[2], row[0].id)
    elif ctx.sort_key == SortKey.RATE:
        sort_key_fn = lambda row: (row[1].rate, row[0].id)
    else:  # TOTAL_COST
        sort_key_fn = lambda row: (row[3], row[0].id)

    scored.sort(key=sort_key_fn)
    top = scored[:10]

    items: list[CompareItem] = []
    for idx, (product, option, monthly_payment, total_interest) in enumerate(top):
        rank_no = idx + 1
        notes: list[str] = []
        if product.rate_semantics == RateSemantics.DISCLOSED_AVG_RATE:
            notes.append("신용점수 구간별 전월 평균금리 기준")
        vs_current = None
        if current_total_interest is not None:
            vs_current = total_interest - current_total_interest  # 음수 = 현재보다 이자 절감 (SPEC: '현재 대비 -1,234,567원')
        items.append(
            CompareItem(
                rank=rank_no,
                anon_label=_anon_label(rank_no, product, ctx),
                product_ref=product.id,
                rate=option.rate,
                rate_kind=option.rate_kind,
                rate_semantics=product.rate_semantics,
                monthly_payment=monthly_payment,
                total_interest=total_interest,
                vs_current_total_interest=vs_current,
                disclosure_url=product.disclosure_url,
                notes=notes,
            )
        )
    return items


def sort_explain(ctx: CompareContext) -> str:
    """정렬 기준을 설명하는 한국어 한 문장."""
    if ctx.sort_key == SortKey.MONTHLY_PAYMENT:
        basis = "월 납입액이 적은 순"
    elif ctx.sort_key == SortKey.RATE:
        basis = "금리가 낮은 순"
    else:
        basis = "총이자가 적은 순"
    return f"대출금액 {ctx.amount:,}원, 기간 {ctx.term_months}개월 기준으로 {basis}으로 정렬했습니다."
