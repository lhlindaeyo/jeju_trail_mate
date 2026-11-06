from datetime import date

from app.services.geo import latlon_to_kma_grid, sunset_kst
from app.services.schedule_service import estimate_duration_min

from .conftest import make_trip, route_id


def test_sunset_jeju_november():
    s = sunset_kst(date(2026, 11, 5))
    assert 17 * 60 + 20 <= s.hour * 60 + s.minute <= 17 * 60 + 50


def test_kma_grid_jeju_city():
    assert latlon_to_kma_grid(33.4996, 126.5312) == (53, 38)


def test_duration_includes_rests():
    assert estimate_duration_min(14, 3.5, 90) == 240 + 2 * 10


def test_routes_marked_unverified(client):
    data = client.get("/v1/routes").json()
    assert data["routes"], "seed routes loaded"
    assert all(r["freshness"]["data_status"] == "needs_check" for r in data["routes"])
    notices = client.get("/v1/routes/10/notices").json()
    assert notices["notices_status"] == "needs_check" and notices["caveat"]


def test_plan_requires_approval(client, auth):
    trip = make_trip(client, auth)
    drafts = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={}).json()["drafts"]
    assert len(drafts) == 3 and all(d["user_approved_at"] is None for d in drafts)
    plan = client.get(f"/v1/trips/{trip}", headers=auth).json()["plan"]
    assert all(d["approved"] is None and d["pending_draft"] for d in plan)

    client.post(f"/v1/trips/{trip}/plan/approve", headers=auth, json={})
    plan = client.get(f"/v1/trips/{trip}", headers=auth).json()["plan"]
    first = plan[0]["approved"]
    assert first and plan[0]["pending_draft"] is None

    # A change creates a pending draft but leaves the approved row untouched.
    client.post(f"/v1/trips/{trip}/plan/changes", headers=auth, json=[{"date": "2026-11-10", "route_id": None, "note": "휴식"}])
    plan = client.get(f"/v1/trips/{trip}", headers=auth).json()["plan"]
    assert plan[0]["approved"]["id"] == first["id"]
    assert plan[0]["pending_draft"]["note"] == "휴식"


def test_plan_respects_end_time(client, auth):
    client.patch("/v1/me/preferences", headers=auth, json={"walk_end_time": "08:30", "daily_distance_target_km": 20})
    trip = make_trip(client, auth, "2026-11-10", "2026-11-10")
    d = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={}).json()["drafts"][0]
    assert d["route"] is None and "맞는 코스가 없어" in d["note"]


def test_no_login_required(client, auth):
    assert client.get("/v1/me").status_code == 200
    assert client.post("/v1/auth/dev-login", json={"email": "a@b.c"}).status_code in (404, 405)


def test_gps_requires_consent_and_manual_fallback(client, auth):
    client.patch("/v1/me/preferences", headers=auth, json={"consent_location": False})
    trip = make_trip(client, auth)
    r = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": route_id(client, "6")})
    assert r.status_code == 409
    r = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": route_id(client, "6"), "tracking_mode": "manual"})
    assert r.status_code == 201
    sid = r.json()["session_id"]
    r = client.post(f"/v1/sessions/{sid}/manual", headers=auth, json={"distance_km": 5.5})
    assert r.json()["walked_km"] == 5.5
    client.post(f"/v1/sessions/{sid}/end", headers=auth)
