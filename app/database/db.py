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
  last_ping_ok INTEGER NULL,
  last_ping_ms INTEGER NULL,
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
    if "last_ping_ok" not in cols:
        conn.execute("ALTER TABLE cameras ADD COLUMN last_ping_ok INTEGER NULL")
    if "last_ping_ms" not in cols:
        conn.execute("ALTER TABLE cameras ADD COLUMN last_ping_ms INTEGER NULL")
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
        _migrate_drop_legacy_unknown_codes(conn)
        conn.commit()


def _migrate_drop_legacy_unknown_codes(conn) -> None:
    """В v0.1.36 убрали промежуточный unknown-код «0x01 Буферизация > 2 min»
    (а также его предшественника «0x03»). Теперь камера после короткой 5 c
    проверки уходит в unknown один раз и на следующем тике попадает на
    длинную (~2 мин) проверку, по итогу которой становится online или offline.

    Чтобы старые БД, в которых эти коды могли «залипнуть» в last_error,
    начали жить по новой схеме — обнуляем такие пометки. Делаем это один
    раз (PRAGMA user_version), чтобы пользовательские заметки об ошибках
    у offline-камер не затирались при каждом запуске.
    """
    target_version = 2
    cur_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur_version >= target_version:
        return
    conn.execute(
        "UPDATE cameras SET last_error = NULL "
        "WHERE status = 'unknown' AND ("
        "  TRIM(IFNULL(last_error, '')) IN ('0x01', '0x03') "
        "  OR IFNULL(last_error, '') LIKE '%Буферизация > 2 min%' "
        "  OR IFNULL(last_error, '') LIKE '%(0x01)%' "
        "  OR IFNULL(last_error, '') LIKE '%(0x03)%' "
        ")",
    )
    conn.execute(f"PRAGMA user_version = {target_version}")


@contextmanager
def get_connection(db_path: Path | None = None):
    conn = _connect(db_path or config.DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

