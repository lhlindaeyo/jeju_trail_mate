"""제주올레 공식 시작·종점 좌표(jejuolle.org) + OSM 경로선 import. Synthetic data only — not real course geometry."""
import json
import tempfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.data_import import olle_official, olle_osm
from app.data_import.common import ImportError_
from app.db import SessionLocal
from app.models import TrailRoute
from app.services.geo import haversine_m, path_length_m

ROAD_JS = ('x=1;const p={"01":{start:{lat:33.47721,lng:126.89584},end:{lat:33.45201,lng:126.92432}},'
           '"01_1":{start:{lat:33.5095,lng:126.9426},end:{lat:33.5095,lng:126.9426}},'
           '10:{start:{lat:33.2399,lng:126.3351},end:{lat:33.2191,lng:126.2528}},'
           '"15_A":{start:{lat:33.4191,lng:126.2624},end:{lat:33.4671,lng:126.3387}},'
           '"18_1":{start:{lat:33.9635,lng:126.2961},end:{lat:33.9448,lng:126.3292}}};')

START = (33.3000, 126.3000)  # synthetic straight course heading north, ≈ 2.0 km
STEP = 0.0009  # ≈ 100 m of latitude


def line(i0: int, i1: int) -> list[list[float]]:
    return [[START[0] + i * STEP, START[1]] for i in range(i0, i1 + 1)]


def member(pts, role=""):
    return {"type": "way", "role": role, "geometry": [{"lat": a, "lon": b} for a, b in pts]}


def test_endpoint_table_parsing():
    out = olle_official.parse_endpoints(ROAD_JS)
    assert set(out) == {"1", "1-1", "10", "15-A", "18-1"}
    assert out["10"]["start"] == [33.2399, 126.3351]
    assert olle_official.course_no_from_key("03_B") == "3-B"


def test_fetch_follows_hashed_assets():
    pages = {"/trail": '<script src="/assets/trail_ko_router-Ab1.js">',
             "/assets/trail_ko_router-Ab1.js": 'import("./Road-Zz_9.js")',
             "/assets/Road-Zz_9.js": ROAD_JS * 5}  # repeated keys keep the first entry

    def handler(req):
        body = pages.get(req.url.path)
        return httpx.Response(200 if body else 404, text=body or "")

    with tempfile.TemporaryDirectory() as d, pytest.raises(ImportError_, match="5개만"):
        # five distinct courses is far fewer than the real 29 → treated as a site change
        olle_official.fetch(Path(d), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_course_no_from_osm_tags():
    f = olle_osm.course_no_from_tags
    assert f({"ref": "JOT6"}) == "6"
    assert f({"ref": "JOT3A"}) == "3-A"
    assert f({"name": "올레길 16코스"}) == "16"
    assert f({"name": "Ollegil 8"}) == "8"
    assert f({"name": "Jeju Olle trail 7"}) == "7"
    assert f({"name": "Olle trail 6(B)"}) == "6-B"
    assert f({"name": "제주올레 1-1코스"}) == "1-1"
    assert f({"name": "올레길5 (Ollegil 5) wheelchair", "ref": "JOT5"}) is None
    assert f({"name": "한라산 둘레길"}) is None


def test_chain_reorders_and_flips_ways():
    ways = [line(14, 20), list(reversed(line(0, 7))), line(7, 14)]
    out = olle_osm.chain(ways, start=[START[0], START[1]])
    assert out["path"][0] == [START[0], START[1]]
    assert out["path"][-1] == line(20, 20)[0]
    assert out["gaps_m"] == [] and out["unused_m"] == 0
    assert abs(path_length_m(out["path"]) - 2000) < 30


@pytest.fixture
def course99(client):
    with SessionLocal() as db:
        r = TrailRoute(trail_type="jeju_olle", operator="test", course_no="99", name="test 99", distance_km=2.0,
                       source_url="test")
        db.add(r)
        db.commit()
    yield
    with SessionLocal() as db:
        db.delete(db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle", TrailRoute.course_no == "99")))
        db.commit()


def _route99(db):
    return db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle", TrailRoute.course_no == "99"))


def test_endpoints_then_osm_import(client, course99):
    end = line(20, 20)[0]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        (root / "official").mkdir()
        (root / "official" / olle_official.FILE_NAME).write_text(json.dumps({"fetched_at": "2026-11-16T00:00:00Z", "courses": {
            "99": {"key": "99", "start": list(START), "end": end},
            "98": {"key": "98", "start": [40.0, 126.0], "end": [40.0, 126.0]}}}), encoding="utf-8")
        relations = [
            {"type": "relation", "id": 1, "tags": {"name": "Olle trail 99"},  # complete, out of order
             "members": [member(line(10, 20)), member(list(reversed(line(0, 10)))), member(line(5, 6), role="alternative")]},
            {"type": "relation", "id": 2, "tags": {"ref": "JOT99"}, "members": [member(line(0, 9))]},  # half missing
            {"type": "relation", "id": 3, "tags": {"name": "Olle trail 99 wheelchair"}, "members": [member(line(0, 20))]},
        ]
        (root / "osm").mkdir()
        (root / "osm" / olle_osm.FILE_NAME).write_text(json.dumps({"osm3s": {"timestamp_osm_base": "2026-11-16T00:00:00Z"},
                                                                   "elements": relations}), encoding="utf-8")
        with SessionLocal() as db:
            ep = olle_official.run(db, root / "official")
            assert any(u["course"] == "99" for u in ep["updated"])
            assert any("98코스: 제주 밖" in p for p in ep["problems"])
            rep = olle_osm.run(db, root / "osm")
            assert rep["loaded"]["99"]["relation"] == 1
            assert any("wheelchair" in s for s in rep["skipped"])
            r = _route99(db)
            assert r.has_geometry and len(r.segments) == 2
            assert haversine_m(*r.path[0], *START) < 1 and haversine_m(*r.path[-1], *end) < 1
            assert r.distance_km == 2.0  # official distance is kept, the line only draws the course


def test_osm_import_rejects_mismatch(client, course99):
    with SessionLocal() as db:
        r = _route99(db)
        r.start_lat, r.start_lon = START
        r.end_lat, r.end_lon = line(20, 20)[0]
        db.commit()
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / olle_osm.FILE_NAME).write_text(json.dumps({"elements": [
            {"type": "relation", "id": 7, "tags": {"ref": "JOT99"}, "members": [member(line(0, 12))]}]}), encoding="utf-8")
        with SessionLocal() as db:
            rep = olle_osm.run(db, Path(d))
            assert "99" in rep["rejected"] and not rep["loaded"]
            problems = rep["rejected"]["99"][0]["problems"]
            assert any("길이" in p for p in problems) and any("종점" in p for p in problems)
            assert not _route99(db).has_geometry
