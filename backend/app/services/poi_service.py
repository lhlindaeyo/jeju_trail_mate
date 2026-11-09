"""Nearby places from the imported `poi_places` registry (v1.2 §04 B, §08 find_places_near).

Sources: 서귀포시 착한가격업소 OpenAPI, 제주 공공와이파이 AP (CSV / 제주데이터허브 OpenAPI),
other CSVs. Only places with coordinates are ranked by distance; closed businesses are
excluded; every result carries its source and reference date. Nothing here is live opening
information. When a category has data but no coordinates yet, a small address-only list is
returned and labelled as such (never as "nearby")."""
from __future__ import annotations

import math

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PoiPlace
from .geo import haversine_m

CLOSED_WORDS = ("폐업", "휴업", "취소", "말소", "직권")
CATEGORY_ALIASES = {
    "food": ["food", "cafe"], "cafe": ["cafe"], "lodging": ["lodging"],
    "rest": ["rest", "wifi", "cafe"], "wifi": ["wifi"], "toilet": ["toilet"],
}
# Wi-Fi AP categories that are not places to rest (still available via category="wifi").
NOT_REST = ("버스정류소", "전기차충전소", "공항", "터미널", "의료시설", "공공기관")
CATEGORY_LABEL = {"food": "음식점", "cafe": "카페", "lodging": "숙박", "rest": "쉬어갈 곳", "wifi": "공공와이파이 장소", "toilet": "화장실"}


def _closed(status: str | None) -> bool:
    return bool(status) and any(w in status for w in CLOSED_WORDS)


GOODPRICE_SOURCE = "seogwipo_goodprice"
GOODPRICE_BADGE = "착한가격업소"


def _place(p: PoiPlace, d: float | None) -> dict:
    return {"name": p.name, "category": p.category, "address": p.address, "phone": p.phone, "detail": p.detail,
            "badge": GOODPRICE_BADGE if p.source_id == GOODPRICE_SOURCE else None,
            "distance_m": round(d) if d is not None else None, "location": [p.lat, p.lon] if p.lat is not None else None,
            "location_precision": p.geo_precision, "source_id": p.source_id}


def _sources(rows: list[PoiPlace]) -> list[dict]:
    seen = {}
    for p in rows:
        if p.source and p.source.id not in seen:
            seen[p.source.id] = {"name": p.source.name, "ref_date": p.source.ref_date, "url": p.source.platform_url}
    return list(seen.values())


def find_places_near(db: Session, lat: float, lon: float, category: str = "food", radius_m: int = 1000, limit: int = 8) -> dict:
    cats = CATEGORY_ALIASES.get(category, [category])
    dlat = radius_m / 110_540.0
    dlon = radius_m / (111_320.0 * math.cos(math.radians(lat)))
    rows = list(db.scalars(select(PoiPlace).where(
        PoiPlace.category.in_(cats), PoiPlace.lat.is_not(None),
        PoiPlace.lat.between(lat - dlat, lat + dlat), PoiPlace.lon.between(lon - dlon, lon + dlon))))
    near = [(p, haversine_m(lat, lon, p.lat, p.lon)) for p in rows if not _closed(p.open_status)
            and not (category == "rest" and p.detail and any(w in p.detail for w in NOT_REST))]
    near = sorted([x for x in near if x[1] <= radius_m], key=lambda x: x[1])
    seen_names: set[str] = set()
    near = [x for x in near if not (x[0].name in seen_names or seen_names.add(x[0].name))][:limit]  # one entry per place name
    base = {"category": category, "category_label": CATEGORY_LABEL.get(category, category), "radius_m": radius_m,
            "caveat": "영업 여부·시간은 실시간 정보가 아닙니다. 방문 전 전화나 지도 앱으로 확인하세요."}
    if near:
        return {**base, "status": "ok", "places": [_place(p, d) for p, d in near], "sources": _sources([p for p, _ in near]),
                "message": None}
    all_rows = list(db.scalars(select(PoiPlace).where(PoiPlace.category.in_(cats)).limit(400)))
    if not all_rows:
        return {**base, "status": "unavailable", "places": [], "sources": [],
                "message": "검증된 장소 데이터가 아직 연결되지 않아 주변 장소를 안내할 수 없어요."}
    if not any(p.lat is not None for p in all_rows):
        # Data exists but has no coordinates yet → address-only list, clearly not "nearby".
        listed = [p for p in all_rows if not _closed(p.open_status)][:5]
        return {**base, "status": "address_only", "places": [_place(p, None) for p in listed], "sources": _sources(listed),
                "message": "장소 데이터에 좌표가 없어 가까운 순으로 고르지 못했어요. 아래는 주소 기준 목록이에요."}
    return {**base, "status": "ok", "places": [], "sources": _sources(all_rows[:1]),
            "message": f"반경 {radius_m}m 안에 좌표가 확인된 {CATEGORY_LABEL.get(category, '장소')}가 없어요."}
