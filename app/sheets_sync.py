from __future__ import annotations

import logging
from dataclasses import dataclass, field

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from . import config
from .rtsp_probe import looks_like_rtsp_url

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _quote_sheet_tab(title: str) -> str:
    escaped = title.replace("'", "''")
    return f"'{escaped}'"


def _norm_header(s: str) -> str:
    t = (s or "").strip().lower().replace("ё", "е")
    return " ".join(t.split())


# ключ -> допустимые подписи колонки (после нормализации)
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "project": ("проект", "project", "объект", "object", "площадка"),
    "name": (
        "наименование",
        "название",
        "камера",
        "name",
        "точка",
        "имя",
    ),
    "camera_type": (
        "тип камеры",
        "тип",
        "type",
        "модель",
        "model",
        "производитель",
    ),
    "url": ("rtsp", "url", "адрес", "ссылка", "поток", "link", "стрим"),
}


@dataclass
class CameraRecord:
    """camera_id: T_{sheet_gid}_{excel_row} в режиме таблицы, L_{sheet_gid} в legacy."""

    camera_id: str
    source_sheet_id: int
    rtsp_url: str
    project: str = ""
    name: str = ""
    camera_type: str = ""
    cell_a1: str | None = None
    legacy_sheet_title: str = ""


@dataclass
class SheetsState:
    cameras: list[CameraRecord] = field(default_factory=list)
    last_error: str | None = None
    updated_at_iso: str | None = None
    table_mode: bool = False
    table_sheet_title: str | None = None


def _build_sheets_client():
    if config.GOOGLE_APPLICATION_CREDENTIALS:
        creds = service_account.Credentials.from_service_account_file(
            config.GOOGLE_APPLICATION_CREDENTIALS,
            scopes=SCOPES,
        )
        return build("sheets", "v4", credentials=creds, cache_discovery=False)
    if config.GOOGLE_SHEETS_API_KEY:
        return build(
            "sheets",
            "v4",
            developerKey=config.GOOGLE_SHEETS_API_KEY,
            cache_discovery=False,
        )
    raise RuntimeError(
        "Укажите в .env GOOGLE_APPLICATION_CREDENTIALS (JSON сервисного аккаунта) "
        "или GOOGLE_SHEETS_API_KEY (ключ API; в Google Cloud включите Sheets API; "
        "таблица — доступ «Просматривать могут все в интернете»)."
    )


def _col_index_to_a1(idx: int) -> str:
    n = idx + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


def _map_header_row(row: list) -> dict[str, int] | None:
    if not row:
        return None
    col_map: dict[str, int] = {}
    for idx, cell in enumerate(row):
        key = _norm_header(str(cell) if cell is not None else "")
        if not key:
            continue
        for field_name, aliases in HEADER_ALIASES.items():
            if field_name in col_map:
                continue
            if any(key == a or key.startswith(a + " ") for a in aliases):
                col_map[field_name] = idx
                break
    if "url" not in col_map:
        return None
    for k in ("project", "name", "camera_type"):
        if k not in col_map:
            col_map[k] = -1
    return col_map


def _cell(row: list, idx: int) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    v = row[idx]
    if v is None:
        return ""
    return str(v).strip()


def _parse_table_sheet(
    values: list[list],
    source_sheet_id: int,
    sheet_title: str,
) -> list[CameraRecord]:
    if not values:
        return []
    col_map = _map_header_row(values[0])
    if not col_map:
        col_map = {"project": 0, "name": 1, "camera_type": 2, "url": 3}
        log.info(
            "лист «%s»: строка заголовков не распознана, колонки A=проект B=имя C=тип D=URL",
            sheet_title,
        )

    cameras: list[CameraRecord] = []
    for r_idx in range(1, len(values)):
        row = values[r_idx]
        url = _cell(row, col_map["url"])
        if not looks_like_rtsp_url(url):
            continue
        excel_row = r_idx + 1
        pj = _cell(row, col_map["project"])
        nm = _cell(row, col_map["name"])
        ct = _cell(row, col_map["camera_type"])
        if not nm:
            nm = f"Строка {excel_row}"
        ucol = col_map["url"]
        cell_a1 = f"{_col_index_to_a1(ucol)}{excel_row}"
        cid = f"T_{source_sheet_id}_{excel_row}"
        cameras.append(
            CameraRecord(
                camera_id=cid,
                source_sheet_id=source_sheet_id,
                rtsp_url=url,
                project=pj,
                name=nm,
                camera_type=ct,
                cell_a1=cell_a1,
            )
        )
    return cameras


