"""v1.2: two trail types, data pipeline, route-geometry progress, waypoint check-in, diary."""
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import shapefile
from pyproj import CRS

from app.data_import import hayoung_shp, olle_csv, poi_csv
from app.data_import.common import ImportError_
from app.db import SessionLocal
from app.services.geo import haversine_m, path_length_m, project_on_path

from .conftest import make_trip, route_id
from .test_journal_book import PNG

ORIGIN = (33.2530, 126.5600)  # synthetic test square near Seogwipo — not real course data
SIDE_DEG_LAT, SIDE_DEG_LON = 0.0045, 0.0054  # ≈ 500 m each


def perimeter(t: float) -> list[float]:
    """Point at fraction t of a closed square loop starting and ending at ORIGIN."""
    t = t % 1.0
    lat0, lon0 = ORIGIN
    k, f = int(t * 4), (t * 4) % 1
    corners = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
    a, b = corners[k], corners[k + 1]
    return [lat0 + (a[0] + (b[0] - a[0]) * f) * SIDE_DEG_LAT, lon0 + (a[1] + (b[1] - a[1]) * f) * SIDE_DEG_LON]


def write_synthetic_shp(folder: Path, *, with_prj=True) -> None:
    """Hayoung course 1 as 10 legs; each leg's code is the waypoint it ends at (1S → 11 … 19 → 1F)."""
    w = shapefile.Writer(str(folder / "hayoung-olle"), shapeType=shapefile.POLYLINE, encoding="utf-8")
    w.field("CODE", "C", size=8)
    w.field("NAME", "C", size=60)
    codes = [f"1{i}" for i in range(1, 10)] + ["1F"]
    for k, code in enumerate(codes):
        pts = [perimeter(k / 10 + j / 50) for j in range(6)]
        if k == 9:
            pts[-1] = list(ORIGIN)
        w.line([[[lon, lat] for lat, lon in pts]])
        w.record(code, f"경유지{code}")
    w.close()
    (folder / "hayoung-olle.cpg").write_text("UTF-8")
    (folder / "hayoung-olle.qmd").write_text("<qgis/>")
    if with_prj:
        (folder / "hayoung-olle.prj").write_text(CRS.from_epsg(4326).to_wkt())


def test_seed_has_both_trail_types(client):
    olle = client.get("/v1/routes", params={"trail_type": "jeju_olle"}).json()["routes"]
    hay = client.get("/v1/routes", params={"trail_type": "hayoung_olle"}).json()["routes"]
    assert len(olle) == 27 and len(hay) == 3
    assert {r["course_no"] for r in olle} >= {"3-A", "3-B", "1-1", "18-1"}
    assert all(r["operator"] == "서귀포시" and r["type_notice"] for r in hay)
    assert [len(r["waypoints"]) for r in hay] == [11, 15, 9]
    assert olle[0]["freshness"]["ref_date"] == "2020-01-14" and olle[0]["freshness"]["data_status"] == "needs_check"
    h3 = next(r for r in hay if r["course_no"] == "3")
    assert h3["has_geometry"] is False and h3["distance_km"] is None  # nothing invented before SHP import
    sources = {s["id"] for s in client.get("/v1/data-sources").json()["sources"]}
    assert {"olle_course_status", "hayoung_shp", "seogwipo_food"} <= sources


def test_olle_csv_parsing():
    assert olle_csv.parse_distance_and_duration("15.14~5시간", None) == (15.1, 240, 300)
    assert olle_csv.parse_distance_and_duration("17.7km", "5~6시간") == (17.7, 300, 360)
    assert olle_csv.normalize_course_no("3A코스") == "3-A"
    assert olle_csv.normalize_course_no("올레 18-1코스") == "18-1"


def test_shp_missing_prj_is_reported():
    with tempfile.TemporaryDirectory() as d:
        write_synthetic_shp(Path(d), with_prj=False)
        with SessionLocal() as db, pytest.raises(ImportError_, match=".prj"):
            hayoung_shp.run(db, Path(d))


