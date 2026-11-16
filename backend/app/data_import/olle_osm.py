"""OpenStreetMap 제주올레 route relations → trail_routes.path / route_segments (jeju_olle).

OSM is volunteer-mapped (ODbL, attribution "© OpenStreetMap contributors"): only some courses
exist, a few are old alignments (6-A/6-B) or variants (wheelchair 5코스), and members are not
always in walking order or direction. So every relation is rebuilt and checked before it is used:

  1. map the relation to a course (ref JOT6 / JOT3A, or a name like "올레길 16코스", "Ollegil 8"),
  2. chain its ways into one line, starting at the way end nearest the official start point
     (from olle_official) and always taking the next way whose end is nearest the current end,
  3. accept it only if the rebuilt length is within LENGTH_TOL of the official distance and both ends
     are within END_TOL_M of the official start/end points.

Courses that fail stay without geometry and are listed in the report — nothing is drawn for a
route that does not match the official course table."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RouteSegment, TrailRoute
from ..services.geo import haversine_m, path_length_m
from .common import ImportError_, register

SOURCE_ID = "olle_osm"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter",
            "https://maps.mail.ru/osm/tools/overpass/api/interpreter")  # public mirrors, tried in order
UA = {"User-Agent": "trail-data-import/0.1"}
FILE_NAME = "overpass_olle_relations.json"
QUERY = """[out:json][timeout:120];
(
  relation["route"~"hiking|foot"]["name"~"올레|Olle",i](33.1,126.1,34.05,127.0);
  relation["route"~"hiking|foot"]["ref"~"^JOT"](33.1,126.1,34.05,127.0);
);
out geom;"""

SKIP_ROLES = {"alternative", "excursion", "approach", "connection", "platform", "stop"}
JOIN_M = 30.0  # ends closer than this are the same point
GAP_MAX_M = 300.0  # largest hole in a relation we bridge before giving up on the rest
LENGTH_TOL = 0.12  # same tolerance as the 하영올레 SHP check
END_TOL_M = 500.0


def course_no_from_tags(tags: dict) -> str | None:
    name = " ".join(str(tags.get(k, "")) for k in ("name", "name:ko", "name:en"))
    if re.search(r"wheelchair|휠체어", name, re.I):
        return None  # a variant, not the official course
    m = re.fullmatch(r"JOT(\d+)(?:-?(\d+|[AB]))?", str(tags.get("ref", "")).strip(), re.I)
    if not m:
        m = re.search(r"(?:올레길?\s*|olle(?:gil)?\s*(?:trail)?\s*)(\d+)(?:\s*-\s*(\d+)|\s*\(?([AB])\)?)?", name, re.I)
    if not m:
        return None
    sub = next((g for g in m.groups()[1:] if g), None)
    return f"{int(m.group(1))}-{sub.upper()}" if sub else str(int(m.group(1)))


def _ways(rel: dict) -> list[list[list[float]]]:
    out = []
    for mem in rel.get("members", []):
        if mem.get("type") != "way" or mem.get("role", "") in SKIP_ROLES:
            continue
        pts = [[g["lat"], g["lon"]] for g in mem.get("geometry") or [] if g]
        if len(pts) >= 2:
            out.append(pts)
    return out


def _d(a: list[float], b: list[float]) -> float:
    return haversine_m(a[0], a[1], b[0], b[1])


def chain(ways: list[list[list[float]]], start: list[float] | None = None) -> dict:
    """Join ways end to end. Returns {path, used, unused_m, gaps_m}."""
    if not ways:
        return {"path": [], "used": [], "unused_m": 0.0, "gaps_m": []}
    left = set(range(len(ways)))
    if start:
        i, rev = min(((i, r) for i in left for r in (False, True)),
                     key=lambda ir: _d(ways[ir[0]][-1 if ir[1] else 0], start))
    else:  # orient the first member away from its neighbours
        i, rev = 0, False
        if len(ways) > 1:
            nxt = ways[1]
            rev = min(_d(ways[0][0], nxt[0]), _d(ways[0][0], nxt[-1])) < min(_d(ways[0][-1], nxt[0]), _d(ways[0][-1], nxt[-1]))
    left.remove(i)
    path = list(reversed(ways[i])) if rev else list(ways[i])
    used, gaps = [i], []
    while left:
        end = path[-1]
        j, jrev, d = min(((j, r, _d(ways[j][-1 if r else 0], end)) for j in left for r in (False, True)), key=lambda x: x[2])
        if d > GAP_MAX_M:
            break
        if d > JOIN_M:
            gaps.append(round(d))
        pts = list(reversed(ways[j])) if jrev else ways[j]
        path.extend(pts[1:] if d <= JOIN_M else pts)
        left.remove(j)
        used.append(j)
    return {"path": path, "used": used, "unused_m": sum(path_length_m(ways[k]) for k in left), "gaps_m": gaps}


def fetch(folder: Path, *, client: httpx.Client | None = None) -> dict:
    c = client or httpx.Client(timeout=180, headers=UA)
    errors, data = [], None
    try:
        for url in OVERPASS:
            try:
                data = c.post(url, data={"data": QUERY}).raise_for_status().json()
                break
            except (httpx.HTTPError, ValueError) as e:  # busy mirror (429/502/504) or an HTML error page
                errors.append(f"{url}: {type(e).__name__}")
    finally:
        if client is None:
            c.close()
    if data is None:
        raise ImportError_("Overpass 서버가 모두 응답하지 않습니다 — 잠시 후 다시 실행하세요 (" + "; ".join(errors) + ")")
    rels = [e for e in data.get("elements", []) if e.get("type") == "relation"]
    if not rels:
        raise ImportError_("Overpass에서 올레 경로를 하나도 받지 못했습니다")
    folder.mkdir(parents=True, exist_ok=True)
    data["fetched_at"] = datetime.now(timezone.utc).isoformat()
    (folder / FILE_NAME).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return {"file": str(folder / FILE_NAME), "relations": len(rels),
            "osm_timestamp": (data.get("osm3s") or {}).get("timestamp_osm_base")}


def evaluate(rel: dict, route: TrailRoute) -> dict:
    start = [route.start_lat, route.start_lon] if route.start_lat is not None else None
    end = [route.end_lat, route.end_lon] if route.end_lat is not None else None
    ways = _ways(rel)
    ch = chain(ways, start)
    path = ch["path"]
    info = {"relation": rel["id"], "name": rel.get("tags", {}).get("name"), "ways": len(ways), "used_ways": len(ch["used"]),
            "unused_km": round(ch["unused_m"] / 1000, 2), "gaps_m": ch["gaps_m"], "problems": []}
    if len(path) < 2:
        info["problems"].append("선형 멤버가 없습니다")
        return info
    length = path_length_m(path)
    info["length_km"] = round(length / 1000, 2)
    info["official_km"] = route.distance_km
    if route.distance_km:
        info["length_error"] = round(abs(length / 1000 - route.distance_km) / route.distance_km, 3)
        if info["length_error"] > LENGTH_TOL:
            info["problems"].append(f"길이 {length / 1000:.2f}km ≠ 공식 {route.distance_km}km")
    else:
        info["problems"].append("공식 거리가 없어 검증할 수 없습니다")
    if start:
        info["start_off_m"] = round(_d(path[0], start))
        if info["start_off_m"] > END_TOL_M:
            info["problems"].append(f"시작점이 공식 시작점과 {info['start_off_m']}m 떨어져 있습니다")
    if end:
        info["end_off_m"] = round(_d(path[-1], end))
        if info["end_off_m"] > END_TOL_M:
            info["problems"].append(f"종점이 공식 종점과 {info['end_off_m']}m 떨어져 있습니다")
    if not (start and end):
        info["problems"].append("공식 시작·종점 좌표가 없습니다 — olle-endpoints 를 먼저 실행하세요")
    info["_path"], info["_ways"] = path, [ways[k] for k in ch["used"]]
    return info


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    f = folder / FILE_NAME
    if not f.exists():
        raise ImportError_(f"{f} 가 없습니다 — 먼저 `python -m app.data_import olle-fetch` 를 실행하세요")
    data = json.loads(f.read_text(encoding="utf-8"))
    rels = [e for e in data.get("elements", []) if e.get("type") == "relation"]
    routes = {r.course_no: r for r in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle"))}
    report = {"file": f.name, "osm_timestamp": (data.get("osm3s") or {}).get("timestamp_osm_base"),
              "relations": len(rels), "loaded": {}, "rejected": {}, "skipped": [], "no_osm_route": []}
    candidates: dict[str, list[dict]] = {}
    for rel in rels:
        tags = rel.get("tags", {})
        no = course_no_from_tags(tags)
        label = f"relation {rel['id']} {tags.get('name') or tags.get('ref') or '(이름 없음)'}"
        if no is None:
            report["skipped"].append(f"{label}: 코스 번호를 알 수 없거나 변형 코스")
        elif no not in routes:
            report["skipped"].append(f"{label}: 공식 코스표에 없는 {no}코스")
        else:
            candidates.setdefault(no, []).append(evaluate(rel, routes[no]))

    for no, cands in candidates.items():
        ok = [c for c in cands if not c["problems"]]
        if not ok:
            report["rejected"][no] = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
            continue
        best = min(ok, key=lambda c: c.get("length_error", 1))
        report["loaded"][no] = {k: v for k, v in best.items() if not k.startswith("_")}
        if dry_run:
            continue
        route = routes[no]
        route.path = best["_path"]
        route.has_geometry = True
        route.segments = [RouteSegment(seq=i, coords=w, length_m=path_length_m(w), source_id=SOURCE_ID)
                          for i, w in enumerate(best["_ways"])]
        route.version = (route.version or 1) + 1
        # Official start/end points and distance stay as published; the line only draws the way between them.
    report["no_osm_route"] = sorted(set(routes) - set(candidates), key=_sort_key)
    report["loaded_count"], report["rejected_count"] = len(report["loaded"]), len(report["rejected"])
    if not dry_run and report["loaded"]:
        register(db, SOURCE_ID, [f], license=license, ref_date=(report["osm_timestamp"] or "")[:10] or None)
        db.commit()
    return report


def _sort_key(no: str):
    main, _, sub = no.partition("-")
    return (int(main), sub)
