from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app import config


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS objects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cameras (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  object_id INTEGER NOT NULL,
  camera_identifier TEXT NOT NULL,
  camera_name TEXT NOT NULL,
  group_name TEXT NOT NULL DEFAULT '',
  gps_coords TEXT NOT NULL DEFAULT '',
  uin TEXT NOT NULL DEFAULT '',
  rtsp_url TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'unknown',
  last_seen_online_at TEXT NULL,
  last_checked_at TEXT NULL,
  last_error TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(object_id) REFERENCES objects(id) ON DELETE CASCADE,
  UNIQUE(object_id, camera_identifier)
);
"""


def _ensure_columns(conn) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(cameras)").fetchall()}
    if "gps_coords" not in cols:
        conn.execute("ALTER TABLE cameras ADD COLUMN gps_coords TEXT NOT NULL DEFAULT ''")
    if "uin" not in cols:
        conn.execute("ALTER TABLE cameras ADD COLUMN uin TEXT NOT NULL DEFAULT ''")
    # Однократная миграция: распарсить старые group_name вида "Тип 2 | GPS 55.7, 37.6"
    rows = conn.execute(
        "SELECT id, group_name, gps_coords FROM cameras WHERE gps_coords = '' AND group_name LIKE '%GPS%'"
    ).fetchall()
    for r in rows:
        gn = r["group_name"]
        if " | GPS " in gn:
            type_part, gps_part = gn.split(" | GPS ", 1)
            conn.execute(
                "UPDATE cameras SET group_name = ?, gps_coords = ? WHERE id = ?",
                (type_part.strip(), gps_part.strip(), r["id"]),
            )


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def initialize_database(db_path: Path | None = None) -> None:
    path = db_path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_columns(conn)
        _normalize_error_codes(conn)
        conn.commit()


def _normalize_error_codes(conn) -> None:
    """Перевод старого кода '0x03' в актуальный UNKNOWN_DEEP_FAIL_CODE для unknown-камер."""
    new_code = config.UNKNOWN_DEEP_FAIL_CODE
    if new_code == "0x03":
        return
    conn.execute(
        "UPDATE cameras SET last_error = ? "
        "WHERE status = 'unknown' AND TRIM(IFNULL(last_error, '')) = '0x03'",
        (new_code,),
    )


@contextmanager
def get_connection(db_path: Path | None = None):
    conn = _connect(db_path or config.DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

