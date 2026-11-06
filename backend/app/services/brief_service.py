"""앞길 브리핑: 지금 위치 기준으로 일몰 대비 도착, 앞쪽 보급 지점, 앞 6시간 날씨를 계산한다.

저장하는 것이 없다. 요청 때마다 진행 중인 세션(또는 오늘 코스), 일몰 계산, 적재된 공공 장소 데이터,
기상청 단기예보에서 그 자리에서 만들어 돌려준다. 확인하지 못한 값은 만들어 내지 않고 'unavailable'과
이유를 돌려준다."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..models import TrackingSession, TrailRoute, Trip, User
from . import poi_service, schedule_service, weather_service
from .geo import haversine_m, now_kst, project_on_path, sunset_kst
from .tracking_service import progress_summary, route_progress

SUNSET_BUFFER_MIN = 30  # 해 지기 이만큼 전에는 걷기를 마치는 것을 권한다
TIGHT_MARGIN_MIN = 30  # 도착 예상이 일몰보다 이만큼보다 덜 앞서면 '빠듯함'
SUPPLY_GROUPS = [("toilet", "화장실"), ("food", "식사·카페"), ("rest", "쉬어갈 곳")]
NEARBY_RADIUS_M = 2000  # 경로 좌표가 없는 코스: 내 주변 반경
AHEAD_WINDOW_M = 5000  # 경로 좌표가 있는 코스: 앞으로 이만큼까지
OFF_PATH_MAX_M = 300  # 경로에서 이만큼 안쪽의 장소만 '길 위'로 본다
BEHIND_TOLERANCE_M = 25  # 앞쪽 구간 매칭이 전체 경로 최선 매칭보다 이 이상 멀면 이미 지나온 곳
WEATHER_HOURS = 6
RAIN_POP_PCT = 60
STRONG_WIND_MS = 8.0


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --- 위치 ------------------------------------------------------------------------------
def _location(route: TrailRoute | None, session: TrackingSession | None, user: User,
              lat: float | None, lon: float | None) -> dict | None:
    if session and session.last_lat is not None and user.preferences.consent_location:
        return {"lat": session.last_lat, "lon": session.last_lon, "source": "session"}
    if lat is not None and lon is not None:
        return {"lat": lat, "lon": lon, "source": "device"}
    if route and route.start_lat is not None:
        return {"lat": route.start_lat, "lon": route.start_lon, "source": "route_start"}
    return None


# --- 1) 일몰 대비 도착 ----------------------------------------------------------------
def _daylight(route: TrailRoute | None, session: TrackingSession | None, sched, user: User, loc: dict | None,
              now: datetime) -> dict:
    if not route or not route.distance_km:
        return {"status": "unavailable", "message": "코스나 코스 거리 데이터가 없어 일몰과 비교할 수 없어요."}
    prefs = user.preferences
    where = (loc["lat"], loc["lon"]) if loc else (None, None)
    sunset = sunset_kst(now.date(), *where) if loc else sunset_kst(now.date())
    finish_by = sunset - timedelta(minutes=SUNSET_BUFFER_MIN)
    out: dict = {"sunset_hhmm": sunset.strftime("%H:%M"), "finish_by_hhmm": finish_by.strftime("%H:%M"),
                 "buffer_min": SUNSET_BUFFER_MIN}

    walking = session is not None and session.status != "ended"
    if walking:
        prog = route_progress(session)
        if prog["done_m"] is None:
            return {**out, "status": "unavailable", "mode": "walking", "message": "이 코스는 거리 데이터가 없어 도착 시각을 계산할 수 없어요."}
        est = schedule_service.estimate_arrival(route_distance_km=route.distance_km, walked_m=prog["done_m"], pace_kmh=prefs.pace_kmh,
                                                rest_every_minutes=prefs.rest_every_minutes, now=now)
        eta = datetime.fromisoformat(est["eta"])
        summary = progress_summary(session, sched)
        out.update(mode="walking", remaining_km=est["remaining_km"], remaining_min=est["remaining_min"], eta_hhmm=est["eta_hhmm"],
                   progress_basis=prog["basis"], assumptions=est["assumptions"],
                   confidence="low" if summary["gps_quality"] == "poor" or summary["gps_stale"] else "normal")
        if est["remaining_km"] <= 0:
            return {**out, "status": "done", "margin_min": None, "message": "코스 끝에 다다랐어요."}
    else:
        minutes = schedule_service.estimate_duration_min(route.distance_km, prefs.pace_kmh, prefs.rest_every_minutes)
        eta = now + timedelta(minutes=minutes)
        latest_start = finish_by - timedelta(minutes=minutes)
        planned = (sched.planned_start if sched and sched.route_id == route.id and sched.planned_start else prefs.walk_start_time)
        out.update(mode="planned", duration_min=minutes, eta_hhmm=eta.strftime("%H:%M"), latest_start_hhmm=latest_start.strftime("%H:%M"),
                   planned_start_hhmm=planned, confidence="normal",
                   assumptions=f"평균 {prefs.pace_kmh}km/h, {prefs.rest_every_minutes}분마다 휴식 가정. 지금 출발한다고 가정한 도착 시각이에요.")

    margin = round((sunset - eta).total_seconds() / 60)
    status = "late" if margin < 0 else "tight" if margin < TIGHT_MARGIN_MIN else "ok"
    out.update(status=status, margin_min=margin)
    if out["mode"] == "walking":
        out["message"] = {
            "ok": f"지금 속도면 {out['eta_hhmm']} 도착, 일몰({out['sunset_hhmm']})보다 {margin}분 일찍이에요.",
            "tight": f"{out['eta_hhmm']} 도착 예상으로 일몰({out['sunset_hhmm']})까지 {margin}분밖에 남지 않아요. 쉬는 시간을 줄이거나 가까운 마을에서 마치는 것도 생각해 보세요.",
            "late": f"{out['eta_hhmm']} 도착 예상으로 일몰({out['sunset_hhmm']}) 뒤예요. 가까운 마을이나 큰길에서 오늘 걷기를 마치고 귀환 수단을 확인하세요.",
        }[status]
    else:
        out["message"] = {
            "ok": f"{out['latest_start_hhmm']}까지 출발하면 일몰 {SUNSET_BUFFER_MIN}분 전에 마칠 수 있어요. 지금 출발하면 {out['eta_hhmm']} 도착이에요.",
            "tight": f"지금 출발하면 {out['eta_hhmm']} 도착으로 일몰({out['sunset_hhmm']})이 가까워요. 서두르거나 코스를 줄이는 걸 권해요.",
            "late": f"지금 출발하면 {out['eta_hhmm']} 도착으로 일몰({out['sunset_hhmm']}) 뒤예요. 오늘은 짧은 코스나 내일 출발을 권해요.",
        }[status]
    return out


# --- 2) 앞쪽 보급 지점 -------------------------------------------------------------------
def _supplies(db: Session, route: TrailRoute | None, session: TrackingSession | None, loc: dict | None) -> dict:
    if not loc:
        return {"status": "unavailable", "places": [], "message": "현재 위치를 알 수 없어요. 위치를 켜면 앞쪽 화장실·식당을 찾아 드려요."}
    lat, lon = loc["lat"], loc["lon"]
    on_path = bool(route and route.has_geometry and route.path and len(route.path) >= 2 and session is not None and session.status != "ended")
    done = (session.progress_m or 0.0) if on_path else 0.0
    length = route.distance_km * 1000 if on_path and route.distance_km else None
    groups, sources, caveat = [], {}, None
    for cat, label in SUPPLY_GROUPS:
        radius = AHEAD_WINDOW_M if on_path else NEARBY_RADIUS_M
        res = poi_service.find_places_near(db, lat, lon, cat, radius, limit=40)
        caveat = caveat or res.get("caveat")
        places = []
        for p in res.get("places", []):
            if not p.get("location"):
                continue
            item = {"name": p["name"], "address": p.get("address"), "detail": p.get("detail"), "source_id": p.get("source_id"),
                    "distance_m": round(haversine_m(lat, lon, p["location"][0], p["location"][1])), "ahead_m": None}
            if on_path:
                # 앞쪽 구간으로 한정한 매칭이 전체 경로 기준 최선의 매칭과 거의 같을 때만 '앞'으로 본다.
                # (이미 지나온 곳은 창 가장자리로 밀려 거리가 커지므로 걸러진다. 출발=도착인 순환 코스도 구분된다.)
                lat_p, lon_p = p["location"]
                off, along = project_on_path(route.path, lat_p, lon_p, along_range=(max(done - 50, 0.0), length or float("inf")))
                off_best, _ = project_on_path(route.path, lat_p, lon_p)
                if off > OFF_PATH_MAX_M or off - off_best > BEHIND_TOLERANCE_M or along - done > AHEAD_WINDOW_M:
                    continue
                item["ahead_m"] = round(max(along - done, 0.0))
                item["off_path_m"] = round(off)
            places.append(item)
        places.sort(key=lambda x: x["ahead_m"] if x["ahead_m"] is not None else x["distance_m"])
        for s in res.get("sources", []):
            sources[s["name"]] = s
        groups.append({"category": cat, "label": label, "places": places[:3],
                       "message": None if places else (res.get("message") or f"확인된 {label}가 없어요.")})
    return {"status": "ok", "basis": "along_route" if on_path else "nearby",
            "scope": f"앞으로 {AHEAD_WINDOW_M // 1000}km 이내, 길에서 {OFF_PATH_MAX_M}m 안쪽" if on_path
            else f"내 위치 반경 {NEARBY_RADIUS_M // 1000}km",
            "groups": groups, "sources": list(sources.values()), "caveat": caveat,
            "note": None if on_path else "이 코스는 경로 좌표가 없어 앞쪽이 아니라 내 주변 기준으로 보여 드려요."}


# --- 3) 앞 6시간 날씨 -------------------------------------------------------------------
def _weather(loc: dict | None, now: datetime) -> dict:
    if not loc:
        return {"status": "unavailable", "message": "위치를 알 수 없어 날씨를 확인하지 못했어요."}
    w = weather_service.get_weather(loc["lat"], loc["lon"], hours=WEATHER_HOURS)
    if w.get("status") != "ok":
        return w
    hours = w.get("forecast", [])
    if not hours:
        return weather_service.unavailable("empty forecast")
    rain_from = next((h for h in hours if (_num(h["precip_prob_pct"]) or 0) >= RAIN_POP_PCT
                      or (h.get("precip_type") not in (None, "없음", "0"))), None)
    winds = [x for x in (_num(h["wind_ms"]) for h in hours) if x is not None]
    temps = [x for x in (_num(h["temp_c"]) for h in hours) if x is not None]
    max_wind = max(winds) if winds else None
    if rain_from:
        headline = f"{rain_from['time'][11:16]}쯤부터 비 가능성이 있어요 (강수확률 {rain_from['precip_prob_pct']}%)."
    else:
        headline = f"앞으로 {WEATHER_HOURS}시간은 비 소식이 없어요."
    if max_wind is not None and max_wind >= STRONG_WIND_MS:
        headline += f" 바람이 강해요(최대 {max_wind:g}m/s)."
    return {"status": "ok", "headline": headline, "rain_from_hhmm": rain_from["time"][11:16] if rain_from else None,
            "max_wind_ms": max_wind, "temp_min_c": min(temps) if temps else None, "temp_max_c": max(temps) if temps else None,
            "hours": hours, "source": w["source"], "source_url": w.get("source_url"), "base_time": w["base_time"]}


# --- 묶음 ------------------------------------------------------------------------------
def build_brief(db: Session, user: User, trip: Trip | None, session: TrackingSession | None, route: TrailRoute | None = None,
                lat: float | None = None, lon: float | None = None, now: datetime | None = None) -> dict:
    now = now or now_kst()
    sched = schedule_service.approved_for(db, trip, now.date()) if trip else None
    if session is not None and session.status == "ended":
        session = None
    if session is not None and session.route:
        route = session.route
    elif route is None and sched is not None:
        route = sched.route
    loc = _location(route, session, user, lat, lon)
    return {
        "generated_at": now.isoformat(),
        "mode": "walking" if session is not None else "planned" if route else "none",
        "route": ({"id": route.id, "name": route.name, "trail_type": route.trail_type, "distance_km": route.distance_km,
                   "has_geometry": route.has_geometry} if route else None),
        "location": loc,
        "daylight": _daylight(route, session, sched, user, loc, now),
        "supplies": _supplies(db, route, session, loc),
        "weather": _weather(loc, now),
        "note": "정보는 요청한 순간 계산한 값이에요. 장소 영업 여부는 실시간이 아니며 공식 공지는 별도로 확인하세요.",
    }
