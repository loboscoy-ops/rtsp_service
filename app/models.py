from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CameraRecord:
    camera_id: str
    rtsp_url: str
    project: str = ""
    name: str = ""
    camera_type: str = ""
    lat: float | None = None
    lon: float | None = None
    cell_a1: str | None = None
    legacy_sheet_title: str = ""
    source_sheet_id: int = 0
