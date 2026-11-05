"""POI CSV (서귀포시 휴게음식점현황 등) → poi_places (v1.2 §04 B1).

* Closed businesses are kept with their status so the app can exclude them explicitly.
* Coordinates: uses lat/lon columns, or LOCALDATA-style 좌표정보(X/Y) (EPSG:5174 by default),
  or optional Kakao address geocoding (KAKAO_REST_API_KEY). Rows without coordinates stay
  address-only and are never used for distance-based suggestions."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import delete
from sqlalchemy.orm import Session

from ..models import PoiPlace
from .common import ImportError_, pick, read_csv, register

CAFE_WORDS = ("커피", "카페", "다방", "찻집", "제과", "베이커리", "아이스크림", "전통찻집")


def _category(kind: str | None, default: str) -> str:
    if kind and any(w in kind for w in CAFE_WORDS):
        return "cafe"
    return default


def _geocode(client: httpx.Client, key: str, address: str) -> tuple[float, float] | None:
    r = client.get("https://dapi.kakao.com/v2/local/search/address.json", params={"query": address},
                   headers={"Authorization": f"KakaoAK {key}"})
    if r.status_code != 200:
        return None
    docs = r.json().get("documents", [])
    return (float(docs[0]["y"]), float(docs[0]["x"])) if docs else None


def run(db: Session, folder: Path, *, source_id: str = "seogwipo_food", default_category: str = "food",
        xy_crs: str = "EPSG:5174", geocode: bool = False, license: str | None = None, dry_run: bool = False) -> dict:
    files = sorted(folder.glob("*.csv"))
    if not files:
        raise ImportError_(f"{folder}에 CSV가 없습니다")
    report = {"files": [f.name for f in files], "rows": 0, "with_coords": 0, "geocoded": 0, "closed": 0, "problems": []}
    key = os.environ.get("KAKAO_REST_API_KEY") if geocode else None
    if geocode and not key:
        raise ImportError_("--geocode 에는 KAKAO_REST_API_KEY 환경변수가 필요합니다")
    client = httpx.Client(timeout=8) if key else None
    transformer = None
    places: list[PoiPlace] = []
    now = datetime.now(timezone.utc)
    for f in files:
        rows = read_csv(f)
        cols = list(rows[0])
        c_name = pick(cols, "사업장명", "업소명", "상호", "장소명", "명칭", "이름")
        c_addr = pick(cols, "도로명", "소재지", "주소", "상세주소")
        c_status = pick(cols, "영업상태", "상세영업상태", "상태")
        c_kind = pick(cols, "업태", "업종", "카테고리", "분류")
        c_lat, c_lon = pick(cols, "위도"), pick(cols, "경도")
        c_x, c_y = pick(cols, "좌표정보(x)", "좌표정보x", "X좌표"), pick(cols, "좌표정보(y)", "좌표정보y", "Y좌표")
        if not c_name:
            raise ImportError_(f"{f.name}: 이름 컬럼을 찾지 못했습니다. 컬럼: {cols}")
        if c_x and c_y and not (c_lat and c_lon):
            from pyproj import Transformer

            transformer = Transformer.from_crs(xy_crs, "EPSG:4326", always_xy=True)
        for i, r in enumerate(rows):
            report["rows"] += 1
            status = r.get(c_status) if c_status else None
            if status and any(w in status for w in ("폐업", "휴업", "취소", "말소")):
                report["closed"] += 1
            lat = lon = None
            precision = None
            try:
                if c_lat and c_lon and r.get(c_lat) and r.get(c_lon):
                    lat, lon, precision = float(r[c_lat]), float(r[c_lon]), "source"
                elif transformer and r.get(c_x) and r.get(c_y):
                    lon, lat = transformer.transform(float(r[c_x]), float(r[c_y]))
                    precision = "source"
            except ValueError:
                report["problems"].append(f"{f.name}:{i + 2} 좌표 형식 오류")
            addr = r.get(c_addr) if c_addr else None
            if lat is None and client and addr:
                hit = _geocode(client, key, addr)
                if hit:
                    lat, lon, precision = hit[0], hit[1], "geocoded"
                    report["geocoded"] += 1
            if lat is not None:
                report["with_coords"] += 1
            places.append(PoiPlace(name=r[c_name], category=_category(r.get(c_kind) if c_kind else None, default_category),
                                   address=addr, lat=lat, lon=lon, geo_precision=precision, open_status=status,
                                   source_id=source_id, checked_at=now, external_key=f"{f.stem}:{i}"))
    if not dry_run:
        db.execute(delete(PoiPlace).where(PoiPlace.source_id == source_id))  # replace this source's snapshot
        db.add_all(places)
        register(db, source_id, files, license=license)
        db.commit()
    return report
