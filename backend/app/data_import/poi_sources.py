"""Place data from the files and Open API guides the owner downloaded (data/raw/poi):

* 제주특별자치도_공공와이파이_관광지AP정보 CSV — place name, category, address (no coordinates).
* 서귀포시 착한가격 업소 OpenAPI V2.0 (no key, REST GET, XML)
    GET https://www.seogwipo.go.kr/openapi/goodPriceService/?page=N&pageSize=M
    → <Response><total/><page/><pageSize/><items><item> shop_code (001 음식점, 002 이미용, 003 세탁업,
       004 숙박업, 005 목욕업), shop_title, shop_tel, shop_local, shop_address, shop_goods,
       shop_holiday, shop_thumb </item></items></Response>
    No coordinates: shops are matched by address (+ name) against the local 위치정보 요약DB (data/raw/juso_entrc)
    and coordinate-bearing store lists (data/raw/stores: 소상공인 상가정보, 인허가 CSV) when present.

Many APs share one place (several APs at 천지연폭포), so rows are merged per place name + address."""
from __future__ import annotations

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
GOODPRICE_API = "https://www.seogwipo.go.kr/openapi/goodPriceService/"
GOODPRICE_CATEGORY = {"001": "food", "002": "beauty", "003": "laundry", "004": "lodging", "005": "bath"}
UA = {"User-Agent": "JejuTrailMate/0.1 (+https://github.com/lhlindaeyo/jeju_trail_mate)"}


def _key(name: str, address: str | None) -> str:
    return re.sub(r"\s+", "", f"{name}|{address or ''}")


def _replace(db: Session, source_id: str, places: list[PoiPlace]) -> None:
    db.execute(delete(PoiPlace).where(PoiPlace.source_id == source_id))
    db.add_all(places)


def geocoder():
    """(address, name=None) -> (lat, lon) using the configured GEOCODER_CHAIN, restricted to providers whose
    results may be stored (local 위치정보 요약DB, store lists; Kakao only with KAKAO_STORABLE=true).
    None when no such provider is available."""
    from ..geocode import get_geocoder

    chain = get_geocoder()
    if not any(p.storable for p in chain.providers):
        return None
    return lambda address, name=None: chain.business_to_xy(name, address)


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
            hit = geocode(addr, it.get("shop_title"))
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
        src.note = "서귀포시 OpenAPI 실시간 조회값을 적재" + ("(주소 지오코딩 포함)" if located else " — 좌표 없음: 상가정보(`sbiz-fetch`)나 위치정보 요약DB(data/raw/juso_entrc)를 넣고 다시 적재하면 거리순 추천에 쓰입니다.")
        db.commit()
    by_cat: dict[str, int] = {}
    for p in places:
        by_cat[p.category] = by_cat.get(p.category, 0) + 1
    return {"total": total, "fetched": len(items), "by_category": by_cat, "with_coords": located}
