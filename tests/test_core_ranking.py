"""app/core/ranking.py, app/core/hashing.py 테스트.

ProductSnapshot 픽스처는 모두 인라인으로 만든다(DB 사용 안 함).
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from app.core.hashing import canonical_json, fingerprint, result_hash
from app.core.ranking import eligible, rank, select_option, sort_explain
from app.models import (
    CompareContext,
    LenderGroup,
    PolicyParams,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    ProductSource,
    RateSemantics,
    RateType,
    RepayMethod,
    SortKey,
)


def make_product(**overrides) -> ProductSnapshot:
    defaults = dict(
        id="prod-1",
        snapshot_id="snap-1",
        source=ProductSource.FINLIFE,
        category=ProductCategory.CREDIT,
        lender_group=LenderGroup.BANK,
        company_code="C001",
        company_name="1은행",
        product_code="P001",
        product_name="신용대출 상품",
        rate_semantics=RateSemantics.OFFER_RATE,
        options=[ProductOption(rate=6.0, rate_kind="base", term_months=36)],
        disclosure_url="https://finlife.fss.or.kr/finlifeapi/example",
    )
    defaults.update(overrides)
    return ProductSnapshot(**defaults)


def make_ctx(**overrides) -> CompareContext:
    defaults = dict(
        category=ProductCategory.CREDIT,
        amount=10_000_000,
        term_months=36,
        sort_key=SortKey.TOTAL_COST,
        repay_method=RepayMethod.EQUAL_PAYMENT,
        lender_groups=[LenderGroup.BANK, LenderGroup.SAVINGS_BANK, LenderGroup.POLICY],
        user_confirmed=True,
    )
    defaults.update(overrides)
    return CompareContext(**defaults)


EMPTY_PARAMS = PolicyParams(version="test", params={})


# ---------------------------------------------------------------------------
# eligible
# ---------------------------------------------------------------------------


def test_eligible_filters_by_category():
    products = [
        make_product(id="credit-1", category=ProductCategory.CREDIT),
        make_product(id="mortgage-1", category=ProductCategory.MORTGAGE),
    ]
    ctx = make_ctx(category=ProductCategory.CREDIT)
    result = eligible(products, ctx)
    assert [p.id for p in result] == ["credit-1"]


def test_eligible_filters_by_lender_group():
    products = [
        make_product(id="bank-1", lender_group=LenderGroup.BANK),
        make_product(id="card-1", lender_group=LenderGroup.CARD),
    ]
    ctx = make_ctx(lender_groups=[LenderGroup.BANK])
    result = eligible(products, ctx)
    assert [p.id for p in result] == ["bank-1"]


def test_eligible_excludes_company_by_name_or_code():
    products = [
        make_product(id="a", company_name="가나은행", company_code="A01"),
        make_product(id="b", company_name="다라은행", company_code="B01"),
    ]
    ctx = make_ctx(exclude_companies=["가나은행"])
    result = eligible(products, ctx)
    assert [p.id for p in result] == ["b"]


def test_eligible_filters_by_rate_type():
    products = [
        make_product(id="fixed-1", options=[ProductOption(rate=5.0, rate_kind="base", rate_type="fixed")]),
        make_product(id="var-1", options=[ProductOption(rate=5.0, rate_kind="base", rate_type="variable")]),
    ]
    ctx = make_ctx(rate_type=RateType.FIXED)
    result = eligible(products, ctx)
    assert [p.id for p in result] == ["fixed-1"]


def test_eligible_filters_by_max_rate():
    products = [
        make_product(id="cheap", options=[ProductOption(rate=4.5, rate_kind="base")]),
        make_product(id="expensive", options=[ProductOption(rate=9.0, rate_kind="base")]),
    ]
    ctx = make_ctx(max_rate=5.0)
    result = eligible(products, ctx)
    assert [p.id for p in result] == ["cheap"]


def test_eligible_credit_band_allows_avg_fallback():
    products = [
        make_product(
            id="band-match",
            options=[ProductOption(rate=6.0, rate_kind="base", credit_band="4-6등급")],
        ),
        make_product(
            id="avg-fallback",
            options=[ProductOption(rate=7.0, rate_kind="avg")],
        ),
        make_product(
            id="no-match",
            options=[ProductOption(rate=7.0, rate_kind="base", credit_band="1-3등급")],
        ),
    ]
    ctx = make_ctx(credit_band="4-6등급")
    result = eligible(products, ctx)
    assert {p.id for p in result} == {"band-match", "avg-fallback"}


# ---------------------------------------------------------------------------
# select_option
# ---------------------------------------------------------------------------


def test_select_option_prefers_credit_band_match():
    product = make_product(
        options=[
            ProductOption(rate=9.0, rate_kind="base", term_months=36, credit_band="1-3등급"),
            ProductOption(rate=6.0, rate_kind="base", term_months=36, credit_band="4-6등급"),
        ]
    )
    ctx = make_ctx(credit_band="4-6등급", term_months=36)
    opt = select_option(product, ctx)
    assert opt.rate == 6.0
    assert opt.credit_band == "4-6등급"


def test_select_option_prefers_term_match_when_band_absent():
    product = make_product(
        options=[
            ProductOption(rate=5.0, rate_kind="base", term_months=12),
            ProductOption(rate=7.0, rate_kind="base", term_months=36),
        ]
    )
    ctx = make_ctx(term_months=36, credit_band=None)
    opt = select_option(product, ctx)
    assert opt.term_months == 36
    assert opt.rate == 7.0


def test_select_option_rate_kind_priority_avg_over_base_over_min():
    product = make_product(
        options=[
            ProductOption(rate=5.0, rate_kind="min", term_months=36),
            ProductOption(rate=6.0, rate_kind="base", term_months=36),
            ProductOption(rate=7.0, rate_kind="avg", term_months=36),
        ]
    )
    ctx = make_ctx(term_months=36, credit_band=None)
    opt = select_option(product, ctx)
    assert opt.rate_kind == "avg"


def test_select_option_returns_none_when_no_options():
    product = make_product(options=[])
    ctx = make_ctx()
    assert select_option(product, ctx) is None


# ---------------------------------------------------------------------------
# rank / sort_explain
# ---------------------------------------------------------------------------


def _three_products():
    return [
        make_product(
            id="prod-bank",
            lender_group=LenderGroup.BANK,
            rate_semantics=RateSemantics.DISCLOSED_AVG_RATE,
            options=[ProductOption(rate=6.5, rate_kind="avg", term_months=36, credit_band="4-6등급")],
        ),
        make_product(
            id="prod-savings",
            lender_group=LenderGroup.SAVINGS_BANK,
            rate_semantics=RateSemantics.OFFER_RATE,
            options=[ProductOption(rate=9.0, rate_kind="base", term_months=36)],
        ),
        make_product(
            id="prod-policy",
            lender_group=LenderGroup.POLICY,
            rate_semantics=RateSemantics.CURATED,
            options=[ProductOption(rate=5.0, rate_kind="base", term_months=36)],
        ),
    ]


def test_rank_orders_by_total_cost_and_labels_anonymously():
    products = _three_products()
    ctx = make_ctx(sort_key=SortKey.TOTAL_COST)
    items = rank(products, ctx, EMPTY_PARAMS)
    assert [i.product_ref for i in items] == ["prod-policy", "prod-bank", "prod-savings"]
    assert items[0].anon_label == "A정책상품 신용대출"
    assert items[1].anon_label == "B은행 신용대출"
    assert items[2].anon_label == "C저축은행 신용대출"
    assert [i.rank for i in items] == [1, 2, 3]


def test_rank_by_rate_orders_by_option_rate():
    products = _three_products()
    ctx = make_ctx(sort_key=SortKey.RATE)
    items = rank(products, ctx, EMPTY_PARAMS)
    assert [i.rate for i in items] == sorted(i.rate for i in items)
    assert items[0].product_ref == "prod-policy"


def test_rank_marks_disclosed_avg_rate_note():
    products = _three_products()
    ctx = make_ctx()
    items = rank(products, ctx, EMPTY_PARAMS)
    bank_item = next(i for i in items if i.product_ref == "prod-bank")
    assert "전월 평균금리" in bank_item.notes[0]
    other_item = next(i for i in items if i.product_ref == "prod-policy")
    assert other_item.notes == []


def test_rank_excludes_zero_and_none_rate_options():
    """금리가 0 이하이거나 미공시(None)인 옵션은 순위에서 제외한다(금리 미공시 취급)."""
    products = [
        make_product(id="zero-rate", options=[ProductOption(rate=0.0, rate_kind="base", term_months=36)]),
        make_product(id="none-rate", options=[ProductOption(rate=None, rate_kind="base", term_months=36)]),
        make_product(id="negative-rate", options=[ProductOption(rate=-1.0, rate_kind="base", term_months=36)]),
        make_product(id="normal", options=[ProductOption(rate=5.0, rate_kind="base", term_months=36)]),
    ]
    ctx = make_ctx()
    items = rank(products, ctx, EMPTY_PARAMS)
    assert [i.product_ref for i in items] == ["normal"]


def test_rank_drops_selected_option_over_max_rate_or_wrong_rate_type():
    """select_option은 credit_band 일치를 최우선으로 고르므로, 그 결과가 max_rate를
    넘거나 rate_type이 다르면 (다른 옵션으로 몰래 바꾸지 않고) 상품 자체를 제외해야 한다."""
    products = [
        make_product(
            id="band-over-cap",
            options=[
                ProductOption(rate=9.0, rate_kind="base", term_months=36, credit_band="4-6등급", rate_type="fixed"),
                ProductOption(rate=4.0, rate_kind="base", term_months=36, rate_type="fixed"),
            ],
        ),
        make_product(
            id="band-ok",
            options=[
                ProductOption(rate=4.5, rate_kind="base", term_months=36, credit_band="4-6등급", rate_type="fixed"),
            ],
        ),
        make_product(
            id="wrong-rate-type",
            options=[
                ProductOption(rate=4.0, rate_kind="base", term_months=36, credit_band="4-6등급", rate_type="variable"),
            ],
        ),
    ]
    ctx = make_ctx(credit_band="4-6등급", max_rate=5.0, rate_type=RateType.FIXED)
    items = rank(products, ctx, EMPTY_PARAMS)
    assert [i.product_ref for i in items] == ["band-ok"]


def test_rank_vs_current_total_interest():
    products = [make_product(id="only", options=[ProductOption(rate=5.0, rate_kind="base", term_months=36)])]
    ctx = make_ctx()
    items = rank(products, ctx, EMPTY_PARAMS, current_total_interest=2_000_000)
    assert items[0].vs_current_total_interest == items[0].total_interest - 2_000_000


def test_rank_limits_to_top_10():
    products = [
        make_product(
            id=f"prod-{i:02d}",
            lender_group=LenderGroup.BANK,
            options=[ProductOption(rate=5.0 + i * 0.1, rate_kind="base", term_months=36)],
        )
        for i in range(15)
    ]
    ctx = make_ctx(lender_groups=[LenderGroup.BANK])
    items = rank(products, ctx, EMPTY_PARAMS)
    assert len(items) == 10
    assert [i.rank for i in items] == list(range(1, 11))


def test_rank_ties_broken_by_product_id():
    products = [
        make_product(id="zzz", lender_group=LenderGroup.BANK, options=[ProductOption(rate=5.0, rate_kind="base", term_months=36)]),
        make_product(id="aaa", lender_group=LenderGroup.BANK, options=[ProductOption(rate=5.0, rate_kind="base", term_months=36)]),
    ]
    ctx = make_ctx(lender_groups=[LenderGroup.BANK])
    items = rank(products, ctx, EMPTY_PARAMS)
    assert [i.product_ref for i in items] == ["aaa", "zzz"]


def test_sort_explain_mentions_amount_and_term():
    ctx = make_ctx(amount=12_345_678, term_months=24, sort_key=SortKey.MONTHLY_PAYMENT)
    text = sort_explain(ctx)
    assert "12,345,678" in text
    assert "24개월" in text
    assert "—" not in text


# ---------------------------------------------------------------------------
# 재현성: 같은 스냅샷으로 두 번 rank -> 동일 결과, 동일 result_hash
# ---------------------------------------------------------------------------


def test_rank_and_result_hash_are_reproducible():
    products = _three_products()
    ctx = make_ctx()
    versions = {"engine_version": "0.1.0", "rules_version": "r1"}

    items1 = rank(products, ctx, EMPTY_PARAMS, current_total_interest=1_000_000)
    items2 = rank(list(reversed(products)), ctx, EMPTY_PARAMS, current_total_interest=1_000_000)

    assert [i.model_dump() for i in items1] == [i.model_dump() for i in items2]

    hash1 = result_hash(items1, ctx, "snap-1", versions)
    hash2 = result_hash(items2, ctx, "snap-1", versions)
    assert hash1 == hash2
    assert isinstance(hash1, str) and len(hash1) == 64  # sha256 hex


def test_result_hash_changes_when_items_differ():
    products = _three_products()
    ctx = make_ctx()
    versions = {"engine_version": "0.1.0"}
    items = rank(products, ctx, EMPTY_PARAMS)
    h1 = result_hash(items, ctx, "snap-1", versions)
    h2 = result_hash(items[:-1], ctx, "snap-1", versions)
    assert h1 != h2


# ---------------------------------------------------------------------------
# hashing.canonical_json
# ---------------------------------------------------------------------------


def test_canonical_json_sorts_keys_deterministically():
    obj_a = {"b": 1, "a": 2, "c": {"z": 1, "y": 2}}
    obj_b = {"c": {"y": 2, "z": 1}, "a": 2, "b": 1}
    assert canonical_json(obj_a) == canonical_json(obj_b)
    assert canonical_json(obj_a).startswith('{"a":2,"b":1,"c":')


def test_canonical_json_serializes_enum_date_datetime_stably():
    payload = {
        "category": ProductCategory.CREDIT,
        "lender": LenderGroup.BANK,
        "day": date(2026, 9, 6),
        "when": datetime(2026, 9, 6, 12, 30, 0),
    }
    text = canonical_json(payload)
    assert '"category":"credit"' in text
    assert '"lender":"bank"' in text
    assert '"2026-09-06"' in text
    assert canonical_json(payload) == text  # 반복 호출해도 동일


def test_canonical_json_handles_pydantic_model():
    ctx = make_ctx()
    text1 = canonical_json(ctx)
    text2 = canonical_json(ctx)
    assert text1 == text2
    assert '"category":"credit"' in text1


def test_fingerprint_is_sha256_hex_and_deterministic():
    obj = {"a": 1, "b": [1, 2, 3]}
    f1 = fingerprint(obj)
    f2 = fingerprint(obj)
    assert f1 == f2
    assert len(f1) == 64
    int(f1, 16)  # 유효한 hex 문자열이어야 한다
