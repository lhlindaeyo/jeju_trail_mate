"""대표 관광지와 그 이야기.

The spot list is app/data/tour_spots.json. When the official 한국관광공사 TourAPI text has been fetched
(`python -m app.data_import tour-fetch` → data/raw/tour_api/kto_spots.json), each matched spot uses the
official overview (개요 — it usually carries the place's legend too) and official coordinates, with the
source 한국관광공사. Spots the API did not match keep the app's own short curated text, with its
'설화라 사실과 다를 수 있다' caveat. The tools only pass these texts on; they never write their own.

With the user's current position (and a 공공데이터포털 key), a story request about 'here' asks TourAPI
위치기반 조회 for the nearest 관광지·문화시설 and answers with their official overview — far more places
than the 18 above, e.g. the small harbours and shrines along an Olle course. Coordinates sent to the API
are rounded to ~100 m. Any API trouble falls back to the 18 spots."""
from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import DataSource
from .geo import haversine_m

TOUR_FILE = Path(__file__).resolve().parent.parent / "data" / "tour_spots.json"
OFFICIAL_FILE_NAME = "kto_spots.json"
CAVEAT = "위치는 대략적이고 운영시간·입장료는 담지 않았어요. 이야기는 전해 내려오는 설화라 사실과 다를 수 있으니 방문 전 공식 안내를 확인하세요."
OFFICIAL_CAVEAT = "한국관광공사 공식 안내문을 그대로 옮겼어요. 운영시간·입장료는 담지 않았으니 방문 전 확인하세요."
OFFICIAL_SOURCE = {"id": "kto_tourapi", "name": "한국관광공사 TourAPI 국문 관광정보 서비스",
                   "provider": "한국관광공사 (공공데이터포털 15101578)", "platform_url": "https://www.data.go.kr/data/15101578/openapi.do",
                   "ref_date": None, "license": "이용허락범위 제한 없음 · 안내문 공공누리 제1유형(출처 표시: 한국관광공사)",
                   "status": "seed", "note": "대표 관광지의 공식 개요·좌표. `python -m app.data_import tour-fetch`로 받음."}


def _first_sentence(text: str, limit: int = 120) -> str:
    first = re.split(r"(?<=[.!?다요])\s", text.strip(), maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit].rstrip() + "…"


def _official() -> dict:
    f = Path(get_settings().tour_api_dir) / OFFICIAL_FILE_NAME
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


@lru_cache(maxsize=1)
def _data() -> dict:
    data = json.loads(TOUR_FILE.read_text(encoding="utf-8"))
    off = _official()
    spots = []
    for p in data["spots"]:
        o = (off.get("spots") or {}).get(p["id"])
        if o and o.get("overview"):
            p = {**p, "lat": o["lat"], "lon": o["lon"], "summary": _first_sentence(o["overview"]), "story": o["overview"],
                 "official": True, "official_title": o["title"], "contentid": o["contentid"]}
        spots.append({**p, "official": p.get("official", False)})
    return {**data, "spots": spots, "official_fetched_at": (off.get("fetched_at") or "")[:10] or None}


def reload() -> None:
    _data.cache_clear()


def seed_tour_source(db: Session) -> None:
    for src in (_data()["data_source"], OFFICIAL_SOURCE):
        if not db.get(DataSource, src["id"]):
            db.add(DataSource(**src))
    db.commit()


def _source_fields(chosen: list[dict]) -> dict:
    d = _data()
    s = d["data_source"]
    official = [p for p in chosen if p.get("official")]
    if official and len(official) == len(chosen):
        # shown under the answer as just '출처: [name](url)' — no fetch date
        return {"source": OFFICIAL_SOURCE["name"], "source_url": OFFICIAL_SOURCE["platform_url"],
                "fetched_at": None, "caveat": OFFICIAL_CAVEAT}
    if official:
        return {"source": f"{OFFICIAL_SOURCE['name']} · {s['name']}", "source_url": OFFICIAL_SOURCE["platform_url"],
                "fetched_at": d["official_fetched_at"], "caveat": f"{OFFICIAL_CAVEAT} 공식 안내문이 없는 곳은 {CAVEAT}"}
    return {"source": s["name"], "source_url": s["platform_url"], "fetched_at": s["ref_date"], "caveat": CAVEAT}


