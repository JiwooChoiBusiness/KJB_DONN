"""브라우저 전용 모드 시드 내보내기/적재 왕복 테스트 (SPEC 2.10).

scripts.export_seed(export 로직)과 app.data.products.import_seed/ensure_seed_loaded가
서로 맞물려 동작하는지 확인한다. 실제 data/donn.db는 건드리지 않고 전부 tmp_path 임시
DB/파일로만 검증한다.
"""
from __future__ import annotations

# tests.test_api를 다른 어떤 app.* 임포트보다도 먼저 임포트한다(값 자체는 안 쓴다).
# tests/test_api.py는 자신이 모듈 임포트되는 시점에 DONN_DB_PATH/GEMINI_* 환경변수를
# 임시값으로 먼저 설정한 뒤에 app.data.db 등을 임포트하는데, app.data.db.DB_PATH는
# "최초 임포트 시점"에 한 번만 평가되는 모듈 전역이라 그 순서가 세션 전체에 고정된다.
# 이 파일이 (pytest 커맨드라인 순서상) app.data.db를 먼저 건드려버리면 나중에 임포트되는
# tests/test_api.py가 실 data/donn.db를 보게 되어 그 파일의 격리가 깨진다. 이 줄이 그
# 경쟁을 항상 tests.test_api가 이기도록 만든다(tests/test_chatlog.py 등과 같은 패턴).
import tests.test_api  # noqa: F401,E402

from app.data import db, products  # noqa: E402
from app.models import (
    LenderGroup,
    ProductCategory,
    ProductOption,
    ProductSnapshot,
    ProductSource,
    RateSemantics,
)
from scripts import export_seed as export_seed_module

_SNAPSHOT_ID = "20260906-0000-testseed"


def _snapshot(idx: int, category: ProductCategory, lender_group: LenderGroup, rate: float) -> ProductSnapshot:
    return ProductSnapshot(
        id=f"{_SNAPSHOT_ID}:{category.value}:{idx}",
        snapshot_id=_SNAPSHOT_ID,
        source=ProductSource.FINLIFE,
        category=category,
        lender_group=lender_group,
        company_code=f"C{idx}",
        company_name=f"테스트회사{idx}",
        product_code=f"P{idx}",
        product_name=f"테스트상품{idx}",
        rate_semantics=RateSemantics.OFFER_RATE,
        options=[ProductOption(rate=rate, rate_kind="base", term_months=36, rate_type="fixed")],
        disclosure_month="202608",
        disclosure_url="https://finlife.fss.or.kr/",
    )


_FIXTURES = [
    _snapshot(1, ProductCategory.CREDIT, LenderGroup.BANK, 5.5),
    _snapshot(2, ProductCategory.CREDIT, LenderGroup.SAVINGS_BANK, 8.0),
    _snapshot(3, ProductCategory.DEPOSIT, LenderGroup.BANK, 3.0),
]


def _seed_source_db(dbfile) -> None:
    conn = db.init_db(db.get_conn(str(dbfile)))
    try:
        products._save_snapshot(
            conn, _SNAPSHOT_ID, "finlife", "2026-09-06T00:00:00", _FIXTURES, note="test seed source"
        )
    finally:
        conn.close()


def _raw_json_by_id(dbfile) -> dict[str, str]:
    conn = db.get_conn(str(dbfile))
    try:
        rows = conn.execute("SELECT id, json FROM products").fetchall()
        return {r["id"]: r["json"] for r in rows}
    finally:
        conn.close()


def test_export_seed_payload_has_expected_meta_and_no_other_tables(tmp_path):
    src_db = tmp_path / "source.db"
    _seed_source_db(src_db)

    conn = db.get_conn(str(src_db))
    try:
        payload = export_seed_module.build_seed_payload(conn)
    finally:
        conn.close()

    # 프로필·결정·대화·소비 데이터는 이 경로에 등장할 여지 자체가 없다(snapshots/products만).
    assert set(payload.keys()) == {"meta", "snapshots", "products"}
    meta = payload["meta"]
    assert meta["snapshot_count"] == 1
    assert meta["product_count"] == len(_FIXTURES)
    assert meta["by_category"] == {"credit": 2, "deposit": 1}
    assert meta["by_source"] == {"finlife": 3}
    assert meta["engine_version"]
    assert meta["generated_at"]


