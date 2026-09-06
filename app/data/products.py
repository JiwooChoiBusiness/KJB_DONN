"""상품 스냅샷 적재/조회. finlife.py, datago.py가 만든 ProductSnapshot을 db.py 스키마에
저장하고, 화면/서비스 계층에는 저장된 JSON에서 다시 만든 ProductSnapshot만 돌려준다.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime
from sqlite3 import Connection
from typing import Any

from dotenv import load_dotenv

from app.data import db
from app.data.datago import (
    DataGoClient,
    normalize_didimdol,
    normalize_fsc_small_loan,
    normalize_kinfa_loan_products,
)
from app.data.finlife import FINLIFE_SERVICES, FinlifeClient, normalize_finlife
from app.models import ProductCategory, ProductSnapshot

load_dotenv()


def _save_snapshot(
    conn: Connection,
    snapshot_id: str,
    source: str,
    fetched_at: str,
    snapshots: list[ProductSnapshot],
    note: str = "",
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO snapshots(snapshot_id, source, fetched_at, count, note) "
        "VALUES (?,?,?,?,?)",
        (snapshot_id, source, fetched_at, len(snapshots), note),
    )
    for snap in snapshots:
        conn.execute(
            """
            INSERT OR REPLACE INTO products(
                id, snapshot_id, source, category, lender_group, company_code, company_name,
                product_code, product_name, rate_semantics, disclosure_month, disclosure_url, json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snap.id,
                snap.snapshot_id,
                snap.source.value,
                snap.category.value,
                snap.lender_group.value,
                snap.company_code,
                snap.company_name,
                snap.product_code,
                snap.product_name,
                snap.rate_semantics.value,
                snap.disclosure_month,
                snap.disclosure_url,
                snap.model_dump_json(),
            ),
        )
    conn.commit()


def load_snapshot(sources: list[str], groups: list[str]) -> str:
    """지정한 소스에서 상품을 내려받아 정규화하고 DB에 저장한다.

    sources: "finlife" | "datago" (둘 다 넣으면 순서대로 각각 자기 snapshot_id로 처리한다).
    groups: finlife topFinGrpNo 목록(datago 처리에는 쓰이지 않지만 시그니처상 항상 받는다).
    반환값은 마지막으로 처리한 소스의 snapshot_id (형식 YYYYMMDD-HHMM-<source>).
    """
    conn = db.init_db(db.get_conn())
    now = datetime.now()
    ts = now.strftime("%Y%m%d-%H%M")
    fetched_at = now.isoformat(timespec="seconds")

    last_snapshot_id: str | None = None
    try:
        for source in sources:
            if source == "finlife":
                snapshot_id = f"{ts}-finlife"
                auth_key = os.environ.get("FINLIFE_AUTH_KEY", "")
                client = FinlifeClient(auth_key)
                snaps: list[ProductSnapshot] = []
                for group in groups:
                    for service in FINLIFE_SERVICES:
                        base_rows, option_rows = client.fetch_all(service, group)
                        if not base_rows:
                            continue
                        snaps.extend(normalize_finlife(service, group, base_rows, option_rows, snapshot_id))
                _save_snapshot(conn, snapshot_id, "finlife", fetched_at, snaps, note=f"groups={','.join(groups)}")
                last_snapshot_id = snapshot_id

            elif source == "datago":
                snapshot_id = f"{ts}-datago"
                service_key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "")
                client = DataGoClient(service_key)
                snaps = []
                snaps.extend(normalize_didimdol(client.fetch_didimdol(), snapshot_id))
                snaps.extend(normalize_fsc_small_loan(client.fetch_fsc_small_loan(), snapshot_id))
                snaps.extend(normalize_kinfa_loan_products(client.fetch_kinfa_loan_products(), snapshot_id))
                _save_snapshot(conn, snapshot_id, "datago", fetched_at, snaps, note="didimdol+fsc+kinfa")
                last_snapshot_id = snapshot_id

            else:
                raise ValueError(f"unknown source: {source}")
    finally:
        conn.close()

    if last_snapshot_id is None:
        raise ValueError("no sources processed")
    return last_snapshot_id


def _latest_snapshot_id(conn: Connection) -> str | None:
    row = conn.execute(
        "SELECT snapshot_id FROM snapshots ORDER BY fetched_at DESC LIMIT 1"
    ).fetchone()
    return row["snapshot_id"] if row else None


def _latest_snapshot_ids_per_source_for_category(conn: Connection, category: ProductCategory) -> list[str]:
    """이 category를 담고 있는 스냅샷들을 source별로 묶어, source마다 가장 최근 1개씩의
    snapshot_id를 돌려준다.

    finlife와 datago는 서로 다른 시각에 독립적으로 적재된다(SPEC 5의 두 개 별도 명령).
    단순히 "이 category를 담은 가장 최근 스냅샷 1개"만 쓰면, 두 소스가 같은 category를
    함께 채우는 경우(예: MORTGAGE = finlife 은행 주담대 + datago 디딤돌) 나중에 적재된
    소스가 먼저 적재된 소스의 데이터를 조회 결과에서 통째로 가려버린다. source별 최신을
    모두 모아 합치면 이 문제가 없다.
    """
    rows = conn.execute(
        """
        SELECT DISTINCT p.snapshot_id, s.source, s.fetched_at
        FROM products p
        JOIN snapshots s ON s.snapshot_id = p.snapshot_id
        WHERE p.category = ?
        """,
        (category.value,),
    ).fetchall()
    best: dict[str, tuple[str, str]] = {}  # source -> (fetched_at, snapshot_id)
    for r in rows:
        src, sid, fetched_at = r["source"], r["snapshot_id"], r["fetched_at"]
        if src not in best or fetched_at > best[src][0]:
            best[src] = (fetched_at, sid)
    return [sid for _fetched_at, sid in best.values()]


