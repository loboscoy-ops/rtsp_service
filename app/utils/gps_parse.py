from __future__ import annotations

import re


def parse_lat_lon(text: str | None) -> tuple[float, float] | None:
    """Достаёт пару (широта, долгота) из строки вроде «55.7522, 37.6156» или «55.7522; 37.6156».

    Поддерживает десятичную запятую в числах. Если порядок явно перепутан
    (lat вне [-90,90]), пробуем поменять местами.
    """
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None

    # Явное разделение запятой или точкой с запятой между двумя числами
    for sep in (";", ","):
        if sep in raw:
            parts = raw.split(sep, 1)
            if len(parts) == 2:
                a = _to_float(parts[0])
                b = _to_float(parts[1])
                if a is not None and b is not None:
                    return _normalize_pair(a, b)

    # Два первых десятичных числа подряд
    nums = re.findall(r"-?\d+(?:[.,]\d+)?", raw)
    if len(nums) >= 2:
        a = _to_float(nums[0])
        b = _to_float(nums[1])
        if a is not None and b is not None:
            return _normalize_pair(a, b)
    return None


def _to_float(fragment: str) -> float | None:
    try:
        return float(fragment.strip().replace(",", "."))
    except ValueError:
        return None


def _normalize_pair(a: float, b: float) -> tuple[float, float] | None:
    lat, lon = a, b
    if abs(lat) > 90 or abs(lon) > 180:
        lat, lon = lon, lat
    if abs(lat) > 90 or abs(lon) > 180:
        return None
    return lat, lon
