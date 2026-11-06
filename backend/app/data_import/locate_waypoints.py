"""Place Hayoung waypoints on the imported route.

The SHP has no waypoint coordinates. A waypoint is located only when an independent dataset
names it AND that location lies on the route:
  1. poi_places with coordinates (제주데이터허브 공공와이파이 AP OpenAPI: 천지연폭포, 새연교, 솜반천 …),
  2. optionally the geocoder chain's place search (GEOCODER_CHAIN: local building names, VWorld, Kakao),
     restricted to the route's surroundings. Providers whose terms forbid storing results (VWorld) only
     yield `suggestions` in the report; their coordinates are never written.
The point is snapped to the route (so check-in radius works along the walked line) and kept only
within MAX_OFFSET_M of it. Located waypoints also verify the walking direction: if most of them
appear in reverse order along the path, the path is reversed."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import PoiPlace, TrailRoute
from ..services.geo import haversine_m, path_length_m, project_on_path

MAX_OFFSET_M = 150.0


def _norm(s: str) -> str:
    return re.sub(r"[\s()（）·\-_]", "", s or "").lower()


GENERIC = {"서귀포", "서귀포시", "제주", "제주도", "공원", "광장", "주차장", "화장실"}


def _aliases(place_name: str) -> set[str]:
    """'솜반내(솜반천)' → {'솜반내솜반천', '솜반내', '솜반천'}; generic words are ignored."""
    parts = {place_name} | set(re.split(r"[()（）]", place_name))
    return {a for a in (_norm(x) for x in parts) if len(a) >= 3 and a not in GENERIC}


def _match(name: str, candidates: list[PoiPlace]) -> list[PoiPlace]:
    n = _norm(name)
    if len(n) < 2:
        return []
    return [p for p in candidates if any(a in n or n in a for a in _aliases(p.name))]


def first_pass_along(path: list[list[float]], lat: float, lon: float, after: float, slack_m: float = MAX_OFFSET_M) -> tuple[float, float] | None:
    """Earliest position ≥ `after` where the route comes within MAX_OFFSET_M of the point (a place
    such as 천지연폭포 is passed on the way out and again on the way back; list order decides)."""
    best_d, _ = project_on_path(path, lat, lon, (after, float("inf")))
    if best_d > MAX_OFFSET_M:
        return None
    acc, prev = 0.0, path[0]
    for p in path[1:]:
        seg = haversine_m(prev[0], prev[1], p[0], p[1])
        if acc + seg >= after and seg > 0:
            d, along = project_on_path([prev, p], lat, lon)
            along += acc
            if d <= min(best_d + slack_m, MAX_OFFSET_M) and along >= after:
                return d, along
        acc += seg
        prev = p
    return None


def point_at(path: list[list[float]], along: float) -> list[float]:
    acc = 0.0
    for a, b in zip(path, path[1:]):
        seg = haversine_m(a[0], a[1], b[0], b[1])
        if acc + seg >= along and seg > 0:
            t = (along - acc) / seg
            return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
        acc += seg
    return path[-1]


def _place_candidates(geocoder, name: str, center: list[float]) -> tuple[list[tuple[float, float, str]], list[tuple[float, float, str]]]:
    """(storable, lookup_only) candidates whose name matches the waypoint. Lookup-only hits (VWorld) come from
    a provider whose terms forbid storing results: they are reported as suggestions for a human to confirm."""
    storable, lookup = [], []
    for h in geocoder.search_place(f"서귀포 {name}", (center[0], center[1]), 3000):
        if _norm(name) in _norm(h.label) or _norm(h.label) in _norm(name):
            (storable if h.storable else lookup).append((h.lat, h.lon, f"{h.label} [{h.provider}]"))
    return storable, lookup


def run(db: Session, *, use_geocoder: bool = True, geocoder=None, dry_run: bool = False) -> dict:
    if use_geocoder and geocoder is None:
        from ..geocode import get_geocoder

        geocoder = get_geocoder()
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
            if not cands and use_geocoder and geocoder:
                cands, lookup = _place_candidates(geocoder, w.name, center)
                for lat, lon, label in lookup:  # not stored: shown so the owner can confirm / survey on site
                    report.setdefault("suggestions", {}).setdefault(route.name, {})[w.name] = f"{label} → {lat:.6f}, {lon:.6f}"
            best = None
            for lat, lon, label in cands:
                d, along = project_on_path(path, lat, lon)
                if d <= MAX_OFFSET_M and (best is None or d < best[0]):
                    best = (d, along, label, (lat, lon))
            if best:
                found[w.id] = best
        info = {"located": len(found), "of": sum(1 for w in route.waypoints if w.kind == "W"), "direction": "미검증", "matches": {}}
        ordered = [found[w.id][1] for w in route.waypoints if w.id in found]
        if len(ordered) == 1:
            # One located waypoint: compare where it sits in the list with where it sits on the path.
            w = next(w for w in route.waypoints if w.id in found)
            seq_frac = w.seq / max(len(route.waypoints) - 1, 1)
            along_frac = found[w.id][1] / length
            fwd, rev = abs(seq_frac - along_frac) <= 0.25, abs((1 - seq_frac) - along_frac) <= 0.25
            if fwd and not rev:
                info["direction"] = "경유지 1곳 기준 일치(약한 근거)"
            elif rev and not fwd and not dry_run:
                route.path = list(reversed(path))
                path = route.path
                found = {k: (d, length - a, lbl, src) for k, (d, a, lbl, src) in found.items()}
                info["direction"] = "경유지 1곳 기준으로 뒤집음(약한 근거)"
        if len(ordered) >= 2:
            pairs = list(zip(ordered, ordered[1:]))
            forward = sum(1 for a, b in pairs if b > a)
            if forward < len(pairs) / 2 and not dry_run:
                route.path = list(reversed(path))
                path = route.path
                found = {k: (d, length - a, lbl, src) for k, (d, a, lbl, src) in found.items()}
                info["direction"] = "경유지 순서에 맞게 뒤집음"
            else:
                info["direction"] = "경유지 순서와 일치" if forward >= len(pairs) / 2 else "역방향(드라이런)"
        # Assign positions in list order using the first pass after the previous waypoint.
        after = 0.0
        for w in route.waypoints:
            if w.id in found:
                _d0, _a0, label, (w_lat, w_lon) = found[w.id]
                hit = first_pass_along(path, w_lat, w_lon, after)
                if hit:
                    found[w.id] = (hit[0], hit[1], label, (w_lat, w_lon))
                    after = hit[1]
        for w in route.waypoints:
            if w.id in found and not dry_run:
                d, along, label, _src = found[w.id]
                w.lat, w.lon = point_at(path, along)
                w.along_m = along
                info["matches"][w.name] = f"{label} · 경로까지 {round(d)}m"
            elif w.id in found:
                info["matches"][w.name] = f"{found[w.id][2]} · 경로까지 {round(found[w.id][0])}m"
        info["order"] = [f"{w.name} {round(found[w.id][1])}m" for w in route.waypoints if w.id in found]
        report["routes"][route.name] = info
    if not dry_run:
        db.commit()
    return report
