from __future__ import annotations

from pathlib import Path

import pandas as pd

from app import config


class TemplateService:
    def generate(self, target_path: Path) -> None:
        rows = [
            {
                "object_name": "Объект 1",
                "camera_identifier": "kpp-01",
                "camera_name": "КПП въезд",
                "rtsp_url": "rtsp://user:pass@192.168.1.10:554/stream1",
                "group_name": "КПП",
                "gps_coords": "55.7522, 37.6156",
                "enabled": 1,
            },
            {
                "object_name": "Объект 1",
                "camera_identifier": "yard-01",
                "camera_name": "Двор",
                "rtsp_url": "rtsp://user:pass@192.168.1.11:554/stream1",
                "group_name": "Двор",
                "gps_coords": "55.7530, 37.6160",
                "enabled": 1,
            },
        ]
        df = pd.DataFrame(rows, columns=config.EXCEL_TEMPLATE_HEADERS)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_excel(target_path, index=False, engine="openpyxl")

