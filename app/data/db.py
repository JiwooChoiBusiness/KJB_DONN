"""DONN SQLite 데이터 계층.

app/data의 다른 모든 모듈이 공유하는 커넥션 생성과 스키마 초기화만 제공한다.
PoC 규모(단일 프로세스, 로컬 파일 DB)에 맞춰 커넥션은 호출부에서 열고 닫는
단순한 모델을 쓴다(장기 커넥션 풀 없음).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("DONN_DB_PATH", "data/donn.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    fetched_at  TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    note        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS products (
    id                TEXT PRIMARY KEY,
    snapshot_id       TEXT NOT NULL,
    source            TEXT NOT NULL,
    category          TEXT NOT NULL,
    lender_group      TEXT NOT NULL,
    company_code      TEXT NOT NULL,
    company_name      TEXT NOT NULL,
    product_code      TEXT NOT NULL,
    product_name      TEXT NOT NULL,
    rate_semantics    TEXT NOT NULL,
    disclosure_month  TEXT DEFAULT '',
    disclosure_url    TEXT DEFAULT '',
    json              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_snapshot_category
    ON products(snapshot_id, category);

CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT PRIMARY KEY,
    json       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    decision_id       TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL,
    result_hash       TEXT NOT NULL,
    context_json      TEXT NOT NULL,
    result_json       TEXT NOT NULL,
    versions_json     TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS chats (
    id          TEXT PRIMARY KEY,
    profile_id  TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chats_profile ON chats(profile_id, updated_at);
CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    llm_used    INTEGER NOT NULL DEFAULT 0,
    action_json TEXT,
    chips_json  TEXT,
    trace_json  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_chat ON chat_messages(chat_id, id);

CREATE TABLE IF NOT EXISTS spending_features (
    profile_id     TEXT PRIMARY KEY,
    features_json  TEXT NOT NULL,
    summary_json   TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS explanations (
    kind          TEXT NOT NULL,
    ref_id        TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY(kind, ref_id)
);
"""


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """새 SQLite 커넥션을 연다. 상위 디렉터리가 없으면 만든다.

    db_path를 생략하면 모듈 전역 DB_PATH(환경변수 DONN_DB_PATH)를 쓴다.
    """
    path = db_path or DB_PATH
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_chat_messages_trace_json(conn: sqlite3.Connection) -> None:
    """기존 DB(이 컬럼이 생기기 전에 만들어진 파일)에 chat_messages.trace_json이 없으면
    추가한다. `CREATE TABLE IF NOT EXISTS`는 이미 있는 테이블의 컬럼을 바꾸지 않으므로
    별도 마이그레이션이 필요하다(SPEC 2.9)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(chat_messages)").fetchall()}
    if "trace_json" not in cols:
        conn.execute("ALTER TABLE chat_messages ADD COLUMN trace_json TEXT")
        conn.commit()


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """스키마를 만든다(이미 있으면 무시). 사용한 커넥션을 반환한다.

    conn을 생략하면 get_conn()으로 새로 연다(호출부가 닫아야 한다).
    """
    c = conn if conn is not None else get_conn()
    c.executescript(SCHEMA_SQL)
    c.commit()
    _migrate_chat_messages_trace_json(c)
    return c
