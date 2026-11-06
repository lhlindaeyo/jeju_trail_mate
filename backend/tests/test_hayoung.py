"""v1.2: two trail types, data pipeline, route-geometry progress, waypoint check-in."""
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import shapefile
from pyproj import CRS

import httpx

from app.data_import import hayoung_shp, locate_waypoints, olle_csv, poi_csv, poi_sources
from app.data_import.common import ImportError_
from app.db import SessionLocal
from app.services.geo import haversine_m, path_length_m, project_on_path

from .conftest import make_trip, route_id

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


def _seg(t0: float, t1: float, n: int = 12) -> list[list[float]]:
    return [perimeter(t0 + (t1 - t0) * j / n) if t1 < 1 or j < n else list(ORIGIN) for j in range(n + 1)]


SPUR_END = [ORIGIN[0] + SIDE_DEG_LAT, ORIGIN[1] + SIDE_DEG_LON + 0.001]  # ≈ 93 m east of the far corner


def write_synthetic_shp(folder: Path, *, with_prj=True, with_course3=False) -> None:
    """Mimics the real 제주데이터허브 files: one file set per course, ONE multi-part POLYLINE with
    route_name / total_dist / total_di_1, parts out of order, a duplicate digitisation and a spur."""
    def write(no: int, parts: list[list[list[float]]], dist_m: int):
        stem = folder / f"hayoung-olle-course-{no}"
        w = shapefile.Writer(str(stem), shapeType=shapefile.POLYLINE, encoding="utf-8")
        for name, typ, size in (("id", "N", 10), ("link_type", "C", 254), ("route_name", "C", 254), ("start_name", "C", 254),
                                ("end_name", "C", 254), ("total_dist", "C", 254), ("total_di_1", "N", 10), ("address", "C", 254)):
            w.field(name, typ, size=size)
        w.line([[[lon, lat] for lat, lon in part] for part in parts])
        w.record(no, "walk", f"하영올레 {no}코스", "서귀포시청제1청사", "서귀포시청제1청사", f"{dist_m / 1000:.1f}km", dist_m, "제주 서귀포시")
        w.close()
        (stem.with_suffix(".cpg")).write_text("UTF-8")
        (stem.with_suffix(".qmd")).write_text("<qgis/>")
        if with_prj:
            (stem.with_suffix(".prj")).write_text(CRS.from_epsg(4326).to_wkt())

    p0, p1a, p1b, p2 = _seg(0, 0.25), _seg(0.25, 0.5), _seg(0.5, 0.75), _seg(0.75, 1.0)
    dup = [[lat + 0.00001, lon] for lat, lon in p0]  # second digitisation of the same street
    dup[0], dup[-1] = p0[0], p0[-1]
    spur = [perimeter(0.5), SPUR_END]
    write(1, [p1a, spur, p0, dup, p2, p1b], 2190)
    small = [list(ORIGIN), [ORIGIN[0], ORIGIN[1] - 0.003], [ORIGIN[0] - 0.002, ORIGIN[1] - 0.003], list(ORIGIN)]
    write(2, [small], 900)
    if with_course3:
        # Closed loop digitised from a point ~1 km north that passes ~20 m from City Hall (like the real 3코스).
        n = ORIGIN[0] + 0.009
        loop = [[n, ORIGIN[1]], [n, ORIGIN[1] + 0.003], [ORIGIN[0] + 0.0002, ORIGIN[1] + 0.003],
                [ORIGIN[0] + 0.0002, ORIGIN[1]], [n, ORIGIN[1]]]
        write(3, [loop], 2600)


