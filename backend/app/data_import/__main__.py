"""Data pipeline CLI.

    python -m app.data_import olle      ../data/raw/olle_course_status
    python -m app.data_import hayoung   ../data/raw/hayoung_olle --inspect
    python -m app.data_import hayoung   ../data/raw/hayoung_olle
    python -m app.data_import poi       ../data/raw/poi [--geocode]
    python -m app.data_import all       ../data/raw          (runs every dataset that is present)
"""
import argparse
import json
import sys
from pathlib import Path

from ..db import Base, SessionLocal, engine
from ..services.route_service import seed_routes
from . import hayoung_shp, olle_csv, poi_csv
from .common import ImportError_


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.data_import")
    ap.add_argument("kind", choices=["olle", "hayoung", "poi", "all"])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--inspect", action="store_true", help="print the SHP schema without loading")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--license", help="이용 조건 (예: 공공누리 제1유형)")
    ap.add_argument("--code-field")
    ap.add_argument("--name-field")
    ap.add_argument("--course-field")
    ap.add_argument("--geocode", action="store_true")
    a = ap.parse_args(argv)

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_routes(db)
        jobs = []
        if a.kind == "all":
            for sub, kind in (("olle_course_status", "olle"), ("hayoung_olle", "hayoung"), ("poi", "poi")):
                d = a.folder / sub
                if d.exists() and any(p.suffix in (".csv", ".shp") for p in d.iterdir()):
                    jobs.append((kind, d))
        else:
            jobs.append((a.kind, a.folder))
        results = {}
        for kind, folder in jobs:
            try:
                if kind == "olle":
                    results[kind] = olle_csv.run(db, folder, license=a.license, dry_run=a.dry_run)
                elif kind == "hayoung" and a.inspect:
                    results[kind] = hayoung_shp.inspect(folder)
                elif kind == "hayoung":
                    results[kind] = hayoung_shp.run(db, folder, code_field=a.code_field, name_field=a.name_field,
                                                    course_field=a.course_field, license=a.license, dry_run=a.dry_run)
                else:
                    results[kind] = poi_csv.run(db, folder, geocode=a.geocode, license=a.license, dry_run=a.dry_run)
            except ImportError_ as e:
                results[kind] = {"error": str(e)}
        print(json.dumps(results or {"error": "가져올 데이터가 없습니다"}, ensure_ascii=False, indent=1, default=str))
        return 1 if any("error" in v for v in results.values()) or not results else 0


if __name__ == "__main__":
    sys.exit(main())
