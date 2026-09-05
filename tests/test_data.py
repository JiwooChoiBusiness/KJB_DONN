"""app/data 오프라인 테스트.

네트워크가 필요한 테스트는 환경변수 DONN_NETWORK_TESTS=1일 때만 실행한다(기본은 스킵).
정규화 테스트는 실제 finlife/data.go.kr 응답에서 확인한 필드 구조를 그대로 본뜬 작은
픽스처를 쓴다(2026-09-06 실 호출로 확인).
"""
from __future__ import annotations

import os
from datetime import date

import pytest

from app.data import db, policy, products, synthetic
from app.data.datago import (
    DataGoClient,
    normalize_didimdol,
    normalize_fsc_small_loan,
    normalize_kinfa_loan_products,
)
from app.data.finlife import CRDT_GRADE_LABELS, FinlifeClient, normalize_finlife
from app.models import LenderGroup, ProductCategory, RateSemantics

NETWORK = os.environ.get("DONN_NETWORK_TESTS") == "1"
skip_no_network = pytest.mark.skipif(not NETWORK, reason="DONN_NETWORK_TESTS=1 아니면 스킵")


# ---------------------------------------------------------------------------
# db
# ---------------------------------------------------------------------------

def test_db_init_creates_all_tables(tmp_path):
    dbfile = tmp_path / "t.db"
    conn = db.get_conn(str(dbfile))
    db.init_db(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"snapshots", "products", "profiles", "decisions", "session"} <= tables
    conn.close()


# ---------------------------------------------------------------------------
# finlife 정규화 (실 응답 구조를 본뜬 픽스처)
# ---------------------------------------------------------------------------

_DEPOSIT_BASE = [{
    "dcls_month": "202608", "fin_co_no": "0010001", "fin_prdt_cd": "TESTD01",
    "kor_co_nm": "테스트은행", "fin_prdt_nm": "테스트 정기예금", "join_way": "영업점,인터넷",
    "join_deny": "1", "join_member": "실명의 개인", "max_limit": 100_000_000,
    "dcls_strt_day": "20260801", "dcls_end_day": None, "fin_co_subm_day": "202608010900",
}]
_DEPOSIT_OPT = [
    {"fin_co_no": "0010001", "fin_prdt_cd": "TESTD01", "intr_rate_type": "S",
     "intr_rate_type_nm": "단리", "save_trm": "12", "intr_rate": 3.1, "intr_rate2": 3.4},
    {"fin_co_no": "0010001", "fin_prdt_cd": "TESTD01", "intr_rate_type": "S",
     "intr_rate_type_nm": "단리", "save_trm": "6", "intr_rate": 2.8, "intr_rate2": 2.8},
]


def test_normalize_finlife_deposit():
    snaps = normalize_finlife(
        "depositProductsSearch", "020000", _DEPOSIT_BASE, _DEPOSIT_OPT, "20260906-0000-finlife"
    )
    assert len(snaps) == 1
    s = snaps[0]
    assert s.category == ProductCategory.DEPOSIT
    assert s.lender_group == LenderGroup.BANK
    assert s.rate_semantics == RateSemantics.OFFER_RATE
    assert s.company_name == "테스트은행"
    assert s.max_amount == 100_000_000
    rates = sorted(o.rate for o in s.options if o.rate is not None)
    # 12개월 base(3.1)+preferential(3.4), 6개월 base(2.8, preferential과 동일해 1개만)
    assert rates == [2.8, 3.1, 3.4]


_CREDIT_BASE = [{
    "dcls_month": "202608", "fin_co_no": "0010001", "fin_prdt_cd": "TESTC01",
    "crdt_prdt_type": "1", "kor_co_nm": "테스트은행", "fin_prdt_nm": "테스트 신용대출",
    "join_way": "인터넷", "cb_name": "KCB", "crdt_prdt_type_nm": "일반신용대출",
    "dcls_strt_day": "20260801", "dcls_end_day": None, "fin_co_subm_day": "202608010900",
}]
_CREDIT_OPT = [
    {"fin_co_no": "0010001", "fin_prdt_cd": "TESTC01", "crdt_prdt_type": "1", "crdt_lend_rate_type": "A",
     "crdt_lend_rate_type_nm": "대출금리", "crdt_grad_5": 6.5, "crdt_grad_6": 7.2, "crdt_grad_avg": 6.9},
    {"fin_co_no": "0010001", "fin_prdt_cd": "TESTC01", "crdt_prdt_type": "1", "crdt_lend_rate_type": "B",
     "crdt_lend_rate_type_nm": "기준금리", "crdt_grad_5": 3.5, "crdt_grad_6": 3.5, "crdt_grad_avg": 3.5},
]


