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


@dataclass
class CameraRecord:
    sheet_id: int
    sheet_title: str
    rtsp_url: str
    cell_a1: str | None = None


@dataclass
class SheetsState:
    cameras: list[CameraRecord] = field(default_factory=list)
    last_error: str | None = None
    updated_at_iso: str | None = None


def _build_sheets_client():
    if not config.GOOGLE_APPLICATION_CREDENTIALS:
        raise RuntimeError("Не задан GOOGLE_APPLICATION_CREDENTIALS в .env")
    creds = service_account.Credentials.from_service_account_file(
        config.GOOGLE_APPLICATION_CREDENTIALS,
        scopes=SCOPES,
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


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


def _col_index_to_a1(idx: int) -> str:
    n = idx + 1
    letters = []
    while n:
        n, rem = divmod(n - 1, 26)
        letters.append(chr(65 + rem))
    return "".join(reversed(letters))


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
    cameras: list[CameraRecord] = []

    for sh in sheets:
        props = (sh or {}).get("properties") or {}
        title = (props.get("title") or "").strip()
        sheet_id = int(props.get("sheetId", 0))
        if not title or title in config.IGNORE_SHEETS:
            continue

        tab = _quote_sheet_tab(title)
        range_a1 = f"{tab}!A1:Z200"
        try:
            res = (
                service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=config.SPREADSHEET_ID,
                    range=range_a1,
                    majorDimension="ROWS",
                )
                .execute()
            )
        except HttpError as e:
            log.warning("read range %s: %s", range_a1, e)
            continue

        values = res.get("values") or []
        url, cell = _find_rtsp_in_grid(values)
        if not url:
            log.info("лист «%s»: RTSP URL не найден (ожидается ячейка с rtsp://...)", title)
            continue

        cameras.append(
            CameraRecord(
                sheet_id=sheet_id,
                sheet_title=title,
                rtsp_url=url,
                cell_a1=cell,
            )
        )

    cameras.sort(key=lambda c: c.sheet_title.lower())
    return SheetsState(
        cameras=cameras,
        last_error=None,
        updated_at_iso=datetime.now(timezone.utc).isoformat(),
    )
