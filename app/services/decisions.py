"""결정 기록 저장/조회/재현 (SPEC 2.3, db.decisions 테이블).

`replay`는 compare 결정만 지원한다(SPEC). 저장된 context(스냅샷 id 조합과
CompareContext 필드)와 versions를 그대로 다시 사용해 랭킹을 재계산하고,
그 결과 해시가 저장된 result_hash와 같은지 비교한다.

브라우저별 세션 분리: `decisions.sid` 컬럼(`app.services.session.current_sid()`)에
저장 당시의 sid를 기록한다. `get`/`list_recent`는 현재 sid의 기록만 보이게 하므로,
`replay`와 `app.services.explain.explain_compare`(둘 다 `get`을 거친다)는 다른 sid의
결정에 대해 자연히 404가 된다. 이 컬럼이 생기기 전에 저장된 기존 행은 sid가 NULL인데,
이런 옛 기록은 "local" sid(미들웨어를 거치지 않는 테스트·스크립트의 기본값)에서만 보인다.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from app.core import hashing, ranking
from app.data import db
from app.data import policy as policy_data
from app.data import products
from app.models import CompareContext, DecisionRecord, ProductSnapshot
from app.services import session


def save(record: DecisionRecord) -> None:
    """DecisionRecord를 decisions 테이블에 저장한다(같은 decision_id면 덮어쓴다).
    현재 sid를 함께 기록한다."""
    conn = db.get_conn()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO decisions(
                decision_id, kind, input_fingerprint, result_hash, context_json,
                result_json, versions_json, created_at, sid
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                record.decision_id,
                record.kind,
                record.input_fingerprint,
                record.result_hash,
                json.dumps(record.context, ensure_ascii=False, default=str),
                json.dumps(record.result, ensure_ascii=False, default=str),
                json.dumps(record.versions, ensure_ascii=False),
                record.created_at.isoformat(),
                session.current_sid(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_record(row) -> DecisionRecord:
    return DecisionRecord(
        decision_id=row["decision_id"],
        kind=row["kind"],
        input_fingerprint=row["input_fingerprint"],
        result_hash=row["result_hash"],
        context=json.loads(row["context_json"]),
        result=json.loads(row["result_json"]),
        versions=json.loads(row["versions_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _owned_by_current_sid(row) -> bool:
    """row["sid"]가 NULL이면(마이그레이션 이전 옛 기록) 현재 sid가 "local"일 때만
    보이게 한다. 그 외에는 현재 sid와 정확히 같아야 한다."""
    row_sid = row["sid"] if "sid" in row.keys() else None
    sid = session.current_sid()
    if row_sid is None:
        return sid == "local"
    return row_sid == sid


def get(decision_id: str) -> Optional[DecisionRecord]:
    """다른 sid의 결정 기록이면 None을 돌려준다(replay·explain_compare가 자연히 404)."""
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM decisions WHERE decision_id = ?", (decision_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None or not _owned_by_current_sid(row):
        return None
    return _row_to_record(row)


def list_recent(limit: int = 20) -> list[DecisionRecord]:
    """현재 sid의 결정 기록만(옛 sid-NULL 기록은 "local"에서만) 최신순으로 돌려준다."""
    sid = session.current_sid()
    conn = db.get_conn()
    try:
        if sid == "local":
            rows = conn.execute(
                "SELECT * FROM decisions WHERE sid = ? OR sid IS NULL "
                "ORDER BY created_at DESC LIMIT ?",
                (sid, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE sid = ? ORDER BY created_at DESC LIMIT ?",
                (sid, limit),
            ).fetchall()
        return [_row_to_record(r) for r in rows]
    finally:
        conn.close()


def replay(decision_id: str) -> dict:
    """저장된 context/snapshot으로 다시 계산해 result_hash가 재현되는지 확인한다.

    반환: {"match": bool, "result_hash": str, "replay_hash": str}
    """
    record = get(decision_id)
    if record is None:
        raise ValueError(f"결정 기록을 찾을 수 없습니다: {decision_id}")
    if record.kind != "compare":
        raise ValueError(f"replay는 compare 결정만 지원합니다(kind={record.kind}).")

    ctx = CompareContext.model_validate(record.context)  # 비공개 키(_current_total_interest)는 무시됨
    current_total_interest = record.context.get("_current_total_interest")
    snapshot_label = record.versions.get("snapshot_id", "") or ""

    snapshot_ids = [s for s in snapshot_label.split("+") if s]
    prods: list[ProductSnapshot] = []
    for sid in snapshot_ids:
        prods.extend(products.query(ctx.category, snapshot_id=sid))

    params = policy_data.load_policy_params()
    eligible_products = ranking.eligible(prods, ctx)
    items = ranking.rank(eligible_products, ctx, params, current_total_interest=current_total_interest)

    replay_hash = hashing.result_hash(items, ctx, snapshot_label, record.versions)
    match = replay_hash == record.result_hash
    return {"match": match, "result_hash": record.result_hash, "replay_hash": replay_hash}
