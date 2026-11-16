"""제주 공중화장실 → poi_places (category "toilet"), for the 앞길 브리핑 '반경 2km 화장실'.

Sources, best first — the first one present in data/raw/toilets/ is loaded and replaces the others:
  1. 행정안전부 전국공중화장실표준데이터 CSV (공공데이터포털 15012892, 이용허락범위 제한 없음),
     downloaded by hand into the folder — any *.csv with 화장실명 + 위도/경도 columns;
  2. the same data through its standard-data OpenAPI (tn_pubr_public_toilet_api), fetched by
     `toilets-fetch` once the 공공데이터포털 key is approved for it (활용신청);
  3. OpenStreetMap amenity=toilets in Jeju (ODbL), fetched by `toilets-fetch` without a key — an interim
     until the official list is in.
Only places inside Jeju (주소 '제주특별자치도' or the island bounding box) are kept."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import PoiPlace
from .common import ImportError_, pick, read_csv, register
from .olle_osm import OVERPASS

STD_SOURCE, OSM_SOURCE = "public_toilet_std", "osm_toilets"
STD_API = "https://api.data.go.kr/openapi/tn_pubr_public_toilet_api"
API_FILE, OSM_FILE = "public_toilets_api.json", "osm_toilets.json"
JEJU = (33.1, 126.1, 34.05, 127.0)  # incl. 추자도
UA = {"User-Agent": "trail-data-import/0.1"}
OSM_QUERY = '[out:json][timeout:120];nwr["amenity"="toilets"](33.1,126.1,34.05,127.0);out center tags;'


def _in_jeju(lat: float | None, lon: float | None) -> bool:
    return lat is not None and lon is not None and JEJU[0] <= lat <= JEJU[2] and JEJU[1] <= lon <= JEJU[3]


def _f(v) -> float | None:
    try:
        x = float(str(v).strip())
        return x if x else None
    except (TypeError, ValueError):
        return None


# --- fetch -------------------------------------------------------------------------------
def fetch(folder: Path, key: str | None, *, client: httpx.Client | None = None) -> dict:
    """Official API when the key is approved for it, otherwise OpenStreetMap."""
    folder.mkdir(parents=True, exist_ok=True)
    c = client or httpx.Client(timeout=60, headers=UA)
    out: dict = {}
    try:
        if key:
            try:
                rows = _fetch_std_api(c, key)
                (folder / API_FILE).write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "rows": rows},
                                                          ensure_ascii=False), encoding="utf-8")
                out["official_api"] = {"jeju_rows": len(rows)}
            except ImportError_ as e:
                out["official_api"] = {"error": str(e)}
        data = None
        for url in OVERPASS:
            try:
                data = c.post(url, data={"data": OSM_QUERY}).raise_for_status().json()
                break
            except (httpx.HTTPError, ValueError):
                continue
        if data is None:
            out["osm"] = {"error": "Overpass 서버가 응답하지 않습니다"}
        else:
            data["fetched_at"] = datetime.now(timezone.utc).isoformat()
            (folder / OSM_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            out["osm"] = {"elements": len(data.get("elements", []))}
    finally:
        if client is None:
            c.close()
    return out


def _fetch_std_api(c: httpx.Client, key: str) -> list[dict]:
    rows, page, size = [], 1, 1000
    while True:
        r = c.get(STD_API, params={"serviceKey": key, "pageNo": page, "numOfRows": size, "type": "json"})
        if r.status_code != 200 or not r.text.lstrip().startswith("{"):
            msg = "등록되지 않은 서비스키 — 공공데이터포털에서 '전국공중화장실표준데이터' 활용신청 필요" if "NOT_REGISTERED" in r.text \
                else f"HTTP {r.status_code}"
            raise ImportError_(f"공중화장실 표준데이터 API: {msg}")
        body = (r.json().get("response") or {}).get("body") or {}
        items = body.get("items") or []
        items = items.get("item", []) if isinstance(items, dict) else items
        rows += [i for i in items if "제주" in f"{i.get('rdnmadr', '')}{i.get('lnmadr', '')}" or _in_jeju(_f(i.get("latitude")), _f(i.get("longitude")))]
        total = int(body.get("totalCount") or 0)
        if page * size >= total or not items:
            return rows
        page += 1
        time.sleep(0.2)


# --- load --------------------------------------------------------------------------------
def _std_place(name, addr, lat, lon, open_time, kind, phone, now) -> PoiPlace:
    detail = " · ".join(x for x in (kind, f"개방 {open_time}" if open_time else None) if x) or None
    return PoiPlace(name=name, category="toilet", address=addr or None, lat=lat, lon=lon, geo_precision="source",
                    detail=detail, phone=phone or None, source_id=STD_SOURCE, checked_at=now,
                    external_key=f"std:{name}|{addr}"[:200])


def _from_csv(path: Path, now) -> list[PoiPlace]:
    rows = read_csv(path)
    cols = list(rows[0])
    c_name, c_lat, c_lon = pick(cols, "화장실명"), pick(cols, "WGS84위도", "위도"), pick(cols, "WGS84경도", "경도")
    if not (c_name and c_lat and c_lon):
        raise ImportError_(f"{path.name}: 화장실명/위도/경도 컬럼을 찾지 못했습니다. 컬럼: {cols}")
    c_road, c_lot = pick(cols, "소재지도로명주소", "도로명주소"), pick(cols, "소재지지번주소", "지번주소")
    c_open, c_kind, c_tel = pick(cols, "개방시간상세", "개방시간"), pick(cols, "구분"), pick(cols, "전화번호")
    out = []
    for r in rows:
        lat, lon = _f(r.get(c_lat)), _f(r.get(c_lon))
        addr = (r.get(c_road) if c_road else "") or (r.get(c_lot) if c_lot else "")
        if not (("제주" in addr) or _in_jeju(lat, lon)) or not _in_jeju(lat, lon):
            continue
        out.append(_std_place(r[c_name], addr, lat, lon, r.get(c_open) if c_open else None, r.get(c_kind) if c_kind else None,
                              r.get(c_tel) if c_tel else None, now))
    return out


def _from_api(rows: list[dict], now) -> list[PoiPlace]:
    out = []
    for i in rows:
        lat, lon = _f(i.get("latitude")), _f(i.get("longitude"))
        if not _in_jeju(lat, lon):
            continue
        out.append(_std_place(i.get("toiletNm") or "공중화장실", i.get("rdnmadr") or i.get("lnmadr"), lat, lon,
                              i.get("openTimeDetail") or i.get("openTime"), i.get("toiletType") or i.get("se"), i.get("phoneNumber"), now))
    return out


def _from_osm(data: dict, now) -> list[PoiPlace]:
    out = []
    for e in data.get("elements", []):
        lat = e.get("lat") or (e.get("center") or {}).get("lat")
        lon = e.get("lon") or (e.get("center") or {}).get("lon")
        if not _in_jeju(lat, lon):
            continue
        t = e.get("tags") or {}
        if t.get("access") in ("private", "no", "customers"):
            continue
        detail = " · ".join(x for x in (f"개방 {t['opening_hours']}" if t.get("opening_hours") else None,
                                        "유료" if t.get("fee") == "yes" else None) if x) or None
        out.append(PoiPlace(name=t.get("name:ko") or t.get("name") or "공중화장실", category="toilet", lat=lat, lon=lon,
                            geo_precision="source", detail=detail, source_id=OSM_SOURCE, checked_at=now,
                            external_key=f"osm:{e['type']}/{e['id']}"))
    return out


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    now = datetime.now(timezone.utc)
    csvs = sorted(folder.glob("*.csv")) if folder.exists() else []
    if csvs:
        places, source, files = _from_csv(csvs[-1], now), STD_SOURCE, [csvs[-1]]
    elif (folder / API_FILE).exists():
        places, source, files = _from_api(json.loads((folder / API_FILE).read_text(encoding="utf-8"))["rows"], now), STD_SOURCE, [folder / API_FILE]
    elif (folder / OSM_FILE).exists():
        places, source, files = _from_osm(json.loads((folder / OSM_FILE).read_text(encoding="utf-8")), now), OSM_SOURCE, [folder / OSM_FILE]
    else:
        raise ImportError_(f"{folder}에 화장실 데이터가 없습니다 — `python -m app.data_import toilets-fetch` 를 먼저 실행하거나 표준데이터 CSV를 넣으세요")
    if not places:
        raise ImportError_(f"{files[0].name}: 제주 화장실을 찾지 못했습니다")
    report = {"file": files[0].name, "source": source, "places": len(places)}
    if not dry_run:
        # one toilet source at a time: the official list replaces the OSM interim and vice versa
        db.execute(delete(PoiPlace).where(PoiPlace.source_id.in_([STD_SOURCE, OSM_SOURCE])))
        db.add_all(places)
        register(db, source, files, license=license, ref_date=now.date().isoformat())
        db.commit()
    return report
