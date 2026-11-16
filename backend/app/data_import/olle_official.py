"""제주올레 공식 홈페이지(jejuolle.org/trail) → trail_routes 시작점·종점 좌표 (jeju_olle).

The site has no route geometry and no course API: every course page is a compiled Vue component
inside one Road-*.js chunk, and that chunk holds a table of start/end coordinates used by the
"open in map app" buttons:  const p={"01":{start:{lat:..,lng:..},end:{lat:..,lng:..}}, 10:{...}, "15_A":{...}}

`fetch` follows /trail → trail_ko_router-*.js → Road-*.js (the hashed names change on every site
deploy), extracts that table and saves it as JSON in data/raw/olle_official/. `run` loads the JSON.
Only the coordinates are taken — photos, texts and map images on the site are not reused."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TrailRoute
from ..services.geo import haversine_m
from .common import ImportError_, register

SOURCE_ID = "olle_official_web"
BASE = "https://www.jejuolle.org"
UA = {"User-Agent": "Mozilla/5.0 (compatible; trail-data-import/0.1)"}
FILE_NAME = "road_endpoints.json"
PROFILE_FILE = Path(__file__).resolve().parents[1] / "data" / "olle_profile_km.json"
JEJU_BBOX = (33.1, 126.1, 34.05, 127.0)  # lat_min, lon_min, lat_max, lon_max — up to 추자도 (18-1, 18-2)

_ENTRY = re.compile(
    r'"?(\d{1,2}(?:_\d|_[AB])?)"?\s*:\s*\{\s*start\s*:\s*\{\s*lat\s*:\s*(-?[\d.]+)\s*,\s*lng\s*:\s*(-?[\d.]+)\s*\}\s*,'
    r'\s*end\s*:\s*\{\s*lat\s*:\s*(-?[\d.]+)\s*,\s*lng\s*:\s*(-?[\d.]+)\s*\}\s*\}')


def course_no_from_key(key: str) -> str:
    """'01' → '1', '01_1' → '1-1', '03_A' → '3-A', '10' → '10'."""
    main, _, sub = key.partition("_")
    return f"{int(main)}-{sub}" if sub else str(int(main))


def parse_endpoints(js: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for m in _ENTRY.finditer(js):
        no = course_no_from_key(m.group(1))
        s_lat, s_lon, e_lat, e_lon = (float(m.group(i)) for i in range(2, 6))
        out.setdefault(no, {"key": m.group(1), "start": [s_lat, s_lon], "end": [e_lat, e_lon]})
    return out


def _asset(text: str, prefix: str) -> str:
    m = re.search(rf"(?:assets/|\./)({re.escape(prefix)}-[A-Za-z0-9_-]+\.js)", text)
    if not m:
        raise ImportError_(f"jejuolle.org 구조가 바뀌었습니다: {prefix}-*.js 를 찾지 못했습니다")
    return f"{BASE}/assets/{m.group(1)}"


def parse_map_images(js: str) -> dict[str, str]:
    """Course key ('03_A') → URL of its PC course map image (road_03_map_pc_2026.jpg). Each course page is a Vue
    component named COURSE_<key>; the image URLs between two component names belong to the first one."""
    names = [(m.start(), m.group(1)) for m in re.finditer(r'__name:"COURSE_([0-9A-Z_]+)"', js)]
    out = {}
    for (pos, key), nxt in zip(names, [n[0] for n in names[1:]] + [len(js)]):
        m = re.search(r'https://contents\.ollepass\.org/[^"\']*?_map_pc[^"\']*?\.jpg', js[pos:nxt])
        if m:
            out[key] = m.group()
    return out


def fetch(folder: Path, *, client: httpx.Client | None = None, images: Path | None = None) -> dict:
    """Saves the start/end table. With `images`, also downloads each course's map image there (map_<key>.jpg) —
    only for tracing the route line locally (tools/olle_map_trace.py); the images are never served by the app."""
    c = client or httpx.Client(timeout=30, headers=UA, follow_redirects=True)
    saved = []
    try:
        router_url = _asset(c.get(f"{BASE}/trail").raise_for_status().text, "trail_ko_router")
        road_url = _asset(c.get(router_url).raise_for_status().text, "Road")
        js = c.get(road_url).raise_for_status().text
        courses = parse_endpoints(js)
        if images is not None and len(courses) >= 20:
            images.mkdir(parents=True, exist_ok=True)
            for key, url in parse_map_images(js).items():
                (images / f"map_{key}.jpg").write_bytes(c.get(url).raise_for_status().content)
                saved.append(key)
    finally:
        if client is None:
            c.close()
    if len(courses) < 20:
        raise ImportError_(f"Road 스크립트에서 코스 좌표를 {len(courses)}개만 찾았습니다 — 사이트 구조 변경 확인 필요")
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"source": road_url, "page": f"{BASE}/trail#/road/<course>", "fetched_at": datetime.now(timezone.utc).isoformat(),
               "courses": courses}
    (folder / FILE_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    out = {"file": str(folder / FILE_NAME), "script": road_url, "courses": len(courses)}
    if images is not None:
        out["map_images"] = len(saved)
    return out


def _in_jeju(p: list[float]) -> bool:
    return JEJU_BBOX[0] <= p[0] <= JEJU_BBOX[2] and JEJU_BBOX[1] <= p[1] <= JEJU_BBOX[3]


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    f = folder / FILE_NAME
    if not f.exists():
        raise ImportError_(f"{f} 가 없습니다 — 먼저 `python -m app.data_import olle-fetch` 를 실행하세요")
    data = json.loads(f.read_text(encoding="utf-8"))
    courses: dict[str, dict] = data.get("courses") or {}
    report = {"file": f.name, "script": data.get("source"), "courses": len(courses), "updated": [], "problems": [],
              "not_in_course_table": []}
    routes = {r.course_no: r for r in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle"))}
    for no, c in sorted(courses.items(), key=lambda kv: kv[1]["key"]):
        start, end = c["start"], c["end"]
        if not (_in_jeju(start) and _in_jeju(end)):
            report["problems"].append(f"{no}코스: 제주 밖 좌표 {start} / {end} — 건너뜀")
            continue
        route = routes.get(no)
        if route is None:
            report["not_in_course_table"].append(no)
            continue
        moved = None
        if route.start_lat is not None:
            moved = round(haversine_m(route.start_lat, route.start_lon, *start))
        report["updated"].append({"course": no, "start": start, "end": end, "start_moved_m": moved,
                                  "loop": haversine_m(*start, *end) < 50})
        if dry_run:
            continue
        route.start_lat, route.start_lon = start
        route.end_lat, route.end_lon = end
    # Current distances read off the site's elevation profiles; the 올레코스현황 CSV predates some re-routes.
    profile = json.loads(PROFILE_FILE.read_text(encoding="utf-8")) if PROFILE_FILE.exists() else {"courses": {}}
    report["distance_changed"] = []
    for no, km in profile["courses"].items():
        route = routes.get(no)
        if route is None or route.distance_km == km:
            continue
        report["distance_changed"].append({"course": no, "csv_km": route.distance_km, "site_km": km})
        if not dry_run:
            route.distance_km = km
    report["without_coordinates"] = sorted(set(routes) - set(courses))
    if not dry_run and report["updated"]:
        ref = (data.get("fetched_at") or "")[:10] or date.today().isoformat()
        register(db, SOURCE_ID, [f], license=license, ref_date=ref)
        db.commit()
    return report
