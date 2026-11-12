from datetime import datetime, timedelta, timezone

from app.agent import llm as llm_mod
from app.agent.llm import AgentRun, ToolCall
from app.db import SessionLocal
from app.models import DailySchedule, TrackingSession, TrailRoute, Trip, User, UserPreferences
from app.rules import engine
from app.services.geo import KST

from .conftest import make_trip, route_id

ADMIN = {"X-Admin-Token": "test-admin"}


def _points(start: datetime, n: int, lat=33.2480, lon=126.5640):
    # ~50 m steps every 30 s ≈ 6 km/h
    return [{"timestamp": (start + timedelta(seconds=30 * i)).isoformat(), "lat": lat, "lon": lon - 0.00054 * i, "accuracy_m": 8}
            for i in range(n)]


def test_session_notices_and_dedup(client, auth):
    trip = make_trip(client, auth)
    rid = route_id(client, "7")
    r = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": rid})
    assert r.status_code == 201, r.text
    body = r.json()
    sid = body["session_id"]
    # end_time_risk may also fire depending on the wall clock, so check membership.
    assert "notice_unverified" in [n["event_type"] for n in body["notifications"]]

    now = datetime.now(timezone.utc)
    r = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _points(now, 10)})
    out = r.json()
    assert out["accepted"] == 10 and out["distance_m"] > 300
    assert "notice_unverified" not in [n["event_type"] for n in out["notifications"]]  # not repeated

    # Admin registers an official detour notice → one safety notification, deduplicated afterwards.
    assert client.post("/v1/admin/routes/7/notices", headers=ADMIN, json={
        "title": "7코스 일부 구간 낙석으로 우회", "notice_type": "detour", "source_url": "https://www.jejuolle.org/"}).status_code == 201
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _points(now + timedelta(minutes=5), 3)}).json()
    notice = [n for n in out["notifications"] if n["event_type"] == "route_notice"]
    assert len(notice) == 1 and notice[0]["severity"] == "safety"
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _points(now + timedelta(minutes=7), 3)}).json()
    assert not [n for n in out["notifications"] if n["event_type"] in ("route_notice", "notice_unverified")]
    assert client.post("/v1/admin/routes/7/notices", headers={"X-Admin-Token": "wrong"}, json={}).status_code == 403
    client.post(f"/v1/sessions/{sid}/end", headers=auth)


def _fixture_session(db, accuracy: float, now_utc: datetime):
    user = User(email=f"rules-{accuracy}-{now_utc.timestamp()}@x.com")
    user.preferences = UserPreferences(walk_end_time="18:00", rest_every_minutes=0, pace_kmh=4.0)
    db.add(user)
    route = db.query(TrailRoute).filter_by(trail_type="jeju_olle", course_no="6").one()
    local = now_utc.astimezone(KST)
    trip = Trip(user=user, title="t", start_date=local.date(), end_date=local.date())
    db.add(trip)
    db.flush()
    db.add(DailySchedule(trip_id=trip.id, date=local.date(), route_id=route.id, planned_start="08:00", planned_end="12:00",
                         revision=1, user_approved_at=now_utc))
    s = TrackingSession(trip_id=trip.id, route_id=route.id, started_at=now_utc - timedelta(hours=3), distance_m=1000,
                        last_lat=33.25, last_lon=126.6, last_accuracy_m=accuracy, last_point_at=now_utc - timedelta(minutes=1))
    db.add(s)
    db.commit()
    return user, s


