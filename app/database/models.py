from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ObjectModel:
    id: int
    name: str
    created_at: str
    updated_at: str
    camera_count: int = 0
    online_count: int = 0
    offline_count: int = 0


@dataclass
class CameraModel:
    id: int
    object_id: int
    object_name: str
    camera_identifier: str
    camera_name: str
    group_name: str
    gps_coords: str
    uin: str
    rtsp_url: str
    enabled: bool
    status: str
    last_seen_online_at: str | None
    last_checked_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str

