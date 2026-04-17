"""Адаптер импорта формы «Форма для подключения видеокамеры к ЕЦХД» (САС).

Использование:
    .venv/bin/python -m tools.import_sas_form "<путь_к_файлу.xlsx>"

Скрипт читает первый лист с табличной шапкой в третьей строке, формирует
записи в схеме приложения (object_name, camera_identifier, ...), сохраняет
адаптированный xlsx в data/ и применяет upsert в локальную БД через Repository.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402
from app.database.db import initialize_database  # noqa: E402
from app.database.repository import Repository  # noqa: E402


SHEET_NAME = "форма для заполнения"

# Индексы колонок (0-based) после header=None.
COL_NUM = 1            # № п/п
COL_UIN = 3            # УИН
COL_OBJECT = 4         # Наименование объекта
COL_ADDRESS = 5        # Адрес объекта
COL_GPS = 16           # GPS координаты
COL_IP = 17            # IP адрес
COL_RTSP = 22          # Ссылка на видеотрансляцию
COL_CAM_TYPE = 23      # Тип камеры (Тип 1/2/3)
COL_ZONE = 24          # Описание зоны обзора камеры
COL_LOCAL_NAME = 26    # Имя камеры в локальной системе видеонаблюдения

DATA_START_ROW = 4     # данные начинаются с 5-й строки (index 4)


def _clean(v) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    return str(v).strip()


def adapt(input_path: Path) -> list[dict]:
    df = pd.read_excel(input_path, sheet_name=SHEET_NAME, header=None, dtype=str, engine="openpyxl")

    rows: list[dict] = []
    uin_to_object_name: dict[str, str] = {}

    for r in range(DATA_START_ROW, len(df)):
        row = df.iloc[r]
        rtsp = _clean(row.get(COL_RTSP))
        if not rtsp.lower().startswith("rtsp"):
            continue

        uin = _clean(row.get(COL_UIN)) or "no-uin"
        raw_object = _clean(row.get(COL_OBJECT)) or uin

        if uin not in uin_to_object_name:
            uin_to_object_name[uin] = raw_object
        object_name = uin_to_object_name[uin]

        num = _clean(row.get(COL_NUM)) or str(r - DATA_START_ROW + 1)
        identifier = f"{uin}-{num}"

        zone = _clean(row.get(COL_ZONE))
        local_name = _clean(row.get(COL_LOCAL_NAME))
        camera_name = zone or local_name or f"Камера {num}"

        cam_type = _clean(row.get(COL_CAM_TYPE)) or "Тип ?"
        ip_addr = _clean(row.get(COL_IP))
        gps = _clean(row.get(COL_GPS))
        if ip_addr and ip_addr not in camera_name:
            camera_name = f"{camera_name} ({ip_addr})"
        rows.append(
            dict(
                object_name=object_name,
                camera_identifier=identifier,
                camera_name=camera_name,
                rtsp_url=rtsp,
                group_name=cam_type,
                gps_coords=gps,
                enabled=1,
            )
        )

    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="Импорт формы САС в RTSP Camera Monitor")
    ap.add_argument("path", help="Путь к XLSX")
    ap.add_argument(
        "--save-adapted",
        default=str(config.DATA_DIR / "cameras_imported_sas.xlsx"),
        help="Куда сохранить адаптированный XLSX",
    )
    args = ap.parse_args()

    src = Path(args.path).expanduser()
    if not src.is_file():
        print(f"Файл не найден: {src}", file=sys.stderr)
        return 2

    rows = adapt(src)
    if not rows:
        print("В файле не найдено камер с RTSP URL", file=sys.stderr)
        return 1

    out_xlsx = Path(args.save_adapted)
    out_xlsx.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=config.EXCEL_TEMPLATE_HEADERS).to_excel(
        out_xlsx, index=False, engine="openpyxl"
    )

    initialize_database(config.DB_PATH)
    repo = Repository()
    created = updated = 0
    for r in rows:
        _, action = repo.upsert_camera_for_object_name(
            object_name=r["object_name"],
            camera_identifier=r["camera_identifier"],
            camera_name=r["camera_name"],
            group_name=r["group_name"],
            rtsp_url=r["rtsp_url"],
            enabled=bool(r["enabled"]),
            gps_coords=r.get("gps_coords", ""),
        )
        if action == "created":
            created += 1
        else:
            updated += 1

    print(f"Адаптированный XLSX: {out_xlsx}")
    print(f"Камер обработано: {len(rows)} (создано {created}, обновлено {updated})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