def test_export_then_import_round_trip_preserves_id_and_json(tmp_path, monkeypatch):
    src_db = tmp_path / "source.db"
    seed_gz = tmp_path / "products_seed.json.gz"
    dest_db = tmp_path / "dest.db"

    _seed_source_db(src_db)
    original_json_by_id = _raw_json_by_id(src_db)

    meta = export_seed_module.export_seed(str(src_db), str(seed_gz))
    assert meta["product_count"] == len(_FIXTURES)
    assert meta["file_bytes"] > 0
    assert seed_gz.exists()

    # 목적지는 완전히 빈 DB(스키마만 있음)에서 시작한다.
    monkeypatch.setattr(db, "DB_PATH", str(dest_db))
    db.init_db(db.get_conn())
    assert products.stats()["total_products"] == 0

    result = products.import_seed(str(seed_gz))
    assert result == {"snapshots": 1, "products": len(_FIXTURES)}

    # products.query가 같은 id로 같은 상품을 돌려준다.
    credit_items = products.query(ProductCategory.CREDIT)
    expected_credit_ids = {s.id for s in _FIXTURES if s.category == ProductCategory.CREDIT}
    assert {p.id for p in credit_items} == expected_credit_ids

    deposit_items = products.query(ProductCategory.DEPOSIT)
    assert [p.id for p in deposit_items] == [
        s.id for s in _FIXTURES if s.category == ProductCategory.DEPOSIT
    ]

    # DB에 저장된 원문 json 문자열까지 바이트 단위로 동일해야 한다("같은 json").
    dest_json_by_id = _raw_json_by_id(dest_db)
    for snap_id, original_json in original_json_by_id.items():
        assert dest_json_by_id[snap_id] == original_json


def test_import_seed_is_idempotent_insert_or_ignore(tmp_path, monkeypatch):
    src_db = tmp_path / "source.db"
    seed_gz = tmp_path / "products_seed.json.gz"
    dest_db = tmp_path / "dest.db"
    _seed_source_db(src_db)
    export_seed_module.export_seed(str(src_db), str(seed_gz))

    monkeypatch.setattr(db, "DB_PATH", str(dest_db))
    db.init_db(db.get_conn())

    first = products.import_seed(str(seed_gz))
    assert first == {"snapshots": 1, "products": len(_FIXTURES)}

    second = products.import_seed(str(seed_gz))
    assert second == {"snapshots": 0, "products": 0}  # 이미 있는 행은 INSERT OR IGNORE로 건너뜀
    assert products.stats()["total_products"] == len(_FIXTURES)


def test_ensure_seed_loaded_only_when_products_table_empty(tmp_path, monkeypatch):
    src_db = tmp_path / "source.db"
    seed_gz = tmp_path / "products_seed.json.gz"
    dest_db = tmp_path / "dest.db"
    _seed_source_db(src_db)
    export_seed_module.export_seed(str(src_db), str(seed_gz))

    monkeypatch.setattr(db, "DB_PATH", str(dest_db))
    db.init_db(db.get_conn())

    assert products.ensure_seed_loaded(str(seed_gz)) is True
    assert products.stats()["total_products"] == len(_FIXTURES)

    # 이미 상품이 있는 DB에서는 다시 부르면 아무 것도 하지 않는다(픽스처를 먼저 넣는
    # tests/test_api.py가 이 동작에 의존한다: 시드가 픽스처를 절대 덮어쓰지 않는다).
    assert products.ensure_seed_loaded(str(seed_gz)) is False
    assert products.stats()["total_products"] == len(_FIXTURES)


def test_ensure_seed_loaded_missing_file_returns_false_and_loads_nothing(tmp_path, monkeypatch):
    dest_db = tmp_path / "dest.db"
    monkeypatch.setattr(db, "DB_PATH", str(dest_db))
    db.init_db(db.get_conn())

    assert products.ensure_seed_loaded(str(tmp_path / "no_such_seed.json.gz")) is False
    assert products.stats()["total_products"] == 0
