"""Place data from the files and Open API guides the owner downloaded (data/raw/poi):

* 제주특별자치도_공공와이파이_관광지AP정보 CSV — place name, category, address (no coordinates).
* 제주데이터허브 「제주 공공와이파이 AP 정보」 OpenAPI — same places WITH latitude/longitude.
    GET https://open.jejudatahub.net/api/proxy/Dtb18ta1btbD1Da1a81aaDttab6tDabb/{appkey}
        ?number=1&limit=100[&addressDong=서홍동][&category=관광지]
    → {"totCnt", "hasMore", "data": [{baseDate, apGroupName, installLocationDetail, category,
       categoryDetail, addressDong, addressDetail, latitude, longitude, macAddress}]}
    The appkey is the project key from 제주데이터허브 마이페이지 > 프로젝트 관리 (JEJU_DATAHUB_APPKEY).
* 서귀포시 착한가격 업소 OpenAPI V2.0 (no key, REST GET, XML)
    GET https://www.seogwipo.go.kr/openapi/goodPriceService/?page=N&pageSize=M
    → <Response><total/><page/><pageSize/><items><item> shop_code (001 음식점, 002 이미용, 003 세탁업,
       004 숙박업, 005 목욕업), shop_title, shop_tel, shop_local, shop_address, shop_goods,
       shop_holiday, shop_thumb </item></items></Response>
    No coordinates: addresses are geocoded with the local 위치정보 요약DB (data/raw/juso_entrc) when present.

Many APs share one place (several APs at 천지연폭포), so rows are merged per place name + address."""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import PoiPlace
from .common import ImportError_, pick, read_csv, register

WIFI_SOURCE, GOODPRICE_SOURCE = "jeju_wifi_ap", "seogwipo_goodprice"
WIFI_API = "https://open.jejudatahub.net/api/proxy/Dtb18ta1btbD1Da1a81aaDttab6tDabb/{appkey}"
GOODPRICE_API = "https://www.seogwipo.go.kr/openapi/goodPriceService/"
GOODPRICE_CATEGORY = {"001": "food", "002": "beauty", "003": "laundry", "004": "lodging", "005": "bath"}
UA = {"User-Agent": "JejuTrailMate/0.1 (+https://github.com/lhlindaeyo/jeju_trail_mate)"}
# 서귀포 도심(하영올레 일대) 행정동 — default scope for the Wi-Fi API pull.
SEOGWIPO_DONGS = ["서홍동", "동홍동", "정방동", "중앙동", "천지동", "송산동", "효돈동", "영천동", "대륜동", "대천동", "중문동", "예래동"]


def _key(name: str, address: str | None) -> str:
    return re.sub(r"\s+", "", f"{name}|{address or ''}")


def _replace(db: Session, source_id: str, places: list[PoiPlace]) -> None:
    db.execute(delete(PoiPlace).where(PoiPlace.source_id == source_id))
    db.add_all(places)


def geocoder():
    """address -> (lat, lon) using the configured GEOCODER_CHAIN, restricted to providers whose results
    may be stored (local 위치정보 요약DB; Kakao only with KAKAO_STORABLE=true). None when none is available."""
    from ..geocode import get_geocoder

    chain = get_geocoder()
    return chain.address_to_xy if any(p.storable for p in chain.providers) else None