def _brief(p: dict, d: float | None) -> dict:
    return {"id": p["id"], "name": p["name"], "area": p["area"], "summary": p["summary"], "tags": p["tags"],
            "distance_m": round(d) if d is not None else None, "location": [p["lat"], p["lon"]], "official": p["official"]}


def _ranked(lat: float | None, lon: float | None, limit: int) -> tuple[list[tuple[float | None, dict]], str]:
    spots = _data()["spots"]
    if lat is not None and lon is not None:
        ranked = sorted(((haversine_m(lat, lon, p["lat"], p["lon"]), p) for p in spots), key=lambda x: x[0])
        return ranked[:limit], "nearby"
    return [(None, p) for p in spots[:limit]], "featured"


def find_spots(lat: float | None = None, lon: float | None = None, limit: int = 5) -> dict:
    """lat/lon이 있으면 가까운 순, 없으면 파일 순서(서귀포 대표 장소 먼저)."""
    ranked, basis = _ranked(lat, lon, limit)
    note = None if basis == "nearby" else "내 위치를 몰라 거리순이 아니라 서귀포 대표 관광지부터 보여 드려요."
    return {"status": "ok", "basis": basis, "places": [_brief(p, d) for d, p in ranked], "note": note,
            **_source_fields([p for _, p in ranked])}


# --- live TourAPI lookups ----------------------------------------------------------------
API = "https://apis.data.go.kr/B551011/KorService2"
COMMON = {"MobileOS": "ETC", "MobileApp": "TrailApp", "_type": "json"}
STORY_TYPES = {"12", "14"}  # 관광지, 문화시설
NEARBY_RADIUS_M = 2000
LIST_TTL_S = 600
_list_cache: dict[tuple, tuple[float, list[dict]]] = {}
_detail_cache: dict[str, dict | None] = {}


def _http() -> httpx.Client:
    """Client factory; tests swap it for one on an httpx.MockTransport."""
    return httpx.Client(timeout=8)


def _api_key() -> str | None:
    s = get_settings()
    return s.sbiz_service_key or s.kma_service_key or None


class _ApiError(Exception):
    pass


def _items(c: httpx.Client, op: str, key: str, **params) -> list[dict]:
    try:
        r = c.get(f"{API}/{op}", params={**COMMON, **params, "serviceKey": key})
    except httpx.HTTPError as e:
        raise _ApiError(type(e).__name__) from None  # the URL holds the key — never pass the exception text on
    if r.status_code != 200 or not r.text.lstrip().startswith("{"):
        raise _ApiError(f"HTTP {r.status_code}")
    body = (r.json().get("response") or {}).get("body") or {}
    items = body.get("items") or {}
    items = items.get("item", []) if isinstance(items, dict) else []
    return items if isinstance(items, list) else [items]


def _detail(c: httpx.Client, key: str, item: dict) -> dict | None:
    from ..data_import.tour_api import clean_overview  # late import: tour_api imports this module

    cid = str(item["contentid"])
    if cid not in _detail_cache:
        d = _items(c, "detailCommon2", key, contentId=cid)
        text = clean_overview(d[0].get("overview", "")) if d else ""
        _detail_cache[cid] = {"name": (d[0].get("title") if d else None) or item.get("title"), "area": item.get("addr1") or "",
                              "story": text, "official": True} if text else None
    return _detail_cache[cid]


