"""Coordinates for a business from datasets that already list shops WITH coordinates, matched by address
(+ name). Nothing here calls a network; it reads files in data/raw/stores/ (STORES_DIR):

* 소상공인 상가(상권)정보 — CSV from data.go.kr, or the JSON written by `python -m app.data_import sbiz-fetch`
  (fields bizesNm / rdnmAdr / lat / lon).
* 지방행정 인허가 음식점·숙박 CSV (서귀포시 휴게음식점현황, 전국일반음식점표준데이터 ...) with 좌표정보(X/Y)
  in EPSG:5174, or 위도/경도 columns. Closed businesses are skipped.

Both are public data with no usage restriction noted (check the licence line on each download page and record
it in 데이터 출처), so `storable = True`.

Matching is deliberately conservative: same road address AND a similar name -> that shop's point. If the name
differs but every shop registered at that address sits within ~60 m (same building), the building's point is
used. Anything ambiguous returns None, so a wrong shop's coordinates never end up in the database."""
from __future__ import annotations

import difflib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from ..data_import.common import pick, read_csv
from ..services.geo import haversine_m
from .address import parse_address
from .base import GeoHit, in_jeju, norm_name

log = logging.getLogger(__name__)

SAME_BUILDING_M = 60.0
CLOSED_WORDS = ("폐업", "휴업", "취소", "말소")
_NOISE = re.compile(r"\(주\)|주식회사|\(유\)|(?:본|지)?점$")


@dataclass
class StoreRec:
    name: str
    address: str
    lat: float
    lon: float
    source: str


def _n(name: str | None) -> str:
    return _NOISE.sub("", norm_name(name))


def similar_names(a: str | None, b: str | None) -> bool:
    na, nb = _n(a), _n(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    if len(short) >= 2 and short in long_:
        return True
    return difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8


def _csv_records(path: Path, xy_crs: str) -> list[StoreRec]:
    rows = read_csv(path)
    cols = list(rows[0])
    c_name = pick(cols, "사업장명", "업소명", "상호명", "bizesNm")
    c_addr = pick(cols, "도로명전체주소", "도로명주소", "rdnmAdr", "소재지전체주소", "주소")
    c_lat, c_lon = pick(cols, "위도", "lat"), pick(cols, "경도", "lon")
    c_x, c_y = pick(cols, "좌표정보(x)", "좌표정보x", "X좌표"), pick(cols, "좌표정보(y)", "좌표정보y", "Y좌표")
    c_status = pick(cols, "영업상태", "상세영업상태")
    if not (c_name and c_addr and ((c_lat and c_lon) or (c_x and c_y))):
        log.warning("%s: 상호/주소/좌표 컬럼을 찾지 못해 건너뜁니다. 컬럼: %s", path.name, cols)
        return []
    transformer = None
    if c_x and c_y and not (c_lat and c_lon):
        from pyproj import Transformer

        transformer = Transformer.from_crs(xy_crs, "EPSG:4326", always_xy=True)
    out = []
    for r in rows:
        if c_status and any(w in r.get(c_status, "") for w in CLOSED_WORDS):
            continue
        try:
            if transformer:
                lon, lat = transformer.transform(float(r[c_x]), float(r[c_y]))
            else:
                lat, lon = float(r[c_lat]), float(r[c_lon])
        except (ValueError, KeyError):
            continue
        if in_jeju(lat, lon) and r.get(c_name) and r.get(c_addr):
            out.append(StoreRec(r[c_name], r[c_addr], lat, lon, path.name))
    return out


def _json_records(path: Path) -> list[StoreRec]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for it in data.get("stores", []) if isinstance(data, dict) else data:
        try:
            lat, lon = float(it["lat"]), float(it["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = " ".join(x for x in (it.get("bizesNm"), it.get("brchNm")) if x)
        addr = it.get("rdnmAdr") or it.get("lnoAdr") or ""
        if in_jeju(lat, lon) and name and addr:
            out.append(StoreRec(name, addr, lat, lon, path.name))
    return out


class StoreMatchGeocoder:
    name = "stores"
    storable = True

    def __init__(self, directory: Path | str, xy_crs: str = "EPSG:5174"):
        self.directory, self.xy_crs = Path(directory), xy_crs
        self._index: dict[tuple, list[StoreRec]] | None = None
        self.count = 0

    def _files(self) -> list[Path]:
        return sorted(f for f in self.directory.rglob("*") if f.is_file() and f.suffix.lower() in (".csv", ".json")) if self.directory.is_dir() else []

    def available(self) -> bool:
        return bool(self._files())

    def load(self) -> None:
        if self._index is not None:
            return
        index: dict[tuple, list[StoreRec]] = defaultdict(list)
        for f in self._files():
            try:
                recs = _json_records(f) if f.suffix.lower() == ".json" else _csv_records(f, self.xy_crs)
            except Exception as exc:  # one broken file must not hide the others
                log.warning("%s: %s", f.name, exc)
                continue
            for r in recs:
                p = parse_address(r.address)
                if p:
                    index[self._key(p)].append(r)
                    self.count += 1
        self._index = index
        log.info("store index: %d shops from %s", self.count, self.directory)

    @staticmethod
    def _key(p: dict) -> tuple:
        return ("road", *p["road"]) if "road" in p else ("parcel", *p["parcel"])

    def match(self, name: str | None, address: str) -> GeoHit | None:
        self.load()
        p = parse_address(address)
        if not p:
            return None
        cands = self._index.get(self._key(p), [])
        if p["sigungu"]:
            cands = [c for c in cands if p["sigungu"] in c.address or not any(s in c.address for s in ("제주시", "서귀포시"))]
        if not cands:
            return None
        for c in cands:
            if name and similar_names(name, c.name):
                return GeoHit(c.lat, c.lon, f"{c.name} [{c.source}]", self.name, True, "store")
        first = cands[0]
        if all(haversine_m(first.lat, first.lon, c.lat, c.lon) <= SAME_BUILDING_M for c in cands):
            return GeoHit(first.lat, first.lon, f"{first.name} 인근 같은 건물 [{first.source}]", self.name, True, "address")
        return None

    def geocode_business(self, name: str, address: str) -> GeoHit | None:
        return self.match(name, address)

    def geocode_address(self, address: str) -> GeoHit | None:
        return self.match(None, address)

    def search_place(self, name: str, center=None, radius_m: float = 3000) -> list[GeoHit]:
        return []  # shops are matched by address; landmark search belongs to the other providers