# --- 공공와이파이 CSV ----------------------------------------------------------------------
def wifi_csv(db: Session, path: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    rows = read_csv(path)
    cols = list(rows[0])
    c_name, c_cat, c_addr = pick(cols, "설치장소", "장소명"), pick(cols, "카테고리"), pick(cols, "주소")
    c_date = pick(cols, "기준일자")
    if not (c_name and c_addr):
        raise ImportError_(f"{path.name}: 설치장소/주소 컬럼을 찾지 못했습니다. 컬럼: {cols}")
    now = datetime.now(timezone.utc)
    merged: dict[str, PoiPlace] = {}
    for r in rows:
        k = _key(r[c_name], r[c_addr])
        if k not in merged:
            merged[k] = PoiPlace(name=r[c_name], category="wifi", address=r[c_addr], detail=r.get(c_cat) or None,
                                 source_id=WIFI_SOURCE, checked_at=now, external_key=f"csv:{k}")
    ref = max((r.get(c_date) for r in rows if c_date and r.get(c_date)), default=None)
    if not dry_run:
        _replace(db, WIFI_SOURCE, list(merged.values()))
        register(db, WIFI_SOURCE, [path], license=license, ref_date=ref,
                 note="CSV 적재(주소만). 좌표는 제주데이터허브 OpenAPI 적재 시 채워짐.")
        db.commit()
    return {"file": path.name, "rows": len(rows), "places": len(merged), "with_coords": 0, "ref_date": ref}


# --- 공공와이파이 OpenAPI (제주데이터허브) ------------------------------------------------------
def wifi_api(db: Session, *, appkey: str | None = None, dongs: list[str] | None = None, license: str | None = None,
             client: httpx.Client | None = None, dry_run: bool = False) -> dict:
    appkey = appkey or os.environ.get("JEJU_DATAHUB_APPKEY")
    if not appkey:
        raise ImportError_("JEJU_DATAHUB_APPKEY가 없습니다. 제주데이터허브에서 데이터를 프로젝트에 담고 "
                           "마이페이지 > 프로젝트 관리의 프로젝트키를 backend/.env에 넣어 주세요.")
    c = client or httpx.Client(timeout=30, headers=UA)
    now = datetime.now(timezone.utc)
    merged: dict[str, dict] = {}
    calls = 0

    def get(params: dict) -> dict:
        nonlocal calls
        resp = c.get(WIFI_API.format(appkey=appkey), params=params)
        calls += 1
        if resp.status_code != 200:
            raise ImportError_(f"제주데이터허브 응답 {resp.status_code}: {resp.text[:200].replace(appkey, '<KEY>')}")
        return resp.json()

    # The API is a daily-snapshot history (millions of rows); one baseDate is the current
    # inventory (~3,000 APs island-wide). Take the newest baseDate from the first row.
    first = get({"number": 1, "limit": 1}).get("data", [])
    base_date = first[0].get("baseDate") if first else None
    if not base_date:
        raise ImportError_("제주데이터허브 응답에 baseDate가 없습니다")
    for dong in dongs or [None]:
        number = 1
        while True:
            params = {"number": number, "limit": 100, "baseDate": base_date}
            if dong:
                params["addressDong"] = dong
            body = get(params)
            for d in body.get("data", []):
                name = (d.get("apGroupName") or "").strip()
                try:
                    lat, lon = float(d.get("latitude")), float(d.get("longitude"))
                except (TypeError, ValueError):
                    lat = lon = None
                k = _key(name, d.get("addressDetail"))
                m = merged.setdefault(k, {"name": name, "address": (d.get("addressDetail") or "").strip(),
                                          "category": " · ".join(dict.fromkeys(x for x in (d.get("category"), d.get("categoryDetail")) if x)),
                                          "lats": [], "lons": [], "base": d.get("baseDate")})
                if lat is not None and 33.0 < lat < 34.0 and 126.0 < lon < 127.0:
                    m["lats"].append(lat)
                    m["lons"].append(lon)
            if not body.get("hasMore") or number > 200:
                break
            number += 1
    places = [PoiPlace(name=m["name"], category="wifi", address=m["address"], detail=m["category"],
                       lat=sum(m["lats"]) / len(m["lats"]) if m["lats"] else None,
                       lon=sum(m["lons"]) / len(m["lons"]) if m["lons"] else None,
                       geo_precision="source" if m["lats"] else None, source_id=WIFI_SOURCE, checked_at=now,
                       external_key=f"api:{k}") for k, m in merged.items()]
    ref = max((m["base"] for m in merged.values() if m["base"]), default=None)
    if not dry_run:
        _replace(db, WIFI_SOURCE, places)
        src = register_api(db, WIFI_SOURCE, license=license, ref_date=ref)
        scope = ", ".join(dongs) if dongs else "제주 전역"
        src.note = f"제주데이터허브 OpenAPI 적재 ({scope}, 기준일 {base_date}). 좌표 = 같은 장소 AP 설치 위치의 평균."
        db.commit()
    return {"calls": calls, "base_date": base_date, "places": len(places),
            "with_coords": sum(1 for p in places if p.lat is not None), "ref_date": ref}


def register_api(db: Session, source_id: str, *, license: str | None, ref_date: str | None):
    from ..models import DataSource

    src = db.get(DataSource, source_id)
    if src is None:
        raise ImportError_(f"unknown data source {source_id}")
    src.downloaded_at, src.status, src.file_hash = datetime.now(timezone.utc), "imported", None
    if license:
        src.license = license
    if ref_date:
        src.ref_date = ref_date
    return src


# --- 서귀포시 착한가격 업소 OpenAPI ---------------------------------------------------------
def parse_goodprice(xml_text: str) -> tuple[int, list[dict]]:
    root = ET.fromstring(xml_text)
    total = int(root.findtext("total") or 0)
    items = [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
    return total, items


def goodprice_api(db: Session, *, license: str | None = None, client: httpx.Client | None = None,
                  geocode=None, dry_run: bool = False) -> dict:
    c = client or httpx.Client(timeout=15, headers=UA)
    page, size, items, total = 1, 50, [], None
    while True:
        resp = c.get(GOODPRICE_API, params={"page": page, "pageSize": size})
        if resp.status_code != 200:
            raise ImportError_(f"서귀포시 API 응답 {resp.status_code}")
        total, batch = parse_goodprice(resp.content.decode("utf-8", errors="replace"))
        items += batch
        if not batch or len(items) >= total or page > 40:
            break
        page += 1
    geocode = geocode if geocode is not None else geocoder()
    now = datetime.now(timezone.utc)
    places, located = [], 0
    for it in items:
        addr = it.get("shop_address") or None
        lat = lon = None
        if geocode and addr:
            hit = geocode(addr)
            if hit:
                lat, lon = hit
                located += 1
        places.append(PoiPlace(name=it.get("shop_title", ""), category=GOODPRICE_CATEGORY.get(it.get("shop_code", ""), "etc"),
                               address=addr, lat=lat, lon=lon, geo_precision="geocoded" if lat is not None else None,
                               phone=it.get("shop_tel") or None,
                               detail=" · ".join(x for x in (it.get("shop_goods"), it.get("shop_holiday")) if x) or None,
                               source_id=GOODPRICE_SOURCE, checked_at=now,
                               external_key=f"gp:{_key(it.get('shop_title', ''), addr)}"))
    if not dry_run:
        _replace(db, GOODPRICE_SOURCE, places)
        src = register_api(db, GOODPRICE_SOURCE, license=license, ref_date=now.date().isoformat())
        src.note = "서귀포시 OpenAPI 실시간 조회값을 적재" + ("(주소 지오코딩 포함)" if located else " — 좌표 없음: 위치정보 요약DB(data/raw/juso_entrc)를 넣고 다시 적재하면 거리순 추천에 쓰입니다.")
        db.commit()
    by_cat: dict[str, int] = {}
    for p in places:
        by_cat[p.category] = by_cat.get(p.category, 0) + 1
    return {"total": total, "fetched": len(items), "by_category": by_cat, "with_coords": located}
