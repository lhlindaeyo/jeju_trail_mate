"""Data pipeline CLI (run from backend/).

    python -m app.data_import all          ../data/raw     # every dataset present + both Open APIs
    python -m app.data_import olle         ../data/raw/olle_course_status
    python -m app.data_import hayoung      ../data/raw/hayoung_olle [--inspect]
    python -m app.data_import poi          ../data/raw/poi           # CSV files in the folder
    python -m app.data_import goodprice                              # 서귀포시 착한가격업소 OpenAPI (no key)
    python -m app.data_import wifi-api     [--dongs 서홍동,동홍동]     # 제주데이터허브 OpenAPI (JEJU_DATAHUB_APPKEY)
    python -m app.data_import locate-waypoints                       # place Hayoung waypoints on the route

Keys are read from the environment or backend/.env (JEJU_DATAHUB_APPKEY, KAKAO_REST_API_KEY).
"""
import argparse
import json
import os
import sys
from pathlib import Path

from ..config import get_settings
from ..db import Base, SessionLocal, engine
from ..services.route_service import seed_routes
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.data_import")
    ap.add_argument("kind", choices=["olle", "hayoung", "poi", "goodprice", "wifi-api", "locate-waypoints", "all"])
    ap.add_argument("folder", type=Path, nargs="?")
    ap.add_argument("--inspect", action="store_true", help="print the SHP schema without loading")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--license", help="이용 조건 (예: 공공누리 제1유형)")
    ap.add_argument("--geocode", action="store_true", help="CSV 주소를 카카오로 지오코딩")
    ap.add_argument("--dongs", help="wifi-api 행정동 목록(쉼표 구분)")
    a = ap.parse_args(argv)
    _export_keys()

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_routes(db)
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
