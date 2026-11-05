"""Place Hayoung waypoints on the imported route.

The SHP has no waypoint coordinates. A waypoint is located only when an independent dataset
names it AND that location lies on the route:
  1. poi_places with coordinates (제주데이터허브 공공와이파이 AP OpenAPI: 천지연폭포, 새연교, 솜반천 …),
  2. optionally Kakao keyword search (KAKAO_REST_API_KEY), restricted to the route's surroundings.
The point is snapped to the route (so check-in radius works along the walked line) and kept only
within MAX_OFFSET_M of it. Located waypoints also verify the walking direction: if most of them
appear in reverse order along the path, the path is reversed."""
from __future__ import annotations

import os
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PoiPlace, TrailRoute
from ..services.geo import haversine_m, path_length_m, project_on_path

MAX_OFFSET_M = 150.0


def _norm(s: str) -> str:
    return re.sub(r"[\s()（）·\-_]", "", s or "").lower()


def _match(name: str, candidates: list[PoiPlace]) -> list[PoiPlace]:
    n = _norm(name)
    return [p for p in candidates if len(n) >= 2 and (n in _norm(p.name) or _norm(p.name) in n) and len(_norm(p.name)) >= 2]


def point_at(path: list[list[float]], along: float) -> list[float]:
    acc = 0.0
    for a, b in zip(path, path[1:]):
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if acc + seg >= along and seg > 0:
            t = (along - acc) / seg
            return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        acc += seg
    return path[-1]


def _kakao(name: str, center: list[float]) -> list[tuple[float, float, str]]:
    key = os.environ.get("KAKAO_REST_API_KEY")
    if not key:
        return []
    r = httpx.get("https://dapi.kakao.com/v2/local/search/keyword.json",
                  params={"query": f"서귀포 {name}", "y": center[0], "x": center[1], "radius": 3000},
                  headers={"Authorization": f"KakaoAK {key}"}, timeout=8)
    if r.status_code != 200:
        return []
    return [(float(d["y"]), float(d["x"]), d["place_name"]) for d in r.json().get("documents", [])[:3]]


def run(db: Session, *, use_kakao: bool = True, dry_run: bool = False) -> dict:
    pois = list(db.scalars(select(PoiPlace).where(PoiPlace.lat.is_not(None))))
    report: dict = {"routes": {}}
    for route in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "hayoung_olle", TrailRoute.has_geometry.is_(True))):
        path = route.path
        length = path_length_m(path)
        center = path[len(path) // 2]
        found = {}
        for w in route.waypoints:
            if w.kind != "W":
                continue
            cands = [(p.lat, p.lon, f"{p.name} ({p.source_id})") for p in _match(w.name, pois)]
            if not cands and use_kakao:
                cands = [c for c in _kakao(w.name, center) if _norm(w.name) in _norm(c[2]) or _norm(c[2]) in _norm(w.name)]
            best = None
            for lat, lon, label in cands:
                d, along = project_on_path(path, lat, lon)
                if d <= MAX_OFFSET_M and (best is None or d < best[0]):
                    best = (d, along, label)
            if best:
                found[w.id] = best
        info = {"located": len(found), "of": sum(1 for w in route.waypoints if w.kind == "W"), "direction": "미검증", "matches": {}}
        ordered = [found[w.id][1] for w in route.waypoints if w.id in found]
        if len(ordered) >= 2:
            pairs = list(zip(ordered, ordered[1:]))
            forward = sum(1 for a, b in pairs if b > a)
            if forward < len(pairs) / 2 and not dry_run:
                route.path = list(reversed(path))
                path = route.path
                found = {k: (d, length - a, lbl) for k, (d, a, lbl) in found.items()}
                info["direction"] = "경유지 순서에 맞게 뒤집음"
            else:
                info["direction"] = "경유지 순서와 일치" if forward >= len(pairs) / 2 else "역방향(드라이런)"
        for w in route.waypoints:
            if w.id in found and not dry_run:
                d, along, label = found[w.id]
                w.lat, w.lon = point_at(path, along)
                w.along_m = along
                info["matches"][w.name] = f"{label} · 경로까지 {round(d)}m"
            elif w.id in found:
                info["matches"][w.name] = f"{found[w.id][2]} · 경로까지 {round(found[w.id][0])}m"
        report["routes"][route.name] = info
    if not dry_run:
        db.commit()
    return report
