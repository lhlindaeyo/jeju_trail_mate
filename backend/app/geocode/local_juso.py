"""Offline geocoder over the national 「도로명주소 위치정보 요약DB」 (행정안전부, 주소기반산업지원서비스,
공공누리 1유형 — 출처표시). Needs no key and no network, and its coordinates may be stored.

Put the downloaded Jeju file(s) (.txt or .zip) in data/raw/juso_entrc/ (JUSO_ENTRC_DIR).

FILE LAYOUT — written from the published record layout WITHOUT having the file at hand, so it is
verified at load time instead of trusted: pipe-separated, UTF-8 or CP949, no header, with
    3 시도명 · 4 시군구명 · 5 읍면동명 · 6 리명 · 7 산여부 · 8 번지 · 9 호 · 11 도로명 ·
    13 건물본번 · 14 건물부번 · 20 건물명 · 21 시군구용건물명 and the LAST TWO columns = 출입구 X, Y (UTM-K, EPSG:5179).
If the sanity check fails, `python -m app.data_import juso-inspect` prints what the file really looks like
and the column constants below are the only thing to adjust."""
from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path

from ..services.geo import haversine_m
from .address import parse_address  # noqa: F401  (re-exported)
from .base import GeocoderError, GeoHit, in_jeju, norm_name

log = logging.getLogger(__name__)

SIDO, SIGUNGU, EMD, RI, SAN, BUNJI, HO = 3, 4, 5, 6, 7, 8, 9
ROAD, BMAIN, BSUB, BNAME, BNAME2 = 11, 13, 14, 20, 21
MIN_COLUMNS = 24
SOURCE_CRS = "EPSG:5179"

_ROADNAME_OK = re.compile(r"^[가-힣0-9]+(?:로|길)$")


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def _read_texts(directory: Path) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for f in sorted(directory.rglob("*")):
        if f.suffix.lower() == ".txt" and f.is_file():
            out.append((f.name, _decode(f.read_bytes())))
        elif f.suffix.lower() == ".zip" and f.is_file():
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.lower().endswith(".txt"):
                        out.append((f"{f.name}:{n}", _decode(z.read(n))))
    return out


def _rows(text: str):
    for line in io.StringIO(text):
        parts = [p.strip() for p in line.rstrip("\r\n").split("|")]
        if len(parts) >= MIN_COLUMNS:
            yield parts


def _layout_ok(row: list[str]) -> bool:
    try:
        float(row[-2]), float(row[-1])
    except ValueError:
        return False
    return (row[SIDO].endswith(("도", "시")) and bool(_ROADNAME_OK.match(row[ROAD]))
            and row[BMAIN].isdigit() and row[BSUB].isdigit())


