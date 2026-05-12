from __future__ import annotations

from collections import Counter

from app.database.db import get_connection
from app.database.models import CameraModel, ObjectModel
from app.utils.datetime_utils import now_iso


class Repository:
    def list_objects(self) -> list[ObjectModel]:
        sql = """
        SELECT
          o.id,
          o.name,
          o.created_at,
          o.updated_at,
          COUNT(c.id) AS camera_count,
          SUM(CASE WHEN c.status = 'online' THEN 1 ELSE 0 END) AS online_count,
          SUM(CASE WHEN c.status = 'offline' THEN 1 ELSE 0 END) AS offline_count
        FROM objects o
        LEFT JOIN cameras c ON c.object_id = o.id
        GROUP BY o.id
        ORDER BY o.name COLLATE NOCASE
        """
        with get_connection() as conn:
            rows = conn.execute(sql).fetchall()
        return [
            ObjectModel(
                id=row["id"],
                name=row["name"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                camera_count=row["camera_count"] or 0,
                online_count=row["online_count"] or 0,
                offline_count=row["offline_count"] or 0,
            )
            for row in rows
        ]

    def add_object(self, name: str) -> int:
        ts = now_iso()
        with get_connection() as conn:
            cur = conn.execute(
                "INSERT INTO objects(name, created_at, updated_at) VALUES(?,?,?)",
                (name.strip(), ts, ts),
            )
            return int(cur.lastrowid)

    def update_object(self, object_id: int, name: str) -> None:
        with get_connection() as conn:
            conn.execute(
                "UPDATE objects SET name = ?, updated_at = ? WHERE id = ?",
                (name.strip(), now_iso(), object_id),
            )

    def delete_object(self, object_id: int) -> None:
        with get_connection() as conn:
            conn.execute("DELETE FROM objects WHERE id = ?", (object_id,))

    def get_or_create_object(self, name: str) -> int:
        cleaned = name.strip()
        with get_connection() as conn:
            row = conn.execute("SELECT id FROM objects WHERE name = ?", (cleaned,)).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                "INSERT INTO objects(name, created_at, updated_at) VALUES(?,?,?)",
                (cleaned, now_iso(), now_iso()),
            )
            return int(cur.lastrowid)

    def _camera_from_row(self, row) -> CameraModel:
        keys = row.keys() if hasattr(row, "keys") else []
        gps = row["gps_coords"] if "gps_coords" in keys else ""
        uin = row["uin"] if "uin" in keys else ""
        ping_ok_raw = row["last_ping_ok"] if "last_ping_ok" in keys else None
        ping_ms_raw = row["last_ping_ms"] if "last_ping_ms" in keys else None
        return CameraModel(
            id=row["id"],
            object_id=row["object_id"],
            object_name=row["object_name"],
            camera_identifier=row["camera_identifier"],
            camera_name=row["camera_name"],
            group_name=row["group_name"],
            gps_coords=gps or "",
            uin=uin or "",
            rtsp_url=row["rtsp_url"],
            enabled=bool(row["enabled"]),
            status=row["status"],
            last_seen_online_at=row["last_seen_online_at"],
            last_checked_at=row["last_checked_at"],
            last_error=row["last_error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_ping_ok=None if ping_ok_raw is None else bool(ping_ok_raw),
            last_ping_ms=int(ping_ms_raw) if ping_ms_raw is not None else None,
        )

    def list_cameras(
        self,
        object_id: int | None = None,
        search: str = "",
        status_filter: str = "all",
    ) -> list[CameraModel]:
        params: list = []
        where: list[str] = []

        if object_id is not None:
            where.append("c.object_id = ?")
            params.append(object_id)
        if search.strip():
            q = f"%{search.strip()}%"
            where.append(
                "(COALESCE(c.uin,'') LIKE ? OR c.camera_identifier LIKE ? "
                "OR c.camera_name LIKE ? OR c.group_name LIKE ? OR o.name LIKE ?)"
            )
            params.extend([q, q, q, q, q])
        if status_filter in {"online", "offline", "unknown"}:
            where.append("c.status = ?")
            params.append(status_filter)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
        SELECT
          c.*,
          o.name AS object_name
        FROM cameras c
        JOIN objects o ON o.id = c.object_id
        {where_sql}
        ORDER BY o.name COLLATE NOCASE, c.camera_name COLLATE NOCASE
        """
        with get_connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._camera_from_row(row) for row in rows]

    def get_camera(self, camera_id: int) -> CameraModel | None:
        sql = """
        SELECT c.*, o.name AS object_name
        FROM cameras c
        JOIN objects o ON o.id = c.object_id
        WHERE c.id = ?
        """
        with get_connection() as conn:
            row = conn.execute(sql, (camera_id,)).fetchone()
        return self._camera_from_row(row) if row else None

    def add_camera(
        self,
        object_id: int,
        camera_identifier: str,
        camera_name: str,
        group_name: str,
        rtsp_url: str,
        enabled: bool,
        gps_coords: str = "",
        uin: str = "",
    ) -> int:
        ts = now_iso()
        with get_connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO cameras(
                  object_id, camera_identifier, camera_name, group_name, gps_coords, uin, rtsp_url,
                  enabled, status, last_seen_online_at, last_checked_at, last_error,
                  created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    object_id,
                    camera_identifier.strip(),
                    camera_name.strip(),
                    group_name.strip(),
                    gps_coords.strip(),
                    uin.strip(),
                    rtsp_url.strip(),
                    int(enabled),
                    "offline",
                    None,
                    None,
                    None,
                    ts,
                    ts,
                ),
            )
            conn.execute("UPDATE objects SET updated_at = ? WHERE id = ?", (ts, object_id))
            return int(cur.lastrowid)

    def update_camera(
        self,
        camera_id: int,
        object_id: int,
        camera_identifier: str,
        camera_name: str,
        group_name: str,
        rtsp_url: str,
        enabled: bool,
        gps_coords: str = "",
        uin: str = "",
    ) -> None:
        ts = now_iso()
        with get_connection() as conn:
            conn.execute(
                """
                UPDATE cameras
                SET object_id = ?, camera_identifier = ?, camera_name = ?, group_name = ?,
                    gps_coords = ?, uin = ?, rtsp_url = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    object_id,
                    camera_identifier.strip(),
                    camera_name.strip(),
                    group_name.strip(),
                    gps_coords.strip(),
                    uin.strip(),
                    rtsp_url.strip(),
                    int(enabled),
                    ts,
                    camera_id,
                ),
            )
            conn.execute("UPDATE objects SET updated_at = ? WHERE id = ?", (ts, object_id))

    def delete_camera(self, camera_id: int) -> None:
        with get_connection() as conn:
            row = conn.execute("SELECT object_id FROM cameras WHERE id = ?", (camera_id,)).fetchone()
            conn.execute("DELETE FROM cameras WHERE id = ?", (camera_id,))
            if row:
                conn.execute("UPDATE objects SET updated_at = ? WHERE id = ?", (now_iso(), row["object_id"]))

    def upsert_camera_for_object_name(
        self,
        object_name: str,
        camera_identifier: str,
        camera_name: str,
        group_name: str,
        rtsp_url: str,
        enabled: bool,
        gps_coords: str = "",
        uin: str = "",
    ) -> tuple[int, str]:
        object_id = self.get_or_create_object(object_name)
        ts = now_iso()
        with get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM cameras WHERE object_id = ? AND camera_identifier = ?",
                (object_id, camera_identifier.strip()),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE cameras
                    SET camera_name = ?, group_name = ?, gps_coords = ?, uin = ?, rtsp_url = ?,
                        enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        camera_name.strip(),
                        group_name.strip(),
                        gps_coords.strip(),
                        uin.strip(),
                        rtsp_url.strip(),
                        int(enabled),
                        ts,
                        row["id"],
                    ),
                )
                conn.execute("UPDATE objects SET updated_at = ? WHERE id = ?", (ts, object_id))
                return int(row["id"]), "updated"

            cur = conn.execute(
                """
                INSERT INTO cameras(
                  object_id, camera_identifier, camera_name, group_name, gps_coords, uin, rtsp_url, enabled,
                  status, last_seen_online_at, last_checked_at, last_error, created_at, updated_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    object_id,
                    camera_identifier.strip(),
                    camera_name.strip(),
                    group_name.strip(),
                    gps_coords.strip(),
                    uin.strip(),
                    rtsp_url.strip(),
                    int(enabled),
                    "offline",
                    None,
                    None,
                    None,
                    ts,
                    ts,
                ),
            )
            conn.execute("UPDATE objects SET updated_at = ? WHERE id = ?", (ts, object_id))
            return int(cur.lastrowid), "created"

    def bulk_upsert_cameras(self, rows) -> tuple[int, int, str | None]:
        """Пакетный upsert набора камер за одно подключение/транзакцию.

        Объекты создаются по уникальным именам, ``object_id`` кэшируются,
        что устраняет N открытий SQLite на каждую строку при импорте больших
        Excel-файлов.

        Третье значение — имя площадки для фокуса в UI после импорта: объект
        с наибольшим числом строк в файле; при равенстве — первый в порядке
        следования строк.
        """
        rows = list(rows)
        if not rows:
            return 0, 0, None
        created = 0
        updated = 0
        ts = now_iso()
        with get_connection() as conn:
            object_id_cache: dict[str, int] = {
                str(r["name"]): int(r["id"])
                for r in conn.execute("SELECT id, name FROM objects").fetchall()
            }

            select_sql = (
                "SELECT id FROM cameras WHERE object_id = ? AND camera_identifier = ?"
            )
            update_sql = (
                "UPDATE cameras SET camera_name = ?, group_name = ?, gps_coords = ?, "
                "uin = ?, rtsp_url = ?, enabled = ?, updated_at = ? WHERE id = ?"
            )
            insert_sql = (
                "INSERT INTO cameras("
                "  object_id, camera_identifier, camera_name, group_name, gps_coords, uin,"
                "  rtsp_url, enabled, status, last_seen_online_at, last_checked_at,"
                "  last_error, created_at, updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            )
            insert_object_sql = (
                "INSERT INTO objects(name, created_at, updated_at) VALUES(?,?,?)"
            )

            touched_object_ids: set[int] = set()

            for row in rows:
                object_name = str(row.get("object_name", "")).strip()
                if not object_name:
                    continue
                object_id = object_id_cache.get(object_name)
                if object_id is None:
                    cur = conn.execute(insert_object_sql, (object_name, ts, ts))
                    object_id = int(cur.lastrowid)
                    object_id_cache[object_name] = object_id

                identifier = str(row.get("camera_identifier", "")).strip()
                params_common = (
                    str(row.get("camera_name", "")).strip(),
                    str(row.get("group_name", "")).strip(),
                    str(row.get("gps_coords", "")).strip(),
                    str(row.get("uin", "")).strip(),
                    str(row.get("rtsp_url", "")).strip(),
                    int(bool(row.get("enabled", True))),
                )

                existing = conn.execute(select_sql, (object_id, identifier)).fetchone()
                if existing:
                    conn.execute(update_sql, (*params_common, ts, int(existing["id"])))
                    updated += 1
                else:
                    conn.execute(
                        insert_sql,
                        (
                            object_id,
                            identifier,
                            *params_common,
                            "offline",
                            None,
                            None,
                            None,
                            ts,
                            ts,
                        ),
                    )
                    created += 1
                touched_object_ids.add(object_id)

            for obj_id in touched_object_ids:
                conn.execute(
                    "UPDATE objects SET updated_at = ? WHERE id = ?", (ts, obj_id)
                )

        first_seen: list[str] = []
        seen_names: set[str] = set()
        for row in rows:
            n = str(row.get("object_name", "")).strip()
            if not n or n in seen_names:
                continue
            seen_names.add(n)
            first_seen.append(n)
        names_for_count = [
            str(r.get("object_name", "")).strip() for r in rows if str(r.get("object_name", "")).strip()
        ]
        focus_object_name: str | None = None
        if names_for_count:
            counts = Counter(names_for_count)
            best = max(counts.values())
            top = {n for n, c in counts.items() if c == best}
            focus_object_name = next((n for n in first_seen if n in top), next(iter(top)))

        return created, updated, focus_object_name

    def bulk_update_field(self, camera_ids: list[int], field: str, value) -> int:
        """Массовое обновление одного поля у камер. Возвращает число затронутых строк."""
        allowed = {"group_name", "gps_coords", "uin", "enabled", "object_id"}
        if field not in allowed:
            raise ValueError(f"Поле {field} нельзя обновлять массово")
        if not camera_ids:
            return 0
        placeholders = ",".join("?" for _ in camera_ids)
        ts = now_iso()
        sql = (
            f"UPDATE cameras SET {field} = ?, updated_at = ? "
            f"WHERE id IN ({placeholders})"
        )
        params = [value, ts, *camera_ids]
        with get_connection() as conn:
            cur = conn.execute(sql, params)
            object_ids = [
                int(r["object_id"])
                for r in conn.execute(
                    f"SELECT DISTINCT object_id FROM cameras WHERE id IN ({placeholders})",
                    camera_ids,
                ).fetchall()
            ]
            for obj_id in object_ids:
                conn.execute(
                    "UPDATE objects SET updated_at = ? WHERE id = ?",
                    (ts, obj_id),
                )
            return int(cur.rowcount or 0)

    def bulk_delete_cameras(self, camera_ids: list[int]) -> int:
        if not camera_ids:
            return 0
        placeholders = ",".join("?" for _ in camera_ids)
        ts = now_iso()
        with get_connection() as conn:
            object_ids = [
                int(r["object_id"])
                for r in conn.execute(
                    f"SELECT DISTINCT object_id FROM cameras WHERE id IN ({placeholders})",
                    camera_ids,
                ).fetchall()
            ]
            cur = conn.execute(
                f"DELETE FROM cameras WHERE id IN ({placeholders})", camera_ids
            )
            for obj_id in object_ids:
                conn.execute(
                    "UPDATE objects SET updated_at = ? WHERE id = ?", (ts, obj_id)
                )
            return int(cur.rowcount or 0)

    def update_camera_status(
        self,
        camera_id: int,
        status: str,
        last_checked_at: str,
        last_error: str | None,
        last_seen_online_at: str | None = None,
        last_ping_ok: bool | None = None,
        last_ping_ms: int | None = None,
    ) -> None:
        ping_ok_val = None if last_ping_ok is None else int(bool(last_ping_ok))
        with get_connection() as conn:
            if status == "online":
                conn.execute(
                    """
                    UPDATE cameras
                    SET status = ?, last_checked_at = ?, last_error = ?, last_seen_online_at = ?,
                        last_ping_ok = ?, last_ping_ms = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        last_checked_at,
                        last_error,
                        last_seen_online_at,
                        ping_ok_val,
                        last_ping_ms,
                        now_iso(),
                        camera_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE cameras
                    SET status = ?, last_checked_at = ?, last_error = ?,
                        last_ping_ok = ?, last_ping_ms = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        status,
                        last_checked_at,
                        last_error,
                        ping_ok_val,
                        last_ping_ms,
                        now_iso(),
                        camera_id,
                    ),
                )

    def count_all_cameras(self) -> int:
        with get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) as cnt FROM cameras").fetchone()
        return int(row["cnt"] if row else 0)

    def seed_demo_data(self) -> None:
        if self.count_all_cameras() > 0:
            return

        object_id = self.get_or_create_object("Демо объект")
        self.add_camera(
            object_id=object_id,
            camera_identifier="demo-entrance-01",
            camera_name="КПП вход",
            group_name="КПП",
            rtsp_url="rtsp://127.0.0.1:8554/entrance",
            enabled=True,
        )
        self.add_camera(
            object_id=object_id,
            camera_identifier="demo-yard-01",
            camera_name="Двор",
            group_name="Двор",
            rtsp_url="rtsp://127.0.0.1:8554/yard",
            enabled=False,
        )