def nearby_official(lat: float, lon: float, radius: int = NEARBY_RADIUS_M, limit: int = 3) -> list[dict] | None:
    """Nearest 관광지·문화시설 with an official overview, nearest first. None = no key or the API failed
    (the caller falls back); [] = the API answered but nothing with a story is within `radius`."""
    key = _api_key()
    if not key:
        return None
    mx, my = round(lon, 3), round(lat, 3)  # ≈100 m — enough to find what is around, without sending the exact fix
    ck = (mx, my, radius)
    try:
        with _http() as c:
            hit = _list_cache.get(ck)
            if hit and time.monotonic() - hit[0] < LIST_TTL_S:
                items = hit[1]
            else:
                items = _items(c, "locationBasedList2", key, mapX=mx, mapY=my, radius=radius, arrange="E",
                               numOfRows=20, pageNo=1)
                items = sorted((i for i in items if str(i.get("contenttypeid")) in STORY_TYPES),
                               key=lambda i: float(i.get("dist") or 1e9))
                _list_cache[ck] = (time.monotonic(), items)
            # TourAPI's location index misses some major spots (정방폭포 is not returned even at 3 km), so the
            # 18 spots already fetched with their official text join in by real distance.
            known = [(haversine_m(lat, lon, p["lat"], p["lon"]), p) for p in _data()["spots"] if p.get("official")]
            cands = [(float(i.get("dist") or 0), "api", i) for i in items]
            cands += [(d, "spot", p) for d, p in known if d <= radius]
            seen, out = {p.get("contentid") for _, p in known}, []
            for dist, kind, x in sorted(cands, key=lambda c: c[0]):
                if kind == "api" and str(x["contentid"]) in seen:
                    continue  # same place as one of the 18
                d = _detail(c, key, x) if kind == "api" else {"name": x["name"], "area": x["area"], "story": x["story"], "official": True}
                if d:
                    out.append({**d, "distance_m": round(dist)})
                if len(out) >= limit:
                    break
            return out
    except (_ApiError, ValueError, KeyError):
        return None


def search_official(name: str) -> dict | None:
    """A place outside the 18 spots, by name: TourAPI keyword search limited to 제주 관광지·문화시설."""
    key = _api_key()
    if not key or not name.strip():
        return None
    q = re.sub(r"\s+", "", name)
    try:
        with _http() as c:
            items = [i for i in _items(c, "searchKeyword2", key, keyword=name.strip(), numOfRows=20, pageNo=1)
                     if str(i.get("contenttypeid")) in STORY_TYPES and "제주" in (i.get("addr1") or "")]
            items.sort(key=lambda i: (re.sub(r"\s+|\(.*?\)|\[.*?\]", "", i.get("title", "")) != q,
                                      not i.get("title", "").replace(" ", "").startswith(q)))
            for i in items[:3]:
                d = _detail(c, key, i)
                if d:
                    return {**d, "distance_m": None}
    except (_ApiError, ValueError, KeyError):
        return None
    return None


def get_story(spot: str | None = None, lat: float | None = None, lon: float | None = None) -> dict:
    spots = _data()["spots"]
    names = [p["name"] for p in spots]
    if spot:
        q = spot.replace(" ", "")
        hit = next((p for p in spots if q in p["name"].replace(" ", "") or p["name"].replace(" ", "") in q
                    or q.lower() == p["id"]), None)
        if hit is None:
            found = search_official(spot)
            if found:
                return {"status": "ok", "basis": "official_search", "stories": [found], **_source_fields([found])}
            return {"status": "not_found", "message": f"'{spot}'은(는) 이야기를 정리해 둔 관광지에 없어요.", "available": names,
                    **_source_fields(spots)}
        chosen = [hit]
    else:
        if lat is not None and lon is not None:
            near = nearby_official(lat, lon)
            if near:
                return {"status": "ok", "basis": "nearby_official", "stories": near, **_source_fields(near)}
        ranked = _ranked(lat, lon, 3)[0]
        # with a position, say how far each one is, so a spot 15 km away is not told as 'here'
        return {"status": "ok", "stories": [{"name": p["name"], "area": p["area"], "story": p["story"], "official": p["official"],
                                             **({"distance_m": round(d)} if d is not None else {})} for d, p in ranked],
                **_source_fields([p for _, p in ranked])}
    return {"status": "ok", "stories": [{"name": p["name"], "area": p["area"], "story": p["story"], "official": p["official"]}
                                        for p in chosen], **_source_fields(chosen)}
