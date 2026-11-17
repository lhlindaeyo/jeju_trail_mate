"""앞길 브리핑: 일몰 대비 도착, 앞쪽 보급 지점, 하루 날씨·다음 경유지·공지."""
from datetime import datetime, timezone

import pytest

from app.auth import local_user
from app.db import SessionLocal
from app.models import PoiPlace, TrailRoute
from app.services import brief_service
from app.services.geo import KST, path_length_m
from app.models import TrackingSession
from sqlalchemy import select

from .conftest import make_trip, route_id

LAT0, LON0 = 33.2600, 126.5000  # synthetic straight test road near Seogwipo — not real course data
M_LAT = 1 / 110_540.0  # degrees of latitude per metre
M_LON = 1 / (111_320.0 * 0.8350)  # degrees of longitude per metre at this latitude


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=KST)


def _current_session(db):
    return db.scalar(select(TrackingSession).where(TrackingSession.status != "ended"))


def test_brief_without_anything(client, auth):
    b = client.get("/v1/brief", headers=auth).json()
    assert b["mode"] == "none" and b["route"] is None
    assert b["daylight"]["status"] == "unavailable" and b["supplies"]["status"] == "unavailable"
    assert b["weather"]["status"] == "unavailable"
    assert b["notices"]["status"] == "unavailable"


def test_planned_daylight_ok_and_late(client, auth):
    rid = route_id(client, "7")
    with SessionLocal() as db:
        user = local_user(db)
        route = db.get(TrailRoute, rid)
        early = brief_service.build_brief(db, user, None, None, route, now=at(6))["daylight"]
        late = brief_service.build_brief(db, user, None, None, route, now=at(16, 30))["daylight"]
    assert early["mode"] == "planned" and early["status"] == "ok" and early["margin_min"] > 60
    assert early["latest_start_hhmm"] < early["finish_by_hhmm"] < early["sunset_hhmm"]
    assert late["status"] == "late" and late["margin_min"] < 0 and "일몰" in late["message"]


def test_walking_daylight_uses_progress(client, auth):
    rid = route_id(client, "7")
    trip = make_trip(client, auth)
    s = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": rid, "tracking_mode": "manual"}).json()
    client.post(f"/v1/sessions/{s['session_id']}/manual", headers=auth, json={"distance_km": 3})
    with SessionLocal() as db:
        user, session = local_user(db), _current_session(db)
        far = brief_service.build_brief(db, user, session.trip, session, now=at(11))["daylight"]
        client.post(f"/v1/sessions/{s['session_id']}/manual", headers=auth, json={"distance_km": 16})
        db.refresh(session)
        near = brief_service.build_brief(db, user, session.trip, session, now=at(11))["daylight"]
        mid = brief_service.build_brief(db, user, session.trip, session, now=at(17, 20))["daylight"]
    assert far["mode"] == "walking" and far["status"] == "ok"
    assert near["remaining_km"] < far["remaining_km"] and near["margin_min"] > far["margin_min"]
    assert mid["status"] in ("tight", "late") and "일몰" in mid["message"]
    client.post(f"/v1/sessions/{s['session_id']}/end", headers=auth)


@pytest.fixture
def straight_road(client):
    """A 2 km straight road heading north with a start/finish only; returns its route id."""
    path = [[LAT0 + i * 100 * M_LAT, LON0] for i in range(21)]
    with SessionLocal() as db:
        route = db.scalar(select(TrailRoute).where(TrailRoute.course_no == "T-brief"))
        if route is None:
            route = TrailRoute(trail_type="test_road", operator="테스트", course_no="T-brief", name="테스트 직선길",
                               start_lat=path[0][0], start_lon=path[0][1], end_lat=path[-1][0], end_lon=path[-1][1],
                               distance_km=round(path_length_m(path) / 1000, 3), has_geometry=True, path=path, source_url="https://example.test")
            db.add(route)
            db.commit()
        return route.id


def _poi(db, name, category, metres_north, metres_east=0.0):
    db.add(PoiPlace(name=name, category=category, lat=LAT0 + metres_north * M_LAT, lon=LON0 + metres_east * M_LON, geo_precision="source"))