def latest_snapshot_id() -> str | None:
    conn = db.get_conn()
    try:
        return _latest_snapshot_id(conn)
    finally:
        conn.close()


def query(category: ProductCategory, snapshot_id: str | None = None) -> list[ProductSnapshot]:
    """저장된 JSON에서 ProductSnapshot을 다시 만들어 반환한다.

    snapshot_id를 명시하면 그 스냅샷 안에서만 찾는다. 생략하면 이 category를 담은 각
    source(finlife/datago 등)의 가장 최근 스냅샷을 모두 모아 합친 결과를 반환한다 (두
    소스가 같은 category를 채우는 경우에도 어느 한쪽이 가려지지 않도록). 그마저 없으면
    DB 전체 최신 스냅샷으로, 그래도 없으면 빈 리스트를 반환한다.
    """
    conn = db.get_conn()
    try:
        if snapshot_id is not None:
            sids = [snapshot_id]
        else:
            sids = _latest_snapshot_ids_per_source_for_category(conn, category)
            if not sids:
                fallback = _latest_snapshot_id(conn)
                sids = [fallback] if fallback else []
        if not sids:
            return []
        placeholders = ",".join("?" for _ in sids)
        rows = conn.execute(
            f"SELECT json FROM products WHERE category = ? AND snapshot_id IN ({placeholders})",
            (category.value, *sids),
        ).fetchall()
        return [ProductSnapshot.model_validate_json(r["json"]) for r in rows]
    finally:
        conn.close()


def stats() -> dict[str, Any]:
    """스냅샷/카테고리별 적재 현황 요약."""
    conn = db.get_conn()
    try:
        snapshot_rows = conn.execute(
            "SELECT snapshot_id, source, fetched_at, count, note FROM snapshots ORDER BY fetched_at DESC"
        ).fetchall()
        snapshots_info = [dict(r) for r in snapshot_rows]

        cat_rows = conn.execute(
            "SELECT snapshot_id, category, COUNT(*) AS c FROM products GROUP BY snapshot_id, category"
        ).fetchall()
        by_snapshot: dict[str, dict[str, int]] = {}
        for r in cat_rows:
            by_snapshot.setdefault(r["snapshot_id"], {})[r["category"]] = r["c"]

        total = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]

        return {
            "latest_snapshot_id": _latest_snapshot_id(conn),
            "snapshots": snapshots_info,
            "by_category_per_snapshot": by_snapshot,
            "total_products": total,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 브라우저 전용 모드 시드 (SPEC 2.10). scripts/export_seed.py가 내보내는 파일을
# 여기서 다시 읽어들인다. 공개 상품 스냅샷만 다루며 profiles/decisions/session 등
# 다른 테이블은 절대 건드리지 않는다.
# ---------------------------------------------------------------------------

DEFAULT_SEED_PATH = "seed/products_seed.json.gz"


def import_seed(path: str) -> dict[str, int]:
    """gzip으로 압축된 시드 JSON(scripts.export_seed 산출물)을 읽어 snapshots·products에
    INSERT OR IGNORE한다(이미 있는 snapshot_id/product id는 그대로 둔다, 멱등).

    반환값은 실제로 새로 들어간 행 수 `{"snapshots": n, "products": n}`.
    """
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)

    conn = db.init_db(db.get_conn())
    try:
        n_snapshots = 0
        for row in payload.get("snapshots", []):
            cur = conn.execute(
                "INSERT OR IGNORE INTO snapshots(snapshot_id, source, fetched_at, count, note) "
                "VALUES (?,?,?,?,?)",
                (row["snapshot_id"], row["source"], row["fetched_at"], row.get("count", 0), row.get("note", "")),
            )
            if cur.rowcount and cur.rowcount > 0:
                n_snapshots += cur.rowcount

        n_products = 0
        for row in payload.get("products", []):
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO products(
                    id, snapshot_id, source, category, lender_group, company_code, company_name,
                    product_code, product_name, rate_semantics, disclosure_month, disclosure_url, json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    row["id"], row["snapshot_id"], row["source"], row["category"], row["lender_group"],
                    row["company_code"], row["company_name"], row["product_code"], row["product_name"],
                    row["rate_semantics"], row.get("disclosure_month", ""), row.get("disclosure_url", ""),
                    row["json"],
                ),
            )
            if cur.rowcount and cur.rowcount > 0:
                n_products += cur.rowcount
        conn.commit()
        return {"snapshots": n_snapshots, "products": n_products}
    finally:
        conn.close()


def ensure_seed_loaded(path: str = DEFAULT_SEED_PATH) -> bool:
    """products 테이블이 비어 있고 시드 파일이 있을 때만 `import_seed`를 실행한다.

    이미 상품이 있으면(실 적재를 했거나 이전에 시드를 넣었으면) 아무 것도 하지 않고
    False를 돌려준다. 실제로 적재했으면 True.
    """
    if not os.path.exists(path):
        return False
    conn = db.init_db(db.get_conn())
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM products").fetchone()["c"]
    finally:
        conn.close()
    if count > 0:
        return False
    import_seed(path)
    return True
