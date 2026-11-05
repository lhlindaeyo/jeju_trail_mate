"""제주특별자치도_올레코스현황 CSV → trail_routes (jeju_olle). No geometry in this dataset."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import TrailRoute
from .common import ImportError_, pick, read_csv, register

SOURCE_ID = "olle_course_status"


def parse_duration(text: str) -> tuple[int | None, int | None]:
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    if not nums:
        return None, None
    lo, hi = nums[0], nums[-1]
    return lo * 60, hi * 60


def parse_distance_and_duration(dist: str, dur: str | None) -> tuple[float | None, int | None, int | None]:
    """Handles separate columns, and the glued preview form '15.14~5시간' (= 15.1 km, 4~5h)."""
    if dur:
        m = re.search(r"\d+(\.\d+)?", dist or "")
        return (float(m.group()) if m else None, *parse_duration(dur))
    m = re.match(r"\s*(\d+\.\d)(\d+)\s*~\s*(\d+)", dist or "")
    if m:
        return float(m.group(1)), int(m.group(2)) * 60, int(m.group(3)) * 60
    m = re.search(r"\d+(\.\d+)?", dist or "")
    return (float(m.group()) if m else None, None, None)


def normalize_course_no(text: str) -> str:
    t = text.replace("코스", "").replace("올레", "").replace(" ", "")
    m = re.search(r"\d+(-\d+|-?[AB])?", t, re.I)
    if not m:
        raise ImportError_(f"코스 번호를 읽지 못했습니다: {text!r}")
    no = m.group().upper()
    return re.sub(r"^(\d+)([AB])$", r"\1-\2", no)


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    files = sorted(folder.glob("*.csv"))
    if len(files) != 1:
        raise ImportError_(f"{folder}에 CSV가 정확히 1개 있어야 합니다(현재 {len(files)}개)")
    rows = read_csv(files[0])
    cols = list(rows[0])
    c_no = pick(cols, "코스번호", "코스명", "코스")
    c_dist = pick(cols, "거리", "길이")
    c_dur = pick(cols, "소요시간", "시간")
    c_start = pick(cols, "시점", "시작", "출발")
    c_end = pick(cols, "종점", "도착", "끝")
    if not (c_no and c_dist):
        raise ImportError_(f"필수 컬럼(코스, 거리)을 찾지 못했습니다. 컬럼: {cols}")
    if c_dur == c_dist:
        c_dur = None
    report = {"file": files[0].name, "columns": cols, "rows": len(rows), "updated": 0, "created": 0, "problems": []}
    for r in rows:
        try:
            no = normalize_course_no(r[c_no])
        except ImportError_ as e:
            report["problems"].append(str(e))
            continue
        km, lo, hi = parse_distance_and_duration(r[c_dist], r.get(c_dur) if c_dur else None)
        if km is None:
            report["problems"].append(f"{no}: 거리 파싱 실패 {r[c_dist]!r}")
        if dry_run:
            continue
        route = db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle", TrailRoute.course_no == no))
        if route is None:
            route = TrailRoute(trail_type="jeju_olle", operator="사단법인 제주올레", course_no=no, name=f"제주올레 {no}코스",
                               source_url="https://www.jejuolle.org/trail#/")
            db.add(route)
            report["created"] += 1
        else:
            report["updated"] += 1
            route.version = (route.version or 1) + 1
        route.distance_km = km
        route.duration_min_low, route.duration_min_high = lo, hi
        if c_start and r.get(c_start):
            route.start_point = r[c_start]
        if c_end and r.get(c_end):
            route.end_point = r[c_end]
        route.source_id = SOURCE_ID
        route.verification_status = "unverified"  # dataset is dated; admin verifies against the official site
    if not dry_run:
        register(db, SOURCE_ID, files, license=license)
        db.commit()
    return report