def test_supplies_are_ahead_on_the_route(client, auth, straight_road):
    with SessionLocal() as db:
        db.query(PoiPlace).filter(PoiPlace.name.like("T-%")).delete(synchronize_session=False)
        _poi(db, "T-앞 화장실", "toilet", 800, 40)  # 500 m ahead of the walker, 40 m off the road
        _poi(db, "T-먼 화장실", "toilet", 1700, 20)
        _poi(db, "T-지나온 화장실", "toilet", 100, 20)  # already behind
        _poi(db, "T-길 밖 식당", "food", 1000, 600)  # 600 m from the road
        _poi(db, "T-앞 식당", "food", 1200, 10)
        db.commit()
    trip = make_trip(client, auth)
    s = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": straight_road}).json()
    now = datetime.now(timezone.utc).isoformat()
    client.post(f"/v1/sessions/{s['session_id']}/points", headers=auth,
                json={"points": [{"timestamp": now, "lat": LAT0 + 300 * M_LAT, "lon": LON0, "accuracy_m": 8}]})

    sup = client.get("/v1/brief", headers=auth).json()["supplies"]
    assert sup["basis"] == "along_route"
    groups = {g["category"]: g["places"] for g in sup["groups"]}
    # toilets: everything within 2 km of the walker, nearest first — the one just behind counts too
    assert [p["name"] for p in groups["toilet"]] == ["T-지나온 화장실", "T-앞 화장실", "T-먼 화장실"]
    assert groups["toilet"][0]["ahead_m"] is None and groups["toilet"][1]["ahead_m"] == pytest.approx(500, abs=40)
    assert next(g for g in sup["groups"] if g["category"] == "toilet")["scope"] == "내 위치 반경 2km"
    assert [p["name"] for p in groups["food"]] == ["T-앞 식당"]  # food stays 'ahead on the route'
    assert "영업" in sup["caveat"]
    client.post(f"/v1/sessions/{s['session_id']}/end", headers=auth)


def test_supplies_nearby_without_route_geometry(client, auth):
    rid = route_id(client, "7")
    with SessionLocal() as db:
        db.query(PoiPlace).filter(PoiPlace.name.like("T-%")).delete(synchronize_session=False)
        _poi(db, "T-주변 화장실", "toilet", 300)
        _poi(db, "T-먼 화장실", "toilet", 6000)
        db.commit()
    b = client.get("/v1/brief", headers=auth, params={"route_id": rid, "lat": LAT0, "lon": LON0}).json()
    assert b["mode"] == "planned" and b["location"]["source"] == "device"
    sup = b["supplies"]
    toilets = next(g for g in sup["groups"] if g["category"] == "toilet")["places"]
    assert sup["basis"] == "nearby" and "경로 좌표가 없어" in sup["note"]
    assert [p["name"] for p in toilets] == ["T-주변 화장실"] and toilets[0]["ahead_m"] is None
    assert 250 < toilets[0]["distance_m"] < 350


def test_weather_summary(client, auth, monkeypatch):
    rid = route_id(client, "7")
    slots = [("14:00", "20", "없음", "2.0"), ("15:00", "70", "비", "9.5"), ("16:00", "40", "없음", "4.0")]
    forecast = [{"time": f"2026-11-10T{t}:00+09:00", "temp_c": "14", "sky": "흐림", "precip_type": pt, "precip_prob_pct": pop, "wind_ms": w}
                for t, pop, pt, w in slots]
    ok = {"status": "ok", "source": "기상청 단기예보 (공공데이터포털)", "source_url": "https://www.data.go.kr/",
          "base_time": "20261110 1400", "forecast": forecast}
    monkeypatch.setattr(brief_service.weather_service, "get_weather", lambda *a, **k: ok)
    w = client.get("/v1/brief", headers=auth, params={"route_id": rid, "lat": LAT0, "lon": LON0}).json()["weather"]
    assert w["status"] == "ok" and w["rain_from_hhmm"] == "15:00" and w["max_wind_ms"] == 9.5
    assert "15:00" in w["headline"] and "바람이 강해요" in w["headline"] and w["source"]

    monkeypatch.setattr(brief_service.weather_service, "get_weather", lambda *a, **k: {**ok, "forecast": forecast[:1]})
    calm = client.get("/v1/brief", headers=auth, params={"route_id": rid, "lat": LAT0, "lon": LON0}).json()["weather"]
    assert calm["rain_from_hhmm"] is None and "비 소식이 없어요" in calm["headline"]


def test_companion_answers_from_brief_tool(client, auth):
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "해 지기 전에 도착할 수 있을까?"}).json()
    assert [c["name"] for c in r["tool_calls"]] == ["get_trail_brief"] and r["reply"]


