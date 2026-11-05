"""서귀포시 하영올레 SHP → route_segments / route_waypoints / trail_routes.path (v1.2 §04 A2, §05).

The SHP attribute schema has not been confirmed yet, so the importer:
  * validates the file set and reads CRS (.prj) and encoding (.cpg),
  * reprojects everything to WGS84 (EPSG:4326),
  * auto-detects code / name / course columns (override with --code-field etc.),
  * handles POINT layers (waypoints) and LINE layers (one line per leg ending at a waypoint).
Run with --inspect first to print the schema and a few records."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RouteSegment, TrailRoute
from ..services.geo import path_length_m, project_on_path
from .common import ImportError_, register

SOURCE_ID = "hayoung_shp"
REQUIRED = [".shp", ".shx", ".dbf", ".prj"]
RECOMMENDED = [".cpg", ".qmd"]
CODE_RE = re.compile(r"^\s*([123])\s*(S|F|\d{1,2})\s*$", re.I)
ENC_ALIASES = {"UTF-8": "utf-8", "UTF8": "utf-8", "65001": "utf-8", "949": "cp949", "EUC-KR": "cp949", "CP949": "cp949"}


def _files(folder: Path) -> tuple[Path, list[Path], list[str]]:
    shps = sorted(folder.glob("*.shp"))
    if len(shps) != 1:
        raise ImportError_(f"{folder}에 .shp 파일이 정확히 1개 있어야 합니다(현재 {len(shps)}개)")
    stem = shps[0].with_suffix("")
    missing = [ext for ext in REQUIRED if not stem.with_suffix(ext).exists()]
    if missing:
        raise ImportError_(f"SHP 구성 파일이 빠졌습니다: {', '.join(missing)} — 6개 파일을 모두 받아 주세요")
    warnings = [f"{ext} 없음 (인코딩/메타데이터 추정)" for ext in RECOMMENDED if not stem.with_suffix(ext).exists()]
    files = [p for p in folder.iterdir() if p.with_suffix("") == stem]
    return shps[0], files, warnings


def _reader(shp: Path):
    import shapefile  # pyshp

    cpg = shp.with_suffix(".cpg")
    raw = cpg.read_text(errors="ignore").strip() if cpg.exists() else "cp949"
    enc = ENC_ALIASES.get(raw.upper(), raw or "cp949")
    return shapefile.Reader(str(shp), encoding=enc), enc


def _transformer(shp: Path):
    from pyproj import CRS, Transformer

    crs = CRS.from_wkt(shp.with_suffix(".prj").read_text(errors="ignore"))
    return Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True), crs


def _detect(fields: list[str], override: str | None, *needles: str) -> str | None:
    if override:
        if override not in fields:
            raise ImportError_(f"필드 {override!r}가 없습니다. 필드: {fields}")
        return override
    for n in needles:
        for f in fields:
            if n in f.lower():
                return f
    return None


def inspect(folder: Path) -> dict:
    shp, files, warnings = _files(folder)
    r, enc = _reader(shp)
    _t, crs = _transformer(shp)
    fields = [f[0] for f in r.fields[1:]]
    sample = [dict(zip(fields, rec)) for rec in r.records()[:8]]
    shape_type, count = r.shapeTypeName, len(r)
    r.close()
    return {"file": shp.name, "files": sorted(p.name for p in files), "warnings": warnings, "encoding": enc,
            "crs": crs.name, "epsg": crs.to_epsg(), "shape_type": shape_type, "count": count,
            "fields": fields, "sample": sample}


def _gap(a, b) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _order(k: str) -> int:
    return -1 if k == "S" else 999 if k == "F" else int(k)


def run(db: Session, folder: Path, *, code_field: str | None = None, name_field: str | None = None,
        course_field: str | None = None, license: str | None = None, dry_run: bool = False) -> dict:
    shp, files, warnings = _files(folder)
    r, enc = _reader(shp)
    tf, crs = _transformer(shp)
    fields = [f[0] for f in r.fields[1:]]
    f_code = _detect(fields, code_field, "code", "코드", "번호", "no")
    f_name = _detect(fields, name_field, "name", "명칭", "이름", "nm")
    f_course = _detect(fields, course_field, "course", "코스", "route")
    kind = r.shapeTypeName.upper()
    report = {"file": shp.name, "encoding": enc, "crs": crs.name, "shape_type": kind, "features": len(r),
              "fields": fields, "detected": {"code": f_code, "name": f_name, "course": f_course},
              "warnings": warnings, "routes": {}, "problems": []}
    if not f_code:
        r.close()
        raise ImportError_(f"경유지 코드 필드를 찾지 못했습니다. --code-field 로 지정하세요. 필드: {fields}")

    per_course: dict[str, dict[str, dict]] = {}
    for sr in r.iterShapeRecords():
        rec = dict(zip(fields, sr.record))
        raw = str(rec.get(f_code, "")).strip()
        m = CODE_RE.match(raw)
        if not m:
            report["problems"].append(f"코드 규칙(예: 1S/11/1F)과 다른 값 {raw!r} — 건너뜀")
            continue
        course = m.group(1)
        if f_course and str(rec.get(f_course, "")).strip():
            course = re.sub(r"\D", "", str(rec[f_course])) or course
        pts = [[lat, lon] for lon, lat in (tf.transform(x, y) for x, y in sr.shape.points)]
        if not pts:
            report["problems"].append(f"{raw}: 지오메트리 없음")
            continue
        per_course.setdefault(course, {})[m.group(2).upper()] = {
            "code": f"{course}{m.group(2).upper()}", "name": str(rec.get(f_name, "")).strip() if f_name else None, "pts": pts}

    r.close()  # release file handles (Windows keeps them locked otherwise)
    for course, feats in sorted(per_course.items()):
        route = db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "hayoung_olle", TrailRoute.course_no == course))
        if route is None:
            report["problems"].append(f"{course}코스: 시드에 없는 코스 — 건너뜀")
            continue
        keys = sorted(feats, key=_order)
        segments: list[list[list[float]]] = []
        if "POINT" in kind:
            coords_by_code = {feats[k]["code"]: feats[k]["pts"][0] for k in keys}
            path = list(coords_by_code.values())
            geometry_kind = "points"  # straight lines between waypoints — not a walkable path
        else:
            path, coords_by_code = [], {}
            for k in keys:
                pts = feats[k]["pts"]
                if path and _gap(pts[-1], path[-1]) < _gap(pts[0], path[-1]):
                    pts = list(reversed(pts))  # orient the leg to continue from the previous end
                segments.append(pts)
                if not path:
                    coords_by_code[f"{course}S"] = pts[0]
                path.extend(pts if not path else pts[1:])
                coords_by_code[feats[k]["code"]] = pts[-1]
            geometry_kind = "line"
        if len(path) < 2:
            report["problems"].append(f"{course}코스: 경로 점이 부족합니다")
            continue
        length = path_length_m(path)
        info = {"geometry": geometry_kind, "length_km": round(length / 1000, 2), "waypoints_located": 0, "unmatched_codes": []}
        report["routes"][course] = info
        if dry_run:
            continue
        route.segments = [RouteSegment(seq=i, coords=s, length_m=path_length_m(s), source_id=SOURCE_ID) for i, s in enumerate(segments)]
        route.path = path
        route.has_geometry = geometry_kind == "line"
        route.distance_km = round(length / 1000, 2) if route.has_geometry else None
        route.version = (route.version or 1) + 1
        along_lo = 0.0
        for w in route.waypoints:
            c = coords_by_code.get(w.code)
            if c is None:
                info["unmatched_codes"].append(w.code)
                continue
            w.lat, w.lon = c
            _dist, along = project_on_path(path, c[0], c[1], (along_lo, length))
            w.along_m = length if w.kind == "F" else along
            along_lo = w.along_m
            info["waypoints_located"] += 1
        route.start_lat, route.start_lon = path[0]
        route.end_lat, route.end_lon = path[-1]
    if not dry_run:
        register(db, SOURCE_ID, files, license=license)
        db.commit()
    return report