def test_normalize_finlife_credit_uses_disclosed_rate_only():
    snaps = normalize_finlife(
        "creditLoanProductsSearch", "030300", _CREDIT_BASE, _CREDIT_OPT, "20260906-0000-finlife"
    )
    assert len(snaps) == 1
    s = snaps[0]
    assert s.lender_group == LenderGroup.SAVINGS_BANK
    assert s.rate_semantics == RateSemantics.DISCLOSED_AVG_RATE
    bands = {o.credit_band for o in s.options if o.credit_band}
    assert CRDT_GRADE_LABELS["crdt_grad_5"] in bands
    assert CRDT_GRADE_LABELS["crdt_grad_6"] in bands
    rates = {o.rate for o in s.options}
    assert {6.5, 7.2, 6.9} <= rates
    assert 3.5 not in rates  # 기준금리(B) 행은 제외되어야 함


# ---------------------------------------------------------------------------
# datago 정규화 (실 응답 구조를 본뜬 픽스처)
# ---------------------------------------------------------------------------

def test_normalize_didimdol():
    rows = [{
        "interest_10y_2000": "2.85", "interest_10y_4000": "3.2", "interest_10y_6000": "3.55",
        "interest_15y_2000": "2.95", "interest_20y_2000": "3.05", "interest_30y_2000": "3.1",
        "applyDy": "20260906",
    }]
    snaps = normalize_didimdol(rows, "20260906-0000-datago")
    assert len(snaps) == 1
    s = snaps[0]
    assert s.category == ProductCategory.MORTGAGE
    assert s.lender_group == LenderGroup.POLICY
    assert s.disclosure_month == "202609"
    term_months = sorted({o.term_months for o in s.options})
    assert term_months == [120, 180, 240, 360]
    # SEV4 #10: 공시 링크는 data.go.kr 개발자 문서가 아니라 한국주택금융공사(HF) 안내 페이지
    assert s.disclosure_url == "https://www.hf.go.kr"


def test_normalize_fsc_small_loan_rate_parsing():
    rows = [
        {
            "basYm": "202608", "snq": "1", "finPrdNm": "테스트 서민대출", "lnLmt": "3,000만원",
            "irtCtg": "변동금리", "irt": "6.0~8.0%", "rdptMthd": "원리금균등분할상환", "usge": "생계",
            "trgt": "근로자", "instCtg": "시중은행", "ofrInstNm": "테스트은행", "hdlInst": "테스트은행",
            "suprTgtDtlCond": "연소득 4천만원 이하",
        },
        {
            "basYm": "202608", "snq": "2", "finPrdNm": "테스트 서민대출2", "lnLmt": "은행별 상이",
            "irtCtg": None, "irt": "은행별 상이(10.5이하)", "rdptMthd": "만기일시상환", "usge": "생계",
            "trgt": "근로자", "instCtg": "시중은행", "ofrInstNm": "테스트은행2", "hdlInst": "테스트은행2",
            "suprTgtDtlCond": "-",
        },
    ]
    snaps = normalize_fsc_small_loan(rows, "20260906-0000-datago")
    assert len(snaps) == 2
    a, b = snaps
    assert a.category == ProductCategory.POLICY and a.lender_group == LenderGroup.POLICY
    assert a.rate_semantics == RateSemantics.CURATED
    assert a.options[0].rate == pytest.approx(7.0)  # (6.0+8.0)/2
    assert a.max_amount == 30_000_000
    # 애매한 텍스트는 rate=None으로 남고 원문은 note에 보존한다(임의 추정 금지)
    assert b.options[0].rate is None
    assert "10.5" in b.options[0].note
    # SEV4 #10: 공시 링크는 data.go.kr 개발자 문서가 아니라 서민금융진흥원(KINFA) 안내 페이지
    assert a.disclosure_url == "https://www.kinfa.or.kr"
    assert b.disclosure_url == "https://www.kinfa.or.kr"


