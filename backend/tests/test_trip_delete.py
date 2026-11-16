from app.db import SessionLocal
from app.models import Notification

from .conftest import make_trip, route_id


def test_delete_trip_removes_plan_sessions_and_their_notifications(client, auth):
    keep = make_trip(client, auth)
    trip = make_trip(client, auth, start="2026-11-20", end="2026-11-21")
    rid = route_id(client, "1")
    client.post(f"/v1/trips/{trip}/plan", headers=auth, json={"route_ids": [rid]})
    client.post(f"/v1/trips/{trip}/plan/approve", headers=auth, json={})
    s = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": rid, "tracking_mode": "manual"}).json()
    client.post(f"/v1/sessions/{s['session_id']}/manual", headers=auth, json={"distance_km": 16})

    with SessionLocal() as db:
        db.add(Notification(user_id=client.get("/v1/me").json()["user_id"], session_id=s["session_id"], event_key="k1",
                            event_type="rest_reminder", severity="info", title="t", message="m"))
        db.commit()

    assert client.delete(f"/v1/trips/{trip}", headers=auth).status_code == 204

    assert client.get(f"/v1/trips/{trip}", headers=auth).status_code == 404
    ids = [t["id"] for t in client.get("/v1/trips", headers=auth).json()["trips"]]
    assert ids == [keep]
    assert client.get("/v1/sessions/current", headers=auth).json()["session"] is None
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.session_id == s["session_id"]).count() == 0


def test_delete_unknown_trip_is_404(client, auth):
    assert client.delete("/v1/trips/does-not-exist", headers=auth).status_code == 404