def test_delay_only_flagged_with_good_gps():
    now_utc = datetime.now(KST).replace(hour=11, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    with SessionLocal() as db:
        user, s = _fixture_session(db, accuracy=10, now_utc=now_utc)
        types = [e.event_type for e in engine.evaluate(db, s, user, now_utc, weather={"status": "unavailable"})]
        assert "schedule_delay" in types
        delivered = engine.deliver(db, s, user, engine.evaluate(db, s, user, now_utc, weather={"status": "unavailable"}), now_utc)
        again = engine.deliver(db, s, user, engine.evaluate(db, s, user, now_utc + timedelta(minutes=5), weather={"status": "unavailable"}), now_utc + timedelta(minutes=5))
        assert any(n.event_type == "schedule_delay" for n in delivered)
        assert not any(n.event_type == "schedule_delay" for n in again)  # within dedupe window

        user2, s2 = _fixture_session(db, accuracy=300, now_utc=now_utc)
        types2 = [e.event_type for e in engine.evaluate(db, s2, user2, now_utc, weather={"status": "unavailable"})]
        assert "schedule_delay" not in types2


def test_rain_event_from_official_forecast_only():
    now_utc = datetime.now(KST).replace(hour=9, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    weather = {"status": "ok", "source": "기상청", "base_time": "x", "source_url": "u",
               "forecast": [{"time": "2026-11-05T10:00:00+09:00", "precip_prob_pct": "70"}]}
    with SessionLocal() as db:
        user, s = _fixture_session(db, accuracy=10, now_utc=now_utc)
        assert "weather" in [e.event_type for e in engine.evaluate(db, s, user, now_utc, weather=weather)]
        assert "weather" not in [e.event_type for e in engine.evaluate(db, s, user, now_utc, weather={"status": "unavailable"})]


def test_agent_mock_routes_to_tools(client, auth):
    trip = make_trip(client, auth)
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "7코스 우회 공지 있어?", "trip_id": trip}).json()
    assert r["tool_calls"] == [{"name": "get_route_notices", "ok": False}] or r["tool_calls"][0]["name"] == "get_route_notices"
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "근처 식당 알려줘", "trip_id": trip}).json()
    assert "위치" in r["reply"]  # no session/location → honest "can't"; never invents places
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "발목을 다쳤어요 살려주세요"}).json()
    assert r["intent"] == "emergency" and "119" in r["reply"]


class _ExternalFake:
    name, external = "fake", True

    def __init__(self, tool=None, args=None):
        self.tool, self.args = tool, args

    def generate(self, system, user, max_tokens=4000):
        return "오늘은 바다를 보며 걸었다."

    def run_tools(self, system, user, tools, execute, max_turns=6):
        out = execute(self.tool, self.args)
        return AgentRun(text="제안을 만들었어요.", tool_calls=[ToolCall(self.tool, self.args, True, out)], provider=self.name)


def test_ai_consent_required_for_external_provider(client, auth):
    client.patch("/v1/me/preferences", headers=auth, json={"consent_ai_processing": False})
    llm_mod.set_llm(_ExternalFake("get_trip_plan", {}))
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "일정 알려줘"}).json()
    assert r["intent"] == "consent_required"


def test_agent_proposal_needs_user_approval(client, auth):
    trip = make_trip(client, auth)
    client.post(f"/v1/trips/{trip}/plan", headers=auth, json={})
    client.post(f"/v1/trips/{trip}/plan/approve", headers=auth, json={})
    before = client.get(f"/v1/trips/{trip}", headers=auth).json()["plan"][0]["approved"]
    llm_mod.set_llm(_ExternalFake("propose_schedule_change", {"reason": "비 예보", "changes": [{"date": "2026-11-10", "route": None}]}))
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "내일 쉬고 싶어", "trip_id": trip}).json()
    assert len(r["proposals"]) == 1 and r["proposals"][0]["status"] == "pending"
    after = client.get(f"/v1/trips/{trip}", headers=auth).json()["plan"][0]["approved"]
    assert after["id"] == before["id"]  # nothing applied yet

    pid = r["proposals"][0]["id"]
    d = client.post(f"/v1/trips/{trip}/proposals/{pid}/decision", headers=auth, json={"approve": True}).json()
    assert d["status"] == "approved"
    assert d["plan"][0]["approved"]["route"] is None and d["plan"][0]["approved"]["revision"] > before["revision"]
    assert client.post(f"/v1/trips/{trip}/proposals/{pid}/decision", headers=auth, json={"approve": True}).status_code == 409


def test_sim_points_may_fast_forward(client, auth):
    """The web demo simulator (source="sim") fast-forwards a walk; real device points at that speed are jumps."""
    trip = make_trip(client, auth)
    rid = route_id(client, "7")
    now = datetime.now(timezone.utc)
    # ~50 m every 2.5 s ≈ 20 m/s (5× walking speed limit)
    fast = [{**p, "timestamp": (now + timedelta(seconds=2.5 * i)).isoformat()} for i, p in enumerate(_points(now, 10))]
    for source, counted in (("device", False), ("sim", True)):
        sid = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": rid}).json()["session_id"]
        out = client.post(f"/v1/sessions/{sid}/points", headers=auth,
                          json={"points": [{**p, "source": source} for p in fast]}).json()
        assert out["accepted"] == 10
        assert (out["distance_m"] > 400) is counted, (source, out["distance_m"])
        client.post(f"/v1/sessions/{sid}/end", headers=auth)
    bad = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": [{**fast[0], "source": "x"}]})
    assert bad.status_code == 422