def test_old_diary_and_book_endpoints_are_gone(client, auth):
    assert client.post("/v1/diary", headers=auth).status_code == 404
    assert client.get("/v1/books/x", headers=auth).status_code == 404


def test_startup_deletes_alerts_of_removed_features(client, auth):
    from app.models import Notification

    client.get("/v1/me", headers=auth)
    with SessionLocal() as db:
        uid = local_user(db).id
        db.add_all([Notification(user_id=uid, event_key="old-journal", event_type="journal_prompt", severity="info",
                                 title="오늘의 기록", message="한 줄이나 음성으로 남겨 보세요.", actions=[{"id": "write", "label": "기록하기"}]),
                    Notification(user_id=uid, event_key="old-photo", event_type="waypoint_arrival", severity="info",
                                 title="경유지 도착 · 새연교", message="사진을 남기고 일기에 연결할까요?",
                                 actions=[{"id": "open_journal", "label": "사진 남기기"}]),
                    Notification(user_id=uid, event_key="rest-1", event_type="rest_reminder", severity="info",
                                 title="잠시 쉬어 갈까요?", message="쉬어 가요.")])
        db.commit()
    from app.main import _drop_removed_feature_alerts

    _drop_removed_feature_alerts()  # runs at startup
    got = client.get("/v1/notifications", headers=auth).json()["notifications"]
    assert "rest_reminder" in {n["event_type"] for n in got}
    assert not any(n["event_type"] == "journal_prompt" or "사진" in n["message"] for n in got)
    with SessionLocal() as db:
        assert db.query(Notification).filter(Notification.event_key.in_(["old-journal", "old-photo"])).count() == 0
        assert db.query(Notification).filter(Notification.event_key == "rest-1").count() == 1


def test_planned_brief_has_notices_and_no_waypoint_section(client, auth):
    rid = route_id(client, "7")
    b = client.get(f"/v1/brief?route_id={rid}", headers=auth).json()
    assert "next_stop" not in b
    assert b["notices"]["status"] == "ok" and isinstance(b["notices"]["notices"], list)


def test_toilets_load_jeju_only_and_official_replaces_osm(client, tmp_path):
    import json

    from app.data_import import toilets

    osm = {"elements": [
        {"type": "node", "id": 1, "lat": 33.2447, "lon": 126.5730, "tags": {"amenity": "toilets", "name": "정방폭포 화장실"}},
        {"type": "way", "id": 2, "center": {"lat": 33.25, "lon": 126.56}, "tags": {"amenity": "toilets", "opening_hours": "09:00-18:00"}},
        {"type": "node", "id": 3, "lat": 33.25, "lon": 126.56, "tags": {"amenity": "toilets", "access": "customers"}},
        {"type": "node", "id": 4, "lat": 37.5, "lon": 127.0, "tags": {"amenity": "toilets"}},  # Seoul
    ]}
    (tmp_path / toilets.OSM_FILE).write_text(json.dumps(osm), encoding="utf-8")
    with SessionLocal() as db:
        rep = toilets.run(db, tmp_path)
        assert rep == {"file": toilets.OSM_FILE, "source": "osm_toilets", "places": 2}
        rows = db.query(PoiPlace).filter(PoiPlace.source_id == "osm_toilets").all()
        assert {r.name for r in rows} == {"정방폭포 화장실", "공중화장실"} and any(r.detail == "개방 09:00-18:00" for r in rows)
    csv = ("화장실명,구분,소재지도로명주소,소재지지번주소,개방시간상세,WGS84위도,WGS84경도\n"
           "천지연 공중화장실,공중화장실,제주특별자치도 서귀포시 남성중로 2,,연중무휴,33.2468,126.5547\n"
           "서울 화장실,공중화장실,서울특별시 중구,,24시간,37.56,126.97\n")
    (tmp_path / "전국공중화장실표준데이터.csv").write_text(csv, encoding="utf-8")
    with SessionLocal() as db:
        rep = toilets.run(db, tmp_path)
        assert rep["source"] == "public_toilet_std" and rep["places"] == 1
        assert db.query(PoiPlace).filter(PoiPlace.source_id == "osm_toilets").count() == 0
        db.query(PoiPlace).filter(PoiPlace.source_id == "public_toilet_std").delete()
        db.commit()