def test_normalize_kinfa_loan_products_disclosure_url():
    """SEV4 #10: 대출상품한눈에(KINFA)도 서민금융진흥원 안내 페이지를 공시 링크로 쓴다."""
    rows = [{
        "basym": "202608", "seq": "1", "finprdnm": "테스트 대출상품", "lnlmt": "2000",
        "irtctg": "고정금리", "irt": "5.0", "rdptmthd": "원리금균등분할상환", "usge": "생계",
        "trgt": "근로자", "hdlinst": "테스트은행", "suprtgtdtlcond": "-", "maxrdpttrm": "10",
    }]
    snaps = normalize_kinfa_loan_products(rows, "20260906-0000-datago")
    assert len(snaps) == 1
    assert snaps[0].disclosure_url == "https://www.kinfa.or.kr"
    assert snaps[0].category == ProductCategory.POLICY


# ---------------------------------------------------------------------------
# policy
# ---------------------------------------------------------------------------

def test_load_policy_params_seed_keys():
    params = policy.load_policy_params()
    expected_keys = {
        "dsr_limit_bank_pct", "dsr_limit_nonbank_pct", "stress_dsr_rate_metro_pct",
        "stress_dsr_rate_other_pct", "credit_loan_dsr_assumed_term_months",
        "prepay_fee_period_months", "interest_income_tax_pct", "rate_cut_request_min_rate",
        "refi_rate_gap_min_pct", "stress_variable_rate_add_pct", "emergency_fund_months",
    }
    assert expected_keys <= set(params.params.keys())
    assert params.value("dsr_limit_bank_pct") == 40
    assert params.params["dsr_limit_bank_pct"].needs_verification is True
    assert params.value("emergency_fund_months") == 1
    assert params.params["rate_cut_request_min_rate"].needs_verification is False


def test_load_policy_params_missing_source_forces_verification(tmp_path):
    yaml_text = (
        'version: "0.0.1"\n'
        "params:\n"
        "  test_key:\n"
        "    value: 1\n"
        "    unit: pct\n"
        "    source_url: \"\"\n"
        "    source_title: \"\"\n"
        "    needs_verification: true\n"
        "    note: \"임시\"\n"
    )
    p = tmp_path / "policy.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    params = policy.load_policy_params(str(p))
    assert params.value("test_key") == 1
    assert params.params["test_key"].needs_verification is True


# ---------------------------------------------------------------------------
# synthetic
# ---------------------------------------------------------------------------

def test_personas_ids_and_shape():
    ids = [p.id for p in synthetic.PERSONAS]
    assert ids == [f"P{i}" for i in range(1, 9)]
    for p in synthetic.PERSONAS:
        assert p.monthly_income > 0
        assert len(p.loans) >= 1
        for loan in p.loans:
            assert loan.balance >= 0
            assert loan.remaining_months >= 1


def test_generate_transactions_deterministic():
    rows1 = synthetic.generate_transactions("P1", months=2, seed=42, end=date(2026, 9, 6))
    rows2 = synthetic.generate_transactions("P1", months=2, seed=42, end=date(2026, 9, 6))
    assert [r.model_dump() for r in rows1] == [r.model_dump() for r in rows2]
    assert len(rows1) > 0
    categories = {r.category for r in rows1}
    assert "대출상환" in categories


def test_generate_transactions_varies_by_seed():
    rows1 = synthetic.generate_transactions("P3", months=2, seed=1, end=date(2026, 9, 6))
    rows2 = synthetic.generate_transactions("P3", months=2, seed=2, end=date(2026, 9, 6))
    assert [r.amount for r in rows1] != [r.amount for r in rows2]


def test_generate_transactions_unknown_persona_raises():
    with pytest.raises(ValueError):
        synthetic.generate_transactions("P99", months=1, seed=1, end=date(2026, 9, 6))


def test_transactions_to_csv_roundtrip():
    rows = synthetic.generate_transactions("P2", months=1, seed=7, end=date(2026, 9, 6))
    csv_text = synthetic.transactions_to_csv(rows)
    lines = csv_text.strip("\n").splitlines()
    assert lines[0].split(",") == ["id", "date", "amount", "merchant", "category", "kind", "memo"]
    assert len(lines) == len(rows) + 1


# ---------------------------------------------------------------------------
# products (DB round-trip, 네트워크 없이 정규화된 픽스처만 사용)
# ---------------------------------------------------------------------------