def test_projection_respects_loop_window():
    path = [perimeter(i / 200) for i in range(201)]
    length = path_length_m(path)
    near_start = perimeter(0.001)
    _d, along_global = project_on_path(path, *near_start)
    _d, along_window = project_on_path(path, *near_start, (0, 500))
    assert along_window < 50 and (along_global < 50 or along_global > length - 50)


@pytest.fixture(scope="module")
def hayoung_imported(client):
    with tempfile.TemporaryDirectory() as d:
        write_synthetic_shp(Path(d))
        with SessionLocal() as db:
            info = hayoung_shp.inspect(Path(d))
            assert info["shape_type"].upper().startswith("POLYLINE") and "CODE" in info["fields"]
            report = hayoung_shp.run(db, Path(d), license="테스트")
    assert report["routes"]["1"]["waypoints_located"] == 11, report
    return route_id(client, "1", "hayoung_olle")


def test_import_builds_geometry(client, hayoung_imported):
    r = client.get(f"/v1/routes/{hayoung_imported}").json()
    assert r["has_geometry"] and 1.9 < r["distance_km"] < 2.1  # 4 × ~500 m
    assert r["path"][0] == pytest.approx(list(ORIGIN))
    alongs = [w["along_m"] for w in r["waypoints"]]
    assert alongs == sorted(alongs) and alongs[-1] == pytest.approx(r["distance_km"] * 1000, abs=5)
    src = next(s for s in client.get("/v1/data-sources").json()["sources"] if s["id"] == "hayoung_shp")
    assert src["status"] == "imported" and src["license"] == "테스트"


def _pts(fracs, start, acc=8, offset_lon=0.0):
    return [{"timestamp": (start + timedelta(seconds=40 * i)).isoformat(),
             "lat": perimeter(f)[0], "lon": perimeter(f)[1] + offset_lon, "accuracy_m": acc} for i, f in enumerate(fracs)]


def test_progress_checkin_and_off_route(client, auth, hayoung_imported):
    trip = make_trip(client, auth)
    s = client.post("/v1/sessions", headers=auth, json={"trip_id": trip, "route_id": hayoung_imported}).json()
    sid = s["session_id"]
    t0 = datetime.now(timezone.utc)
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3], t0)}).json()
    summ = out["summary"]
    assert summ["progress_basis"] == "route_geometry" and 0.28 < summ["actual_progress"] < 0.32
    assert summ["visited_waypoints"] == ["서귀포시청제1청사", "법장사골목길", "걸매생태공원", "천지연폭포"]
    assert summ["next_waypoint"]["code"] == "14" and 150 < summ["next_waypoint"]["distance_m"] < 250
    arrivals = [n for n in out["notifications"] if n["event_type"] == "waypoint_arrival"]
    assert [n["title"] for n in arrivals] == ["경유지 도착 · 법장사골목길", "경유지 도착 · 걸매생태공원", "경유지 도착 · 천지연폭포"]
    assert "1F" not in str(summ)  # loop finish not triggered at the start

    # Poor accuracy far away: no deviation alert, no progress change.
    far = 0.003  # ≈ 280 m east of the (north-running) east edge
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0.31, 0.31, 0.31, 0.31], t0 + timedelta(minutes=5), acc=80, offset_lon=far)}).json()
    assert not [n for n in out["notifications"] if n["event_type"] == "off_route"]
    # Good accuracy, sustained > 2 min: "위치 확인" alert, once.
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0.31] * 5, t0 + timedelta(minutes=8), offset_lon=far)}).json()
    off = [n for n in out["notifications"] if n["event_type"] == "off_route"]
    assert len(off) == 1 and off[0]["title"].startswith("위치 확인")
    assert 0.28 < out["summary"]["actual_progress"] < 0.32

    r = client.post("/v1/agent/chat", headers=auth, json={"message": "다음 경유지까지 얼마나 남았어?", "trip_id": trip}).json()
    assert "서귀포칠십리시공원" in r["reply"]
    client.post(f"/v1/sessions/{sid}/end", headers=auth)


