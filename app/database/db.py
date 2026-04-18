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
        _normalize_error_codes(conn)
        _migrate_clear_v031_spurious_deep_fail(conn)
        conn.commit()


def _normalize_error_codes(conn) -> None:
    """Переводим старые «голые» коды deep-проверки в новый текстовый формат."""
    msg = config.UNKNOWN_DEEP_FAIL_MESSAGE
    legacy_codes = {"0x03", "0x01"}
    legacy_codes.discard(msg)
    if not legacy_codes:
        return
    placeholders = ",".join("?" for _ in legacy_codes)
    conn.execute(
        f"UPDATE cameras SET last_error = ? "
        f"WHERE status = 'unknown' AND TRIM(IFNULL(last_error, '')) IN ({placeholders})",
        (msg, *legacy_codes),
    )


def _migrate_clear_v031_spurious_deep_fail(conn) -> None:
    """v0.1.31 имел баг: ffprobe валился на «-stimeout», а наш детектор
    таймаута видел подстроку «timeout» в «Unrecognized option 'stimeout'»
    и помечал живые камеры кодом deep-fail. После рестарта они уходили
    бы сразу в 120-секундный ULTRA-режим. Сбрасываем эти отметки один раз
    (через PRAGMA user_version), чтобы следующая проверка пошла по
    нормальному (или DEEP) пути и быстро вернула реальные статусы.
    """
    target_version = 1
    cur_version = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur_version >= target_version:
        return
    msg = config.UNKNOWN_DEEP_FAIL_MESSAGE
    conn.execute(
        "UPDATE cameras SET last_error = NULL "
        "WHERE status = 'unknown' AND TRIM(IFNULL(last_error, '')) = ?",
        (msg,),
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

