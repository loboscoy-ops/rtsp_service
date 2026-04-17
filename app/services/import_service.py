from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app import config
from app.database.repository import Repository
from app.utils.validators import is_valid_rtsp_url, parse_enabled


REQUIRED_FIELDS = ("object_name", "camera_identifier", "camera_name", "rtsp_url")
OPTIONAL_FIELDS = ("group_name", "gps_coords", "enabled")
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

# Подсказки для авто-подбора колонок (точные совпадения и частичные).
FIELD_HINTS: dict[str, tuple[str, ...]] = {
    "object_name": (
        "object_name",
        "наименование объекта",
        "объект",
        "проект",
        "площадка",
    ),
    "camera_identifier": (
        "camera_identifier",
        "уин",
        "uin",
        "id",
        "идентификатор",
        "№ п/п",
        "номер",
    ),
    "camera_name": (
        "camera_name",
        "имя камеры",
        "наименование камеры",
        "описание зоны обзора камеры",
        "описание зоны",
        "имя камеры в локальной системе видеонаблюдения",
    ),
    "rtsp_url": (
        "rtsp_url",
        "ссылка на видеотрансляцию",
        "rtsp",
        "url",
        "ссылка",
        "поток",
    ),
    "group_name": (
        "group_name",
        "группа",
        "зона",
        "тип камеры",
        "место установки камеры",
    ),
    "gps_coords": (
        "gps_coords",
        "gps координаты",
        "gps",
        "координаты",
        "координата",
    ),
    "enabled": (
        "enabled",
        "активна",
        "вкл",
        "включена",
    ),
}


@dataclass
class PreviewIssue:
    row_number: int
    message: str


@dataclass
class PreviewRow:
    object_name: str
    camera_identifier: str
    camera_name: str
    rtsp_url: str
    group_name: str
    gps_coords: str
    enabled: bool
    valid: bool
    error: str = ""


@dataclass
class ImportPreview:
    rows: list[PreviewRow] = field(default_factory=list)
    issues: list[PreviewIssue] = field(default_factory=list)

    @property
    def valid_rows(self) -> list[PreviewRow]:
        return [r for r in self.rows if r.valid]


@dataclass
class SheetData:
    name: str
    rows: list[list[str]]


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().replace("ё", "е").split())


class ImportService:
    def __init__(self, repository: Repository):
        self.repository = repository

    @staticmethod
    def _clean_cell(value: object) -> str:
        if value is None:
            return ""
        try:
            if pd.isna(value):
                return ""
        except Exception:
            pass
        return str(value).strip()

    @staticmethod
    def _engine_for(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix == ".xls":
            return "xlrd"
        return "openpyxl"

    def list_sheets(self, path: Path) -> list[SheetData]:
        sheets: list[SheetData] = []
        engine = self._engine_for(path)
        with pd.ExcelFile(path, engine=engine) as xls:
            for name in xls.sheet_names:
                df = pd.read_excel(
                    xls,
                    sheet_name=name,
                    header=None,
                    dtype=str,
                    engine=engine,
                )
                rows = [
                    [self._clean_cell(c) for c in row]
                    for row in df.itertuples(index=False, name=None)
                ]
                while rows and not any(c for c in rows[-1]):
                    rows.pop()
                sheets.append(SheetData(name=name, rows=rows))
        return sheets

    def auto_detect_mapping(self, header_row: list[str]) -> dict[str, int | None]:
        normalized = [_norm(c) for c in header_row]
        used: set[int] = set()
        mapping: dict[str, int | None] = {f: None for f in ALL_FIELDS}
        # 1. exact match first
        for field_name, hints in FIELD_HINTS.items():
            wanted = {_norm(h) for h in hints}
            for idx, h in enumerate(normalized):
                if not h or idx in used:
                    continue
                if h in wanted:
                    mapping[field_name] = idx
                    used.add(idx)
                    break
        # 2. fuzzy substring fallback for fields not yet mapped
        for field_name, hints in FIELD_HINTS.items():
            if mapping[field_name] is not None:
                continue
            for idx, h in enumerate(normalized):
                if not h or idx in used:
                    continue
                if any(_norm(hint) in h for hint in hints):
                    mapping[field_name] = idx
                    used.add(idx)
                    break
        return mapping

    def build_preview_from_mapping(
        self,
        sheet: SheetData,
        header_row_index: int,
        mapping: dict[str, int | None],
    ) -> ImportPreview:
        preview = ImportPreview()
        if not sheet.rows:
            preview.issues.append(PreviewIssue(0, "Пустой лист"))
            return preview

        for required in REQUIRED_FIELDS:
            col_idx = mapping.get(required)
            if col_idx is None:
                preview.issues.append(
                    PreviewIssue(
                        0,
                        f"Не задано соответствие колонки для поля «{required}»",
                    )
                )
        if preview.issues:
            return preview

        seen_keys: set[tuple[str, str]] = set()
        for r_idx in range(header_row_index + 1, len(sheet.rows)):
            row = sheet.rows[r_idx]
            row_no = r_idx + 1

            def _val(field_name: str) -> str:
                col = mapping.get(field_name)
                if col is None or col < 0 or col >= len(row):
                    return ""
                return row[col]

            object_name = _val("object_name")
            camera_identifier = _val("camera_identifier")
            camera_name = _val("camera_name")
            rtsp_url = _val("rtsp_url")
            group_name = _val("group_name")
            gps_coords = _val("gps_coords")
            enabled_raw = _val("enabled")
            enabled = parse_enabled(enabled_raw) if enabled_raw else True

            if not any([object_name, camera_identifier, camera_name, rtsp_url, group_name, gps_coords]):
                continue

            err_parts: list[str] = []
            if not object_name:
                err_parts.append("object_name пустой")
            if not camera_identifier:
                err_parts.append("camera_identifier пустой")
            if not camera_name:
                err_parts.append("camera_name пустой")
            if not is_valid_rtsp_url(rtsp_url):
                err_parts.append("rtsp_url некорректный")
            key = (object_name.lower(), camera_identifier.lower())
            if object_name and camera_identifier:
                if key in seen_keys:
                    err_parts.append("дубликат object_name + camera_identifier")
                else:
                    seen_keys.add(key)

            err = "; ".join(err_parts)
            preview.rows.append(
                PreviewRow(
                    object_name=object_name,
                    camera_identifier=camera_identifier,
                    camera_name=camera_name,
                    rtsp_url=rtsp_url,
                    group_name=group_name,
                    gps_coords=gps_coords,
                    enabled=enabled,
                    valid=not err_parts,
                    error=err,
                )
            )
            if err_parts:
                preview.issues.append(PreviewIssue(row_no, err))

        return preview

    def import_valid_rows(self, preview: ImportPreview) -> tuple[int, int]:
        created = 0
        updated = 0
        for row in preview.valid_rows:
            _, action = self.repository.upsert_camera_for_object_name(
                object_name=row.object_name,
                camera_identifier=row.camera_identifier,
                camera_name=row.camera_name,
                group_name=row.group_name,
                rtsp_url=row.rtsp_url,
                enabled=row.enabled,
                gps_coords=row.gps_coords,
            )
            if action == "created":
                created += 1
            else:
                updated += 1
        return created, updated