def test_hayoung_bundles_and_plan(client, auth, hayoung_imported):
    bundles = client.get("/v1/routes/hayoung/bundles", headers=auth).json()["bundles"]
    half = next(b for b in bundles if b["label"] == "반나절")
    assert half["distance_km"] and half["duration_min"]
    day = next(b for b in bundles if b["label"] == "하루")
    assert day["distance_km"] is None  # course 2 has no geometry yet → honest unknown
    trip = make_trip(client, auth, "2026-11-10", "2026-11-11")
    h2 = route_id(client, "2", "hayoung_olle")
    drafts = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={"route_ids": [hayoung_imported, h2]}).json()["drafts"]
    assert drafts[0]["route"]["trail_type"] == "hayoung_olle" and "서귀포시청" in drafts[0]["note"]
    assert drafts[0]["extra_routes"] == []  # course 2 cannot be timed, so it is not bundled
    assert drafts[1]["route"]["course_no"] == "2" and "거리 데이터 적재 전" in drafts[1]["note"]


def test_olle_plan_chains_end_to_start(client, auth):
    client.patch("/v1/me/preferences", headers=auth, json={"daily_distance_target_km": 25, "walk_end_time": "18:00", "interests": []})
    trip = make_trip(client, auth, "2026-11-10", "2026-11-12")
    drafts = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={}).json()["drafts"]
    routes = [d["route"] for d in drafts if d["route"]]
    assert all(r["trail_type"] == "jeju_olle" for r in routes)
    assert [r["course_no"] for r in routes] == ["4", "5", "6"]  # 표선→남원→쇠소깍→서귀포
    for a, b in zip(routes, routes[1:]):
        assert b["start_point"] == a["end_point"]


def test_diary_photo_and_three_lines(client, auth, hayoung_imported):
    trip = make_trip(client, auth)
    wp = client.get(f"/v1/routes/{hayoung_imported}").json()["waypoints"][5]
    form = {"trip_id": trip, "text": "새연교 바람\n노을이 예뻤다", "waypoint_id": wp["id"]}
    r = client.post("/v1/diary", headers=auth, data=form, files={"photo": ("a.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    assert r.json()["waypoint"]["name"] == wp["name"] and r.json()["media"][0]["url"]
    bad = client.post("/v1/diary", headers=auth, data={**form, "text": "1\n2\n3\n4"}, files={"photo": ("a.png", PNG, "image/png")})
    assert bad.status_code == 422 and "3줄" in bad.text
    assert client.post("/v1/diary", headers=auth, data=form).status_code == 422  # photo is required


def test_poi_import_and_search(client, auth):
    with tempfile.TemporaryDirectory() as d:
        lat, lon = ORIGIN
        rows = ["사업장명,영업상태명,업태구분명,소재지도로명주소,위도,경도",
                f"바당카페,영업/정상,커피숍,서귀포시 어딘가 1,{lat + 0.0005},{lon}",
                f"닫은식당,폐업,일반음식점,서귀포시 어딘가 2,{lat + 0.0004},{lon}",
                f"먼카페,영업/정상,커피숍,서귀포시 어딘가 3,{lat + 0.05},{lon}",
                "주소만식당,영업/정상,분식,서귀포시 어딘가 4,,"]
        (Path(d) / "food.csv").write_bytes("\n".join(rows).encode("cp949"))
        with SessionLocal() as db:
            rep = poi_csv.run(db, Path(d))
            assert rep["rows"] == 4 and rep["with_coords"] == 3 and rep["closed"] == 1
            from app.services.poi_service import find_places_near

            res = find_places_near(db, lat, lon, "food", 1000)
    assert [p["name"] for p in res["places"]] == ["바당카페"]  # closed + far + address-only excluded
    assert res["sources"][0]["name"].startswith("제주특별자치도 서귀포시_휴게음식점현황")
    assert math.isclose(res["places"][0]["distance_m"], haversine_m(lat, lon, lat + 0.0005, lon), abs_tol=1)