def _find_rtsp_in_grid(values: list[list]) -> tuple[str | None, str | None]:
    if not values:
        return None, None
    for r_idx, row in enumerate(values):
        for c_idx, cell in enumerate(row):
            if cell is None:
                continue
            text = str(cell).strip()
            if looks_like_rtsp_url(text):
                col_letter = _col_index_to_a1(c_idx)
                a1 = f"{col_letter}{r_idx + 1}"
                return text, a1
    return None, None


def _fetch_sheet_values(service, spreadsheet_id: str, sheet_title: str) -> list[list] | None:
    tab = _quote_sheet_tab(sheet_title)
    range_a1 = f"{tab}!A1:Z500"
    try:
        res = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=range_a1,
                majorDimension="ROWS",
            )
            .execute()
        )
    except HttpError as e:
        log.warning("read range %s: %s", range_a1, e)
        return None
    return res.get("values") or []


def fetch_cameras_from_spreadsheet() -> SheetsState:
    from datetime import datetime, timezone

    if not config.SPREADSHEET_ID:
        return SheetsState(
            cameras=[],
            last_error="Не задан SPREADSHEET_ID в .env",
            updated_at_iso=datetime.now(timezone.utc).isoformat(),
        )

    try:
        service = _build_sheets_client()
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=config.SPREADSHEET_ID,
                fields="sheets(properties(sheetId,title))",
            )
            .execute()
        )
    except HttpError as e:
        msg = f"Google Sheets API: {e.status_code} {e.reason}"
        log.warning("%s", msg)
        return SheetsState(
            cameras=[],
            last_error=msg,
            updated_at_iso=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        msg = str(e)
        log.exception("sheets fetch failed")
        return SheetsState(
            cameras=[],
            last_error=msg,
            updated_at_iso=datetime.now(timezone.utc).isoformat(),
        )

    sheets = meta.get("sheets") or []
    titles_to_props: dict[str, dict] = {}
    for sh in sheets:
        props = (sh or {}).get("properties") or {}
        title = (props.get("title") or "").strip()
        if title:
            titles_to_props[title] = props

    table_props: dict | None = None
    table_sheet_name: str = ""

    if config.CAMERAS_SHEET_GID is not None:
        want = int(config.CAMERAS_SHEET_GID)
        for sh in sheets:
            props = (sh or {}).get("properties") or {}
            sid = int(props.get("sheetId", -1))
            if sid == want:
                table_props = props
                table_sheet_name = (props.get("title") or "").strip() or f"gid{want}"
                break
        if table_props is None:
            return SheetsState(
                cameras=[],
                last_error=f"Лист с gid={want} не найден в этой книге",
                updated_at_iso=datetime.now(timezone.utc).isoformat(),
                table_mode=True,
                table_sheet_title=None,
            )
    else:
        tname = (config.CAMERAS_SHEET or "").strip()
        if tname and tname in titles_to_props:
            table_props = titles_to_props[tname]
            table_sheet_name = tname

    if table_props is not None:
        sheet_id = int(table_props.get("sheetId", 0))
        values = _fetch_sheet_values(service, config.SPREADSHEET_ID, table_sheet_name)
        if values is None:
            return SheetsState(
                cameras=[],
                last_error=f"Не удалось прочитать лист «{table_sheet_name}»",
                updated_at_iso=datetime.now(timezone.utc).isoformat(),
                table_mode=True,
                table_sheet_title=table_sheet_name,
            )
        cameras = _parse_table_sheet(values, sheet_id, table_sheet_name)
        cameras.sort(key=lambda c: (c.project.lower(), c.name.lower()))
        return SheetsState(
            cameras=cameras,
            last_error=None,
            updated_at_iso=datetime.now(timezone.utc).isoformat(),
            table_mode=True,
            table_sheet_title=table_sheet_name,
        )

    name_for_legacy_skip = (config.CAMERAS_SHEET or "").strip()

    cameras: list[CameraRecord] = []
    for sh in sheets:
        props = (sh or {}).get("properties") or {}
        title = (props.get("title") or "").strip()
        sheet_id = int(props.get("sheetId", 0))
        if not title or title in config.IGNORE_SHEETS:
            continue
        if name_for_legacy_skip and title == name_for_legacy_skip:
            continue

        values = _fetch_sheet_values(service, config.SPREADSHEET_ID, title)
        if values is None:
            continue
        url, cell = _find_rtsp_in_grid(values)
        if not url:
            log.info("лист «%s»: RTSP URL не найден", title)
            continue

        cid = f"L_{sheet_id}"
        cameras.append(
            CameraRecord(
                camera_id=cid,
                source_sheet_id=sheet_id,
                rtsp_url=url,
                project="",
                name=title,
                camera_type="",
                cell_a1=cell,
                legacy_sheet_title=title,
            )
        )

    cameras.sort(key=lambda c: c.name.lower())
    return SheetsState(
        cameras=cameras,
        last_error=None,
        updated_at_iso=datetime.now(timezone.utc).isoformat(),
        table_mode=False,
        table_sheet_title=None,
    )


def check_spreadsheet_access() -> dict:
    """Проверка: ключ, метаданные книги, наличие целевого листа, чтение ячейки A1."""
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    out: dict = {
        "ok": False,
        "checked_at": ts,
        "spreadsheet_id": config.SPREADSHEET_ID,
        "spreadsheet_title": None,
        "sheets_count": 0,
        "camera_sheet": None,
        "read_sample_ok": False,
        "auth_mode": config.SHEETS_AUTH_MODE,
        "error": None,
    }

    if not config.SPREADSHEET_ID:
        out["error"] = "Не задан SPREADSHEET_ID"
        return out
    if config.SHEETS_AUTH_MODE == "none":
        out["error"] = (
            "Нет учётных данных: задайте GOOGLE_APPLICATION_CREDENTIALS или GOOGLE_SHEETS_API_KEY"
        )
        return out

    try:
        service = _build_sheets_client()
        meta = (
            service.spreadsheets()
            .get(
                spreadsheetId=config.SPREADSHEET_ID,
                fields="properties.title,sheets(properties(sheetId,title))",
            )
            .execute()
        )
    except HttpError as e:
        out["error"] = f"Google Sheets API {e.status_code}: {e.reason}"
        return out
    except Exception as e:
        out["error"] = str(e)
        log.exception("check_spreadsheet_access")
        return out

    out["spreadsheet_title"] = (meta.get("properties") or {}).get("title")
    sheets = meta.get("sheets") or []
    out["sheets_count"] = len(sheets)

    titles_to_props: dict[str, dict] = {}
    for sh in sheets:
        props = (sh or {}).get("properties") or {}
        title = (props.get("title") or "").strip()
        if title:
            titles_to_props[title] = props

    table_props: dict | None = None
    table_sheet_name = ""

    if config.CAMERAS_SHEET_GID is not None:
        want = int(config.CAMERAS_SHEET_GID)
        for sh in sheets:
            props = (sh or {}).get("properties") or {}
            if int(props.get("sheetId", -1)) == want:
                table_props = props
                table_sheet_name = (props.get("title") or "").strip() or f"gid{want}"
                break
        if table_props is None:
            out["error"] = f"Лист с gid={want} не найден в книге"
            return out
    else:
        tname = (config.CAMERAS_SHEET or "").strip()
        if tname and tname in titles_to_props:
            table_props = titles_to_props[tname]
            table_sheet_name = tname

    if table_props is not None:
        sid = int(table_props.get("sheetId", 0))
        out["camera_sheet"] = {
            "mode": "table",
            "gid": sid,
            "title": table_sheet_name,
        }
        sample_title = table_sheet_name
    else:
        first = None
        for sh in sheets:
            props = (sh or {}).get("properties") or {}
            title = (props.get("title") or "").strip()
            if title and title not in config.IGNORE_SHEETS:
                first = title
                break
        out["camera_sheet"] = {
            "mode": "legacy",
            "message": "Камеры с вкладок с RTSP",
            "sample_sheet_title": first,
        }
        sample_title = first

    if not sample_title:
        out["error"] = "Нет ни одного листа для проверки чтения (все в IGNORE_SHEETS?)"
        return out

    tab = _quote_sheet_tab(sample_title)
    try:
        service.spreadsheets().values().get(
            spreadsheetId=config.SPREADSHEET_ID,
            range=f"{tab}!A1:A1",
            majorDimension="ROWS",
        ).execute()
        out["read_sample_ok"] = True
    except HttpError as e:
        out["error"] = f"Чтение A1 листа «{sample_title}»: {e.status_code} {e.reason}"
        return out
    except Exception as e:
        out["error"] = f"Чтение A1: {e}"
        return out

    out["ok"] = True
    return out
