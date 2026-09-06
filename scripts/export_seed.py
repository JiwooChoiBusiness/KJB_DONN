"""브라우저 전용 모드(정적 배포) 시드 내보내기 (SPEC 2.10).

현재 DB(`DONN_DB_PATH`, 기본 `data/donn.db`)에서 각 카테고리의 `products.query()`가
실제로 쓰는 스냅샷(소스별 최신 1개씩, `products._latest_snapshot_ids_per_source_for_category`
참고)만 골라 `snapshots`·`products` 행을 JSON으로 만들고 gzip으로 압축해
`seed/products_seed.json.gz`에 쓴다.

공개 상품 스냅샷(금감원·공공데이터)만 다룬다. 프로필·결정·대화·소비 데이터는 어떤 테이블도
읽지 않으므로 절대 섞이지 않는다.

사용법:
    .venv\\Scripts\\python -m scripts.export_seed
    .venv\\Scripts\\python -m scripts.export_seed --db data/donn.db --out seed/products_seed.json.gz
"""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from app.data import db, products
from app.models import ENGINE_VERSION, ProductCategory

DEFAULT_OUT_PATH = "seed/products_seed.json.gz"


def _seed_snapshot_ids(conn: Connection) -> list[str]:
    """모든 카테고리에 대해 `products.query()`가 실제로 쓰는(소스별 최신) 스냅샷 id 전체."""
    ids: set[str] = set()
    for category in ProductCategory:
        ids.update(products._latest_snapshot_ids_per_source_for_category(conn, category))
    return sorted(ids)


def build_seed_payload(conn: Connection) -> dict[str, Any]:
    """DB 커넥션에서 시드 페이로드(dict)를 만든다.

    scripts/export_seed.py(CLI)와 tests/test_seed.py가 이 함수를 공유한다. `app/data/db.py`의
    snapshots/products 스키마가 바뀌면 여기 컬럼 목록도 함께 바꿔야 한다.
    """
    snapshot_ids = _seed_snapshot_ids(conn)

    snapshots_rows: list[dict[str, Any]] = []
    products_rows: list[dict[str, Any]] = []
    by_source: dict[str, int] = {}
    by_category: dict[str, int] = {}

    if snapshot_ids:
        placeholders = ",".join("?" for _ in snapshot_ids)

        snap_cursor = conn.execute(
            f"SELECT snapshot_id, source, fetched_at, count, note FROM snapshots "
            f"WHERE snapshot_id IN ({placeholders}) ORDER BY snapshot_id",
            snapshot_ids,
        )
        snapshots_rows = [dict(r) for r in snap_cursor.fetchall()]

        prod_cursor = conn.execute(
            f"""
            SELECT id, snapshot_id, source, category, lender_group, company_code, company_name,
                   product_code, product_name, rate_semantics, disclosure_month, disclosure_url, json
            FROM products WHERE snapshot_id IN ({placeholders}) ORDER BY id
            """,
            snapshot_ids,
        )
        products_rows = [dict(r) for r in prod_cursor.fetchall()]

        for row in products_rows:
            by_source[row["source"]] = by_source.get(row["source"], 0) + 1
            by_category[row["category"]] = by_category.get(row["category"], 0) + 1

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "snapshot_count": len(snapshots_rows),
        "product_count": len(products_rows),
        "by_source": by_source,
        "by_category": by_category,
    }
    return {"meta": meta, "snapshots": snapshots_rows, "products": products_rows}


def export_seed(db_path: str | None = None, out_path: str = DEFAULT_OUT_PATH) -> dict[str, Any]:
    """DB에서 시드 페이로드를 만들어 gzip JSON 파일로 쓴다. 파일에 쓴 meta(파일 크기 포함)를 돌려준다."""
    conn = db.get_conn(db_path)
    try:
        payload = build_seed_payload(conn)
    finally:
        conn.close()

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    # mtime=0: 내용이 같으면 항상 같은 바이트가 나오도록(재현성, SPEC 원칙 2).
    with gzip.GzipFile(out, mode="wb", mtime=0) as f:
        f.write(raw)

    meta = dict(payload["meta"])
    meta["file_bytes"] = out.stat().st_size
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="DONN 상품 스냅샷 시드 내보내기 (SPEC 2.10)")
    parser.add_argument("--db", default=None, help="원본 DB 경로 (기본 DONN_DB_PATH 또는 data/donn.db)")
    parser.add_argument("--out", default=DEFAULT_OUT_PATH, help="출력 파일 (기본 seed/products_seed.json.gz)")
    args = parser.parse_args()

    meta = export_seed(args.db, args.out)
    print(f"seed written: {args.out}")
    print(meta)


if __name__ == "__main__":
    main()