class LocalJusoGeocoder:
    name = "local"
    storable = True

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self._loaded = False
        self.road: dict[tuple, list[tuple[str, float, float]]] = {}
        self.parcel: dict[tuple, list[tuple[str, float, float]]] = {}
        self.names: dict[str, list[tuple[str, str, float, float]]] = {}
        self.count = 0

    def available(self) -> bool:
        return self.directory.is_dir() and any(f.suffix.lower() in (".txt", ".zip") for f in self.directory.rglob("*"))

    def load(self) -> None:
        if self._loaded:
            return
        from pyproj import Transformer

        texts = _read_texts(self.directory)
        if not texts:
            raise GeocoderError(f"{self.directory}에 .txt/.zip 파일이 없습니다")
        tr = Transformer.from_crs(SOURCE_CRS, "EPSG:4326", always_xy=True)
        for fname, text in texts:
            all_rows = list(_rows(text))
            rows = [r for r in all_rows if "제주" in r[SIDO]]
            if not rows:
                if any("제주" in "|".join(r[:8]) for r in all_rows[:200]):
                    raise GeocoderError(f"{fname}: 제주 행이 있지만 시도명 컬럼 위치(#{SIDO})가 예상과 다릅니다. "
                                        "`python -m app.data_import juso-inspect`로 실제 형식을 확인하세요.")
                continue  # another province's file
            sample = rows[:200]
            good = sum(_layout_ok(r) for r in sample)
            if good < 0.9 * len(sample):
                raise GeocoderError(f"{fname}: 컬럼 배치가 예상과 다릅니다({good}/{len(sample)}행만 일치). "
                                    "`python -m app.data_import juso-inspect`로 실제 형식을 확인하세요.")
            lons, lats = tr.transform([float(r[-2]) for r in rows], [float(r[-1]) for r in rows])
            kept = 0
            for r, lat, lon in zip(rows, lats, lons):
                if not in_jeju(lat, lon):
                    continue
                kept += 1
                sg = r[SIGUNGU]
                if _ROADNAME_OK.match(r[ROAD]) and r[BMAIN].isdigit():
                    self.road.setdefault((r[ROAD], int(r[BMAIN]), int(r[BSUB] or 0)), []).append((sg, lat, lon))
                if r[BUNJI].isdigit():
                    place = r[RI] or r[EMD]
                    self.parcel.setdefault((place, r[SAN] == "1", int(r[BUNJI]), int(r[HO] or 0)), []).append((sg, lat, lon))
                for nm in {r[BNAME], r[BNAME2]}:
                    if nm:
                        self.names.setdefault(norm_name(nm), []).append((nm, sg, lat, lon))
            self.count += kept
            if kept < 0.9 * len(rows):
                raise GeocoderError(f"{fname}: 좌표 {len(rows) - kept}/{len(rows)}행이 제주 범위 밖입니다. 좌표계({SOURCE_CRS}) 확인이 필요합니다.")
        if not self.count:
            raise GeocoderError(f"{self.directory}에서 제주 데이터를 찾지 못했습니다 (제주특별자치도 파일을 받았는지 확인하세요)")
        self._loaded = True
        log.info("local juso DB loaded: %d entrances", self.count)

    @staticmethod
    def _pick(entries: list[tuple[str, float, float]], sigungu: str | None) -> tuple[float, float] | None:
        cand = [e for e in entries if not sigungu or e[0] == sigungu]
        if not cand:
            return None
        lat0, lon0 = cand[0][1], cand[0][2]
        # Same road name exists in both cities: without a city in the address, refuse to guess.
        if any(haversine_m(lat0, lon0, c[1], c[2]) > 300 for c in cand[1:]):
            return None
        return lat0, lon0

    def geocode_address(self, address: str) -> GeoHit | None:
        self.load()
        p = parse_address(address)
        if not p:
            return None
        entries = self.road.get(p["road"], []) if "road" in p else self.parcel.get(p["parcel"], [])
        xy = self._pick(entries, p["sigungu"])
        return GeoHit(xy[0], xy[1], address, self.name, True, "address") if xy else None

    def search_place(self, name: str, center: tuple[float, float] | None = None, radius_m: float = 3000) -> list[GeoHit]:
        """Matches building names only (e.g. 서귀포시청). Landmarks such as waterfalls are not in this DB."""
        self.load()
        n = norm_name(name)
        if len(n) < 3:
            return []
        found = self.names.get(n) or [e for k, v in self.names.items() if (n in k or k in n) and len(k) >= 3 for e in v]
        hits = []
        for label, _sg, lat, lon in found:
            if center and haversine_m(center[0], center[1], lat, lon) > radius_m:
                continue
            hits.append(GeoHit(lat, lon, label, self.name, True, "place"))
        return hits[:3]


def inspect(directory: Path | str) -> dict:
    """What the downloaded file really looks like (for fixing the column constants)."""
    d = Path(directory)
    if not d.is_dir():
        return {"error": f"{d} 폴더가 없습니다"}
    report: dict = {"dir": str(d), "files": []}
    for fname, text in _read_texts(d):
        lines = text.splitlines()
        rows = [r for r in _rows(text)]
        jeju = [r for r in rows if "제주" in r[SIDO]] if rows else []
        report["files"].append({
            "file": fname, "lines": len(lines), "rows_with_enough_columns": len(rows), "jeju_rows": len(jeju),
            "first_lines": lines[:3],
            "layout_ok_in_first_200_jeju_rows": f"{sum(_layout_ok(r) for r in jeju[:200])}/{len(jeju[:200])}",
        })
    if not report["files"]:
        report["error"] = ".txt/.zip 파일이 없습니다"
    return report
