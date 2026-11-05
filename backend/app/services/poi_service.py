"""Nearby places from the imported `poi_places` registry (v1.2 §04 B, §08 find_places_near).

Only places with coordinates are used for distance-based suggestions, closed businesses are
excluded, and every result carries its source and reference date. Opening status is "as
published" in the dataset — never presented as live."""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DataSource, PoiPlace
from .geo import haversine_m

CLOSED_WORDS = ("폐업", "휴업", "취소", "말소", "직권")
CATEGORY_ALIASES = {"food": ["food", "cafe"], "cafe": ["cafe"], "rest": ["rest", "cafe", "wifi"], "toilet": ["toilet"]}


def _closed(status: str | None) -> bool:
    return bool(status) and any(w in status for w in CLOSED_WORDS)


def find_places_near(db: Session, lat: float, lon: float, category: str = "food", radius_m: int = 1000, limit: int = 8) -> dict:
    cats = CATEGORY_ALIASES.get(category, [category])
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    rows = db.scalars(select(PoiPlace).where(
        PoiPlace.category.in_(cats), PoiPlace.lat.is_not(None),
        PoiPlace.lat.between(lat - dlat, lat + dlat), PoiPlace.lon.between(lon - dlon, lon + dlon)))
    places = []
    sources: dict[str, DataSource] = {}
    for p in rows:
        if _closed(p.open_status):
            continue
        d = haversine_m(lat, lon, p.lat, p.lon)
        if d > radius_m:
            continue
        if p.source:
            sources[p.source.id] = p.source
        places.append({"name": p.name, "category": p.category, "address": p.address, "distance_m": round(d),
                       "location": [p.lat, p.lon], "open_status_as_published": p.open_status,
                       "location_precision": p.geo_precision, "source_id": p.source_id})
    places.sort(key=lambda x: x["distance_m"])
    any_data = db.scalar(select(PoiPlace.id).where(PoiPlace.category.in_(cats)).limit(1)) is not None
    base = {
        "category": category,
        "radius_m": radius_m,
        "sources": [{"name": s.name, "ref_date": s.ref_date, "url": s.platform_url} for s in sources.values()],
        "caveat": "영업 상태는 데이터 기준일의 공개 자료이며 실시간 정보가 아닙니다. 방문 전 확인하세요.",
    }
    if not any_data:
        return {**base, "status": "unavailable", "places": [],
                "message": "검증된 장소 데이터가 아직 연결되지 않아 주변 장소를 안내할 수 없어요."}
    return {**base, "status": "ok", "places": places[:limit],
            "message": None if places else f"반경 {radius_m}m 안에 좌표가 확인된 장소가 없어요."}
