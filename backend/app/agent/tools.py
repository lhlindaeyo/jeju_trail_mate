"""Tools the AI companion may call (spec §07 Tool Router). Each tool runs with the
requesting user's permissions only; mutating tools create *proposals*, never final changes."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import date as _date

from sqlalchemy.orm import Session

from ..models import AgentProposal, TrackingSession, Trip, User
from ..services import poi_service, route_service, schedule_service, weather_service
from ..services import journal_service
from ..services.geo import JEJU_CENTER, now_kst
from ..services.tracking_service import progress_summary, route_progress
from .llm import ToolSpec

TOOLS: list[ToolSpec] = [
    ToolSpec("list_routes", "코스 목록을 조회한다. trail_type으로 제주올레(jeju_olle, 사단법인 제주올레)와 "
             "하영올레(hayoung_olle, 서귀포시 도심 순환길)를 구분한다. 두 길은 서로 다른 길이다.", {
        "type": "object",
        "properties": {
            "trail_type": {"type": "string", "enum": ["jeju_olle", "hayoung_olle"]},
            "max_km": {"type": "number"},
        },
    }),
    ToolSpec("get_route", "코스 상세(유형, 운영 주체, 거리, 소요 시간 범위, 시점·종점, 경유지, 출처·기준일)를 조회한다.", {
        "type": "object",
        "properties": {"route": {"type": "string", "description": "예: '7'(제주올레), '하영올레 2코스', 또는 코스 id"}},
        "required": ["route"],
    }),
    ToolSpec("get_waypoints", "하영올레 코스의 경유지 목록(순서·좌표 보유 여부)을 조회한다. 경로 데이터가 없으면 has_geometry=false.", {
        "type": "object",
        "properties": {"route": {"type": "string", "description": "예: '하영올레 1코스'. 생략 시 현재 세션/오늘 코스"}},
    }),
    ToolSpec("get_next_waypoint", "진행 중인 세션 기준 다음 경유지와 남은 거리, 지금까지 지나온 경유지를 조회한다.", {
        "type": "object", "properties": {},
    }),
    ToolSpec("get_route_notices", "코스의 공식 우회·통제·안전 공지와 마지막 공지 확인 시각을 조회한다. route 생략 시 오늘 일정/진행 중 세션의 코스.", {
        "type": "object",
        "properties": {"route": {"type": "string"}},
    }),
    ToolSpec("get_weather", "기상청 단기예보를 조회한다. 좌표 생략 시 현재 위치 또는 오늘 코스 시작점.", {
        "type": "object",
        "properties": {"lat": {"type": "number"}, "lon": {"type": "number"}, "hours": {"type": "integer", "minimum": 1, "maximum": 48}},
    }),
    ToolSpec("find_places_near", "적재된 공공 데이터(poi_places)에서 현재 위치 주변 장소를 거리순으로 찾는다. "
             "영업 상태는 데이터 기준일의 공개 값이며 실시간이 아니다. 출처를 함께 돌려준다.", {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["food", "cafe", "rest", "toilet", "wifi"]},
            "radius_m": {"type": "integer", "minimum": 100, "maximum": 5000},
        },
        "required": ["category"],
    }),
    ToolSpec("estimate_arrival", "진행 중인 트레킹 세션 기준으로 남은 거리, 예상 도착 시각, 일몰 시각을 계산한다.", {
        "type": "object", "properties": {},
    }),
    ToolSpec("get_trip_plan", "현재 여행의 날짜별 승인된 일정과 승인 대기 중인 초안을 조회한다.", {
        "type": "object", "properties": {},
    }),
    ToolSpec("propose_schedule_change", "일정 변경 '제안'을 만든다. 사용자가 앱에서 승인해야만 반영된다.", {
        "type": "object",
        "properties": {
            "reason": {"type": "string", "description": "사용자에게 보여줄 변경 이유(짧게)"},
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string", "description": "YYYY-MM-DD"},
                        "route": {"type": ["string", "null"], "description": "코스 번호 또는 id. null이면 휴식일"},
                        "planned_start": {"type": ["string", "null"], "description": "HH:MM"},
                    },
                    "required": ["date"],
                },
            },
        },
        "required": ["reason", "changes"],
    }),
    ToolSpec("create_journal_draft", "사용자가 남긴 기록만으로 해당 날짜의 일기 초안을 만든다(저장은 사용자가 한다).", {
        "type": "object",
        "properties": {"date": {"type": "string", "description": "YYYY-MM-DD, 생략 시 오늘"}},
    }),
]


@dataclass
class ToolContext:
    db: Session
    user: User
    trip: Trip | None
    session: TrackingSession | None

    @property
    def prefs(self):
        return self.user.preferences

    def today_schedule(self):
        if not self.trip:
            return None
        return schedule_service.approved_for(self.db, self.trip, now_kst().date())

    def _location(self) -> tuple[float, float] | None:
        if self.session and self.session.last_lat is not None and self.prefs.consent_location:
            return self.session.last_lat, self.session.last_lon
        return None

    def _default_route(self):
        if self.session and self.session.route:
            return self.session.route
        sched = self.today_schedule()
        return sched.route if sched else None

    # --- tool implementations -------------------------------------------------------
    def list_routes(self, trail_type=None, max_km=None):
        routes = route_service.list_routes(self.db, trail_type=trail_type, max_km=max_km)
        brief = [{k: d[k] for k in ("id", "trail_label", "name", "subtitle", "distance_km", "duration_min_low",
                                    "duration_min_high", "start_point", "end_point", "has_geometry")}
                 for d in map(route_service.route_to_dict, routes)]
        return {"routes": brief, "sources": [route_service.source_to_dict(s) for s in {r.source for r in routes if r.source}],
                "note": "하영올레는 서귀포시가 운영하는 별개의 길이며 제주올레 완주에 포함되지 않습니다."}

    def get_waypoints(self, route=None):
        r = route_service.find_route(self.db, route) if route else self._default_route()
        if not r:
            return {"status": "not_found", "message": "어느 코스인지 알 수 없습니다."}
        if not r.waypoints:
            return {"status": "unavailable", "route": r.name,
                    "message": "이 코스는 경유지 데이터가 없습니다. 공식 사이트에서 확인하세요.", "official_url": r.source_url}
        return {"route": r.name, "trail_type": r.trail_type, "has_geometry": r.has_geometry,
                "waypoints": [route_service.waypoint_to_dict(w) for w in r.waypoints],
                "source": route_service.source_to_dict(r.source)}

    def get_next_waypoint(self):
        s = self.session
        if not s or s.status == "ended":
            return {"status": "unavailable", "message": "진행 중인 트레킹 세션이 없습니다."}
        summary = progress_summary(s, self.today_schedule())
        nxt = summary["next_waypoint"]
        if not nxt:
            return {"status": "unavailable", "message": "이 코스에는 다음 경유지 정보가 없습니다.", "visited": summary["visited_waypoints"]}
        return {"next": nxt, "visited": summary["visited_waypoints"], "progress_basis": summary["progress_basis"],
                "gps_accuracy_m": summary["gps_accuracy_m"],
                "message": None if nxt["distance_m"] is not None else "경로 데이터가 없어 거리를 계산하지 않았습니다."}

    def get_route(self, route):
        r = route_service.find_route(self.db, route)
        if not r:
            return {"status": "not_found", "message": f"'{route}' 코스를 데이터에서 찾지 못했습니다."}
        return route_service.route_to_dict(r)

    def get_route_notices(self, route=None):
        r = route_service.find_route(self.db, route) if route else self._default_route()
        if not r:
            return {"status": "not_found", "message": "어느 코스인지 알 수 없습니다. 코스 번호를 알려 주세요."}
        return route_service.notices_payload(self.db, r)

    def get_weather(self, lat=None, lon=None, hours=12):
        if lat is None or lon is None:
            loc = self._location()
            r = self._default_route()
            if loc:
                lat, lon = loc
            elif r and r.start_lat is not None:
                lat, lon = r.start_lat, r.start_lon
            else:
                lat, lon = JEJU_CENTER
        return weather_service.get_weather(lat, lon, hours)

    def find_places_near(self, category, radius_m=1000):
        loc = self._location()
        if not loc:
            return {"status": "unavailable", "places": [],
                    "message": "현재 위치를 알 수 없습니다. 트레킹 세션을 시작하고 위치 사용을 허용하면 주변 장소를 찾아볼 수 있어요."}
        return poi_service.find_places_near(self.db, loc[0], loc[1], category, radius_m)

    def estimate_arrival(self):
        s = self.session
        if not s or s.status == "ended":
            return {"status": "unavailable", "message": "진행 중인 트레킹 세션이 없습니다."}
        if not s.route:
            return {"status": "unavailable", "message": "세션에 코스가 지정되지 않아 남은 거리를 계산할 수 없습니다."}
        prog = route_progress(s)
        if prog["done_m"] is None:
            return {"status": "unavailable", "message": "이 코스는 거리 데이터가 없어 도착 시각을 계산할 수 없습니다."}
        out = schedule_service.estimate_arrival(route_distance_km=s.route.distance_km, walked_m=prog["done_m"],
                                                pace_kmh=self.prefs.pace_kmh, rest_every_minutes=self.prefs.rest_every_minutes)
        out["progress_basis"] = prog["basis"]
        summary = progress_summary(s, self.today_schedule())
        if summary["gps_quality"] == "poor" or summary["gps_stale"]:
            out["confidence"] = "low"
            out["assumptions"] += " 현재 GPS 신호가 약하거나 오래되어 추정이 부정확할 수 있습니다."
        return out

    def get_trip_plan(self):
        if not self.trip:
            return {"status": "unavailable", "message": "진행 중인 여행이 없습니다."}
        return {"trip": self.trip.title, "days": schedule_service.plan_view(self.db, self.trip)}

    def propose_schedule_change(self, reason, changes):
        if not self.trip:
            return {"status": "unavailable", "message": "여행이 없어 일정을 제안할 수 없습니다."}
        normalized = []
        for ch in changes:
            d = date.fromisoformat(ch["date"])
            if not (self.trip.start_date <= d <= self.trip.end_date):
                raise ValueError(f"{d}는 여행 기간 밖입니다.")
            route_id = None
            if ch.get("route"):
                r = route_service.find_route(self.db, str(ch["route"]))
                if not r:
                    raise ValueError(f"코스 '{ch['route']}'를 찾을 수 없습니다.")
                route_id = r.id
            normalized.append({"date": d.isoformat(), "route_id": route_id, "planned_start": ch.get("planned_start")})
        p = AgentProposal(trip_id=self.trip.id, kind="schedule_change", summary=reason, payload={"changes": normalized})
        self.db.add(p)
        self.db.commit()
        return {"proposal_id": p.id, "status": "pending_user_approval",
                "message": "일정 변경 제안을 만들었습니다. 사용자가 앱에서 승인해야 반영됩니다."}

    def create_journal_draft(self, date=None):
        if not self.trip:
            return {"status": "unavailable", "message": "여행이 없습니다."}
        d = _date.fromisoformat(date) if date else now_kst().date()
        try:
            e = journal_service.draft_daily(self.db, self.trip, d, self.prefs)
        except journal_service.NotEnoughRecords as exc:
            return {"status": "not_enough_records", "message": str(exc)}
        return {"entry_id": e.id, "draft": e.ai_draft, "status": "draft",
                "message": "일기 초안을 만들었습니다. 일기 화면에서 수정 후 저장해 주세요."}

    def execute(self, name: str, args: dict):
        allowed = {t.name for t in TOOLS}
        if name not in allowed:
            raise ValueError(f"unknown tool {name}")
        return getattr(self, name)(**args)
