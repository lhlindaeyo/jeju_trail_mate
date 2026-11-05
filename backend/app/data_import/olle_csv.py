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


def split_endpoints(text: str) -> tuple[str | None, str | None]:
    """'시흥초등학교-광치기해변' → (start, end). Values without a single separator stay as start only."""
    t = (text or "").strip()
    if not t:
        return None, None
    parts = re.split(r"\s*[-–~]\s*", t)
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, None  # e.g. 18-2 lists the places along the course, not a start/end pair


def clean_title(text: str) -> str:
    # The published CSV is CP949, which has no en dash: '온평–표선' arrives as '온평?표선'.
    return re.sub(r"(?<=\S)\?(?=\S)", "-", text or "").strip()


def course_no_from(row_no: str, title: str) -> str:
    no = normalize_course_no(row_no)
    variant = re.search(r"\(([AB])\)\s*$", title or "") or re.search(r"(?<=[가-힣])([AB])\s*$", title or "")
    if variant and "-" not in no:
        no = f"{no}-{variant.group(1)}"
    return no


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    from ..models import DailySchedule, TrackingSession

    files = sorted(folder.glob("*.csv"))
    if len(files) != 1:
        raise ImportError_(f"{folder}에 CSV가 정확히 1개 있어야 합니다(현재 {len(files)}개)")
    rows = read_csv(files[0])
    cols = list(rows[0])
    c_no = pick(cols, "코스별", "코스번호", "코스")
    c_title = pick(cols, "코스명")
    if c_title == c_no:
        c_title = None
    c_dist = pick(cols, "거리", "길이")
    c_dur = pick(cols, "소요시간", "시간")
    c_both = pick(cols, "시종점")
    c_start = None if c_both else pick(cols, "시점", "시작", "출발")
    c_end = None if c_both else pick(cols, "종점", "도착")
    c_date = pick(cols, "기준일자", "기준일")
    if not (c_no and c_dist):
        raise ImportError_(f"필수 컬럼(코스, 거리)을 찾지 못했습니다. 컬럼: {cols}")
    if c_dur == c_dist:
        c_dur = None
    report = {"file": files[0].name, "columns": cols, "rows": len(rows), "updated": 0, "created": 0,
              "removed_stale": [], "kept_stale": [], "problems": []}
    seen: set[str] = set()
    ref_dates: set[str] = set()
    for r in rows:
        title = clean_title(r.get(c_title, "")) if c_title else ""
        try:
            no = course_no_from(r[c_no], title)
        except ImportError_ as e:
            report["problems"].append(str(e))
            continue
        if no in seen:
            report["problems"].append(f"{no}: 중복 행 — 첫 행만 사용")
            continue
        seen.add(no)
        km, lo, hi = parse_distance_and_duration(r[c_dist], r.get(c_dur) if c_dur else None)
        if km is None:
            report["problems"].append(f"{no}: 거리 파싱 실패 {r[c_dist]!r}")
        if c_date and r.get(c_date):
            ref_dates.add(r[c_date])
        if c_both:
            start_pt, end_pt = split_endpoints(r.get(c_both, ""))
        else:
            start_pt, end_pt = r.get(c_start) if c_start else None, r.get(c_end) if c_end else None
        if dry_run:
            continue
        route = db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle", TrailRoute.course_no == no))
        if route is None:
            route = TrailRoute(trail_type="jeju_olle", operator="사단법인 제주올레", course_no=no, name=f"제주올레 {no}코스",
                               source_url="https://www.jejuolle.org/trail#/", version=1)
            db.add(route)
            report["created"] += 1
        else:
            report["updated"] += 1
            route.version = (route.version or 1) + 1
        route.distance_km = km
        route.duration_min_low, route.duration_min_high = lo, hi
        if title:
            route.tags = [title]
            if c_both and not start_pt and r.get(c_both):
                route.tags = [title, f"경유: {r[c_both]}"]
        if start_pt:
            route.start_point = start_pt
        route.end_point = end_pt
        route.source_id = SOURCE_ID
        route.verification_status = "unverified"  # admin still checks against the official site
    if not dry_run and seen:
        # Courses absent from the official table (e.g. old seed '15' now split into 15-A/15-B).
        for route in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle")):
            if route.course_no in seen:
                continue
            used = db.scalar(select(DailySchedule.id).where(DailySchedule.route_id == route.id).limit(1)) or \
                db.scalar(select(TrackingSession.id).where(TrackingSession.route_id == route.id).limit(1))
            if used:
                report["kept_stale"].append(route.course_no)
            else:
                db.delete(route)
                report["removed_stale"].append(route.course_no)
        register(db, SOURCE_ID, files, license=license, ref_date=max(ref_dates) if ref_dates else None)
        db.commit()
    report["ref_date"] = max(ref_dates) if ref_dates else None
    return report
