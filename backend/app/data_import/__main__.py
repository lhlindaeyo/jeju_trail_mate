"""Data pipeline CLI (run from backend/).

    python -m app.data_import all          ../data/raw     # every dataset present + both Open APIs
    python -m app.data_import olle         ../data/raw/olle_course_status
    python -m app.data_import hayoung      ../data/raw/hayoung_olle [--inspect]
    python -m app.data_import poi          ../data/raw/poi           # CSV files in the folder
    python -m app.data_import goodprice                              # 서귀포시 착한가격업소 OpenAPI (no key)
    python -m app.data_import wifi-api     [--dongs 서홍동,동홍동]     # 제주데이터허브 OpenAPI (JEJU_DATAHUB_APPKEY)
    python -m app.data_import locate-waypoints                       # place Hayoung waypoints on the route
    python -m app.data_import juso-inspect [folder]                  # check the downloaded 위치정보 요약DB (default data/raw/juso_entrc)
    python -m app.data_import geocode --address "서귀포시 중앙로 62번길 5"   # try every provider in GEOCODER_CHAIN
    python -m app.data_import sbiz-fetch [--sigungu 50130 --classes I1,I2,S2]   # 상가정보 API → data/raw/stores/*.json
    (그 뒤 `goodprice`를 다시 실행하면 상호·주소가 맞는 업소에 좌표가 채워집니다)

Keys are read from the environment or backend/.env (JEJU_DATAHUB_APPKEY, VWORLD_API_KEY, KAKAO_REST_API_KEY).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from ..config import get_settings
from ..db import Base, SessionLocal, engine
from ..services.route_service import seed_routes
from ..services.tour_service import seed_tour_source
from . import hayoung_shp, locate_waypoints, olle_csv, poi_csv, poi_sources
from .common import ImportError_


def _export_keys() -> None:
    s = get_settings()
    for env, val in (("JEJU_DATAHUB_APPKEY", s.jeju_datahub_appkey), ("KAKAO_REST_API_KEY", s.kakao_rest_api_key)):
        if val and not os.environ.get(env):
            os.environ[env] = val


def _poi_folder(db, folder: Path, a) -> dict:
    out = {}
    for f in sorted(folder.glob("*.csv")):
        if "와이파이" in f.name or "wifi" in f.name.lower():
            out[f.name] = poi_sources.wifi_csv(db, f, license=a.license, dry_run=a.dry_run)
        else:
            out[f.name] = poi_csv.run(db, f.parent, geocode=a.geocode, license=a.license, dry_run=a.dry_run, files=[f])
    hwp = [f.name for f in folder.glob("*.hwp")]
    if hwp:
        out["guides"] = {"files": hwp, "note": "HWP 활용가이드는 데이터가 아니라 API 사용법 — goodprice / wifi-api 명령으로 적재"}
    return out or {"error": "CSV가 없습니다"}


def _geocode_tools(a) -> dict:
    from ..geocode import get_geocoder
    from ..geocode.base import GeocoderError
    from ..geocode.local_juso import inspect as juso_inspect

    if a.kind == "juso-inspect":
        return juso_inspect(a.folder or get_settings().juso_entrc_dir)
    if a.kind == "sbiz-fetch":
        from . import sbiz_fetch

        s = get_settings()
        return sbiz_fetch.fetch(s.sbiz_service_key or s.kma_service_key, Path(a.folder or s.stores_dir),
                                sigungu=a.sigungu, classes=tuple(c.strip() for c in a.classes.split(",") if c.strip()))
    if not a.address:
        return {"error": "--address 가 필요합니다"}
    chain = get_geocoder()
    if not chain:
        return {"error": "사용 가능한 지오코더가 없습니다 (위치정보 요약DB 또는 VWORLD_API_KEY 필요)"}
    out: dict = {}
    for p in chain.providers:
        try:
            hit = p.geocode_address(a.address)
            places = [] if hit else p.search_place(a.address)
            out[p.name] = {"storable": p.storable,
                           "address": f"{hit.lat:.6f}, {hit.lon:.6f}" if hit else None,
                           "places": [f"{h.label} {h.lat:.6f}, {h.lon:.6f}" for h in places]}
        except GeocoderError as e:
            out[p.name] = {"error": str(e)}
        except Exception as e:
            out[p.name] = {"error": f"{type(e).__name__}: {e}"}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.data_import")
    ap.add_argument("kind", choices=["olle", "hayoung", "poi", "goodprice", "wifi-api", "locate-waypoints", "all",
                                     "juso-inspect", "geocode", "sbiz-fetch"])
    ap.add_argument("folder", type=Path, nargs="?")
    ap.add_argument("--address", help="geocode: 주소 또는 장소명")
    ap.add_argument("--sigungu", default="50130", help="sbiz-fetch: 시군구코드 (서귀포시 50130, 제주시 50110)")
    ap.add_argument("--classes", default="I1,I2,S2", help="sbiz-fetch: 업종 대분류 (I1 숙박, I2 음식, S2 개인서비스)")
    ap.add_argument("--inspect", action="store_true", help="print the SHP schema without loading")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--license", help="이용 조건 (예: 공공누리 제1유형)")
    ap.add_argument("--geocode", action="store_true", help="CSV 주소를 카카오로 지오코딩")
    ap.add_argument("--dongs", help="wifi-api 행정동 목록(쉼표 구분)")
    a = ap.parse_args(argv)
    _export_keys()

    if a.kind in ("juso-inspect", "geocode", "sbiz-fetch"):
        sys.stdout.reconfigure(encoding="utf-8")
        try:
            result = _geocode_tools(a)
        except ImportError_ as e:
            result = {"error": str(e)}
        print(json.dumps(result, ensure_ascii=False, indent=1, default=str))
        return 1 if "error" in result else 0

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_routes(db)
        seed_tour_source(db)
        jobs: list[tuple[str, Path | None]] = []
        if a.kind == "all":
            root = a.folder or Path("../data/raw")
            for sub, kind in (("olle_course_status", "olle"), ("hayoung_olle", "hayoung"), ("poi", "poi")):
                d = root / sub
                if d.exists() and any(p.suffix in (".csv", ".shp") for p in d.iterdir()):
                    jobs.append((kind, d))
            jobs.append(("goodprice", None))
            if os.environ.get("JEJU_DATAHUB_APPKEY"):
                jobs.append(("wifi-api", None))
            jobs.append(("locate-waypoints", None))
        else:
            if a.kind in ("olle", "hayoung", "poi") and not a.folder:
                ap.error("folder is required")
            jobs.append((a.kind, a.folder))
        results = {}
        for kind, folder in jobs:
            try:
                if kind == "olle":
                    results[kind] = olle_csv.run(db, folder, license=a.license, dry_run=a.dry_run)
                elif kind == "hayoung" and a.inspect:
                    results[kind] = hayoung_shp.inspect(folder)
                elif kind == "hayoung":
                    results[kind] = hayoung_shp.run(db, folder, license=a.license, dry_run=a.dry_run)
                elif kind == "poi":
                    results[kind] = _poi_folder(db, folder, a)
                elif kind == "goodprice":
                    results[kind] = poi_sources.goodprice_api(db, license=a.license, dry_run=a.dry_run)
                elif kind == "wifi-api":
                    dongs = [d.strip() for d in a.dongs.split(",")] if a.dongs else None
                    results[kind] = poi_sources.wifi_api(db, dongs=dongs, license=a.license, dry_run=a.dry_run)
                elif kind == "locate-waypoints":
                    results[kind] = locate_waypoints.run(db, dry_run=a.dry_run)
            except ImportError_ as e:
                db.rollback()
                results[kind] = {"error": str(e)}
            except Exception as e:  # network errors etc. — report and continue with the other jobs
                db.rollback()
                results[kind] = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(results or {"error": "가져올 데이터가 없습니다"}, ensure_ascii=False, indent=1, default=str))
        return 1 if any(isinstance(v, dict) and "error" in v for v in results.values()) or not results else 0


if __name__ == "__main__":
    sys.exit(main())