def test_products_save_query_and_stats(tmp_path, monkeypatch):
    dbfile = tmp_path / "products.db"
    monkeypatch.setattr(db, "DB_PATH", str(dbfile))

    conn = db.init_db(db.get_conn())
    snaps = normalize_finlife(
        "depositProductsSearch", "020000", _DEPOSIT_BASE, _DEPOSIT_OPT, "20260906-0000-finlife"
    )
    products._save_snapshot(
        conn, "20260906-0000-finlife", "finlife", "2026-09-06T00:00:00", snaps, note="test"
    )
    conn.close()

    # snapshot_id 명시
    result = products.query(ProductCategory.DEPOSIT, snapshot_id="20260906-0000-finlife")
    assert len(result) == 1
    assert result[0].product_name == "테스트 정기예금"

    # snapshot_id 생략 -> 해당 카테고리를 담은 최신 스냅샷으로 자동 대체
    result_default = products.query(ProductCategory.DEPOSIT)
    assert len(result_default) == 1

    # 없는 카테고리는 빈 리스트
    assert products.query(ProductCategory.CREDIT) == []

    info = products.stats()
    assert info["total_products"] == 1
    assert info["latest_snapshot_id"] == "20260906-0000-finlife"
    assert info["by_category_per_snapshot"]["20260906-0000-finlife"]["deposit"] == 1


def test_products_query_merges_latest_per_source_across_categories(tmp_path, monkeypatch):
    """finlife(주담대)와 datago(디딤돌)처럼 서로 다른 소스가 같은 category를 채우면,
    나중에 적재된 소스가 먼저 적재된 소스를 가리지 않고 둘 다 조회돼야 한다.
    """
    dbfile = tmp_path / "products.db"
    monkeypatch.setattr(db, "DB_PATH", str(dbfile))
    conn = db.init_db(db.get_conn())

    mortgage_base = [{
        "dcls_month": "202608", "fin_co_no": "0010001", "fin_prdt_cd": "M01",
        "kor_co_nm": "테스트은행", "fin_prdt_nm": "테스트 주담대", "join_way": "영업점",
        "loan_lmt": "LTV 70%", "dcls_strt_day": "20260801", "dcls_end_day": None,
        "fin_co_subm_day": "202608010900",
    }]
    mortgage_opt = [{
        "fin_co_no": "0010001", "fin_prdt_cd": "M01", "rpay_type": "D",
        "rpay_type_nm": "분할상환방식", "lend_rate_type": "C", "lend_rate_type_nm": "변동금리",
        "lend_rate_min": 4.0, "lend_rate_max": 5.0, "lend_rate_avg": 4.3,
    }]
    finlife_snaps = normalize_finlife(
        "mortgageLoanProductsSearch", "020000", mortgage_base, mortgage_opt, "20260906-0000-finlife"
    )
    products._save_snapshot(
        conn, "20260906-0000-finlife", "finlife", "2026-09-06T00:00:00", finlife_snaps
    )

    didimdol_rows = [{"interest_10y_2000": "2.85", "applyDy": "20260906"}]
    datago_snaps = normalize_didimdol(didimdol_rows, "20260906-0100-datago")
    products._save_snapshot(
        conn, "20260906-0100-datago", "datago", "2026-09-06T01:00:00", datago_snaps
    )
    conn.close()

    result = products.query(ProductCategory.MORTGAGE)
    sources = {r.source.value for r in result}
    assert len(result) == 2
    assert sources == {"finlife", "datago_hf"}


# ---------------------------------------------------------------------------
# 네트워크 테스트 (DONN_NETWORK_TESTS=1일 때만)
# ---------------------------------------------------------------------------

@skip_no_network
def test_finlife_live_company_search():
    from dotenv import load_dotenv

    load_dotenv()
    auth_key = os.environ.get("FINLIFE_AUTH_KEY", "")
    client = FinlifeClient(auth_key)
    base_rows, _option_rows = client.fetch_all("depositProductsSearch", "020000")
    assert len(base_rows) > 0


@skip_no_network
def test_datago_live_didimdol():
    from dotenv import load_dotenv

    load_dotenv()
    service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
    client = DataGoClient(service_key)
    rows = client.fetch_didimdol()
    assert len(rows) >= 1