def _wifi_transport(points: dict[str, list[float]]):
    """Fake 제주데이터허브 공공와이파이 API following the HWP guide's response shape."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert "/api/proxy/Dtb18ta1btbD1Da1a81aaDttab6tDabb/test-key" in str(request.url)
        if request.url.params.get("limit") == "1":  # newest-snapshot probe
            return httpx.Response(200, json={"totCnt": 999, "hasMore": True, "data": [{"baseDate": "20260820"}]})
        assert request.url.params.get("baseDate") == "20260820"
        data = [] if request.url.params.get("addressDong") != "서홍동" else [
            {"baseDate": "20260820", "macAddress": "x", "apGroupName": name, "installLocationDetail": "가로등",
             "category": "관광지", "categoryDetail": "관광지", "addressDong": "제주특별자치도 서귀포시 서홍동",
             "addressDetail": f"서귀포시 서홍동 {i}", "latitude": str(lat), "longitude": str(lon)}
            for i, (name, (lat, lon)) in enumerate(points.items())]
        return httpx.Response(200, json={"totCnt": len(data), "hasMore": False, "data": data})
    return httpx.MockTransport(handler)


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
    assert olle_csv.parse_distance_and_duration("10.2km", "3-4시간") == (10.2, 180, 240)
    assert olle_csv.normalize_course_no("3A코스") == "3-A"
    assert olle_csv.normalize_course_no("올레 18-1코스") == "18-1"
    # Real 2026-07-31 file: variant only in the title, CP949-lost dash, combined endpoints column.
    assert olle_csv.course_no_from("3코스", olle_csv.clean_title("온평?표선(A)")) == "3-A"
    assert olle_csv.clean_title("온평?표선(A)") == "온평-표선(A)"
    assert olle_csv.course_no_from("18-1코스", "추자도A") == "18-1"
    assert olle_csv.split_endpoints("시흥초등학교-광치기해변") == ("시흥초등학교", "광치기해변")
    assert olle_csv.split_endpoints("신양항·졸복산·추자교") == (None, None)


def test_olle_csv_import_real_layout(client):
    header = "코스별,코스명,거리,소요시간정보,시종점정보,데이터기준일자"
    rows = [header, "1코스,시흥-광치기,15.1km,4~5시간,시흥초등학교-광치기해변,2026-07-31",
            "15코스,한림-고내(A),16.7km,5~6시간,한림항-고내포구,2026-07-31",
            "15코스,한림-고내(B),13.5km,4~5시간,한림항-고내포구,2026-07-31"]
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "olle.csv").write_bytes("\n".join(rows).encode("cp949"))
        with SessionLocal() as db:
            rep = olle_csv.run(db, Path(d), dry_run=True)
    assert rep["rows"] == 3 and rep["problems"] == [] and rep["ref_date"] == "2026-07-31"


GOODPRICE_XML = """<?xml version="1.0" encoding="UTF-8"?><Response><total>2</total><page>1</page><pageSize>50</pageSize><items>
<item><shop_code>001</shop_code><shop_title>바당국수</shop_title><shop_tel>064-000-0000</shop_tel><shop_local>서귀포시</shop_local>
<shop_address>서귀포시 동문동로 21-1(서귀동)</shop_address><shop_goods>멸치국수 5,000원</shop_goods><shop_holiday>연중</shop_holiday><shop_thumb>a.jpg</shop_thumb></item>
<item><shop_code>004</shop_code><shop_title>바다해호텔</shop_title><shop_tel>064-762-5300</shop_tel><shop_local>서귀포시</shop_local>
<shop_address>서귀포시 천지연로 3-1(서귀동)</shop_address><shop_goods>더블 45000</shop_goods><shop_holiday/><shop_thumb/></item>
</items></Response>"""


def test_goodprice_api_and_address_only_fallback(client):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=GOODPRICE_XML.encode("utf-8"),
                                                               headers={"content-type": "text/xml; charset=UTF-8"}))
    with SessionLocal() as db:
        rep = poi_sources.goodprice_api(db, client=httpx.Client(transport=transport), geocode=False)
        assert rep == {"total": 2, "fetched": 2, "by_category": {"food": 1, "lodging": 1}, "with_coords": 0}
        from app.services.poi_service import find_places_near

        res = find_places_near(db, ORIGIN[0], ORIGIN[1], "lodging", 1000)
    # no coordinates → listed by address and clearly not called "nearby"
    assert res["status"] == "address_only" and res["places"][0]["name"] == "바다해호텔"
    assert res["places"][0]["distance_m"] is None and "좌표가 없어" in res["message"]


def test_shp_missing_prj_is_reported():
    with tempfile.TemporaryDirectory() as d:
        write_synthetic_shp(Path(d), with_prj=False)
        with SessionLocal() as db, pytest.raises(ImportError_, match=".prj"):
            hayoung_shp.run(db, Path(d))


def test_closed_loop_restarts_at_city_hall():
    with tempfile.TemporaryDirectory() as d:
        write_synthetic_shp(Path(d), with_course3=True)
        with SessionLocal() as db:
            rep = hayoung_shp.run(db, Path(d), dry_run=True)
    r3 = rep["routes"]["3"]
    assert r3["rotated_to_shared_start"] is True
    assert haversine_m(*r3["start"], *ORIGIN) < 30 and "missing_courses" not in rep


def test_wifi_api_requires_appkey(monkeypatch):
    monkeypatch.delenv("JEJU_DATAHUB_APPKEY", raising=False)
    with SessionLocal() as db, pytest.raises(ImportError_, match="JEJU_DATAHUB_APPKEY"):
        poi_sources.wifi_api(db)


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
            assert [l["features"][0]["parts"] for l in info["layers"]] == [6, 1]
            report = hayoung_shp.run(db, Path(d), license="테스트")
            # Waypoint positions come from an independent dataset (공공와이파이 AP OpenAPI), ~25 m off the path.
            off = 0.00025
            aps = {"법장사골목길": [perimeter(0.1)[0] + off, perimeter(0.1)[1]], "걸매생태공원": [perimeter(0.2)[0] + off, perimeter(0.2)[1]],
                   "천지연폭포": [perimeter(0.3)[0], perimeter(0.3)[1] + off], "먼 곳": [33.40, 126.40]}
            wifi = poi_sources.wifi_api(db, appkey="test-key", dongs=["서홍동", "동홍동"], client=httpx.Client(transport=_wifi_transport(aps)))
            located = locate_waypoints.run(db, use_geocoder=False)
    r1 = report["routes"]["1"]
    assert r1["dropped_duplicate_parts"] == [3] and r1["repeated_parts"] == [1]  # dup removed, spur walked out and back
    assert r1["start"] == pytest.approx(list(ORIGIN), abs=1e-5)  # shared start of both courses = City Hall
    assert report["missing_courses"] == ["하영올레 3코스"]
    assert wifi == {"calls": 3, "base_date": "20260820", "places": 4, "with_coords": 4, "ref_date": "20260820"}
    loc = located["routes"]["하영올레 1코스"]
    assert loc["located"] == 3 and loc["direction"] == "경유지 순서와 일치", loc
    return route_id(client, "1", "hayoung_olle")


def test_import_builds_geometry(client, hayoung_imported):
    r = client.get(f"/v1/routes/{hayoung_imported}").json()
    assert r["has_geometry"] and 2.1 < r["distance_km"] < 2.3  # 4 × ~500 m + 2 × ~93 m spur
    assert r["path"][0] == pytest.approx(list(ORIGIN))
    alongs = [w["along_m"] for w in r["waypoints"] if w["along_m"] is not None]
    assert len(alongs) == 5 and alongs == sorted(alongs)  # S, three located waypoints, F
    assert alongs[-1] == pytest.approx(r["distance_km"] * 1000, abs=5)
    unlocated = [w for w in r["waypoints"] if w["location"] is None]
    assert {w["name"] for w in unlocated} >= {"새연교", "새섬"}  # nothing invented without a source
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
    # Two batches, each with a check-in: visits loaded from the DB (naive datetimes on SQLite)
    # must sort together with new ones.
    client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0, 0.05, 0.1], t0)})
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0.15, 0.2, 0.25, 0.3], t0 + timedelta(minutes=3))}).json()
    summ = out["summary"]
    assert summ["progress_basis"] == "route_geometry" and 0.25 < summ["actual_progress"] < 0.31
    assert summ["visited_waypoints"] == ["서귀포시청제1청사", "법장사골목길", "걸매생태공원", "천지연폭포"]
    assert summ["next_waypoint"]["code"] == "14" and summ["next_waypoint"]["distance_m"] is None  # not located
    arrivals = [n["title"] for n in client.get("/v1/notifications", headers=auth).json()["notifications"] if n["event_type"] == "waypoint_arrival"]
    assert sorted(arrivals) == sorted(["경유지 도착 · 법장사골목길", "경유지 도착 · 걸매생태공원", "경유지 도착 · 천지연폭포"])
    assert "1F" not in str(summ)  # loop finish not triggered at the start

    # Poor accuracy far away: no deviation alert, no progress change.
    far = 0.003  # ≈ 280 m east of the (north-running) east edge
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0.31, 0.31, 0.31, 0.31], t0 + timedelta(minutes=5), acc=80, offset_lon=far)}).json()
    assert not [n for n in out["notifications"] if n["event_type"] == "off_route"]
    # Good accuracy, sustained > 2 min: "위치 확인" alert, once.
    out = client.post(f"/v1/sessions/{sid}/points", headers=auth, json={"points": _pts([0.31] * 5, t0 + timedelta(minutes=8), offset_lon=far)}).json()
    off = [n for n in out["notifications"] if n["event_type"] == "off_route"]
    assert len(off) == 1 and off[0]["title"].startswith("위치 확인")
    assert 0.25 < out["summary"]["actual_progress"] < 0.31

    r = client.post("/v1/agent/chat", headers=auth, json={"message": "다음 경유지까지 얼마나 남았어?", "trip_id": trip}).json()
    assert "서귀포칠십리시공원" in r["reply"]
    client.post(f"/v1/sessions/{sid}/end", headers=auth)


def test_hayoung_bundles_and_plan(client, auth, hayoung_imported):
    bundles = client.get("/v1/routes/hayoung/bundles", headers=auth).json()["bundles"]
    day = next(b for b in bundles if b["label"] == "하루")
    assert day["distance_km"] and day["duration_min"]
    spare = next(b for b in bundles if b["label"] == "여유 일정")
    assert spare["distance_km"] is None  # course 3 not imported → honest unknown
    trip = make_trip(client, auth, "2026-11-10", "2026-11-11")
    h2, h3 = route_id(client, "2", "hayoung_olle"), route_id(client, "3", "hayoung_olle")
    drafts = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={"route_ids": [hayoung_imported, h2, h3]}).json()["drafts"]
    assert drafts[0]["route"]["trail_type"] == "hayoung_olle" and "1코스 + 2코스" in drafts[0]["note"]
    assert [r["course_no"] for r in drafts[0]["extra_routes"]] == ["2"]  # both fit → bundled into one day
    assert drafts[1]["route"]["course_no"] == "3" and "거리 데이터 적재 전" in drafts[1]["note"]


def test_olle_plan_chains_end_to_start(client, auth):
    client.patch("/v1/me/preferences", headers=auth, json={"daily_distance_target_km": 25, "walk_end_time": "18:00", "interests": []})
    trip = make_trip(client, auth, "2026-11-10", "2026-11-12")
    drafts = client.post(f"/v1/trips/{trip}/plan", headers=auth, json={}).json()["drafts"]
    routes = [d["route"] for d in drafts if d["route"]]
    assert all(r["trail_type"] == "jeju_olle" for r in routes)
    assert [r["course_no"] for r in routes] == ["4", "5", "6"]  # 표선→남원→쇠소깍→서귀포
    for a, b in zip(routes, routes[1:]):
        assert b["start_point"] == a["end_point"]


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
