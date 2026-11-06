"""Geocoder providers + chain. The local-DB fixture is SYNTHETIC: rows follow the layout assumed in
app/geocode/local_juso.py, not a real download (that file arrives after the juso.go.kr approval)."""
import httpx
import pytest
from pyproj import Transformer

from app.geocode import GeocoderChain, GeocoderError, get_geocoder
from app.geocode.local_juso import LocalJusoGeocoder, inspect, parse_address
from app.geocode.vworld import VWorldGeocoder

_TO_UTMK = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def _row(sigungu, emd, bunji, ho, road, main, sub, bname, lat, lon, sido="제주특별자치도"):
    x, y = _TO_UTMK.transform(lon, lat)
    cols = [""] * 27
    cols[0:3] = ["50130", "1", "5013010100"]
    cols[3:10] = [sido, sigungu, emd, "", "0", str(bunji), str(ho)]
    cols[10:15] = ["501303200001", road, "0", str(main), str(sub)]
    cols[20] = bname
    cols[25:27] = [f"{x:.1f}", f"{y:.1f}"]
    return "|".join(cols)


ROWS = [
    _row("서귀포시", "서홍동", 123, 4, "중앙로62번길", 5, 0, "", 33.2480, 126.5650),
    _row("서귀포시", "서홍동", 10, 0, "중앙로", 17, 2, "서귀포시청", 33.2531, 126.5609),
    _row("서귀포시", "정방동", 1, 0, "태평로", 3, 0, "", 33.2460, 126.5640),
    _row("서귀포시", "중앙동", 50, 0, "중앙로", 1, 0, "", 33.2470, 126.5620),
    _row("제주시", "이도이동", 50, 0, "중앙로", 1, 0, "", 33.5100, 126.5210),  # same road/number in the other city
    _row("부산광역시", "연산동", 1, 0, "중앙대로", 1, 0, "", 35.18, 129.07, sido="부산광역시"),
]


@pytest.fixture
def local(tmp_path):
    (tmp_path / "entrc_jeju.txt").write_bytes("\n".join(ROWS).encode("cp949"))
    return LocalJusoGeocoder(tmp_path)


def test_parse_address_variants():
    assert parse_address("제주특별자치도 서귀포시 중앙로 62번길 5 (서홍동)") == {"sigungu": "서귀포시", "road": ("중앙로62번길", 5, 0)}
    assert parse_address("서귀포시 중앙로 17-2")["road"] == ("중앙로", 17, 2)
    assert parse_address("서귀포시 서홍동 123-4") == {"sigungu": "서귀포시", "parcel": ("서홍동", False, 123, 4)}
    assert parse_address("제주시청로 5")["sigungu"] is None  # '제주시' inside a road name is not a city token
    assert parse_address("") is None and parse_address("주소 없음") is None


def test_parse_address_with_eup_myeon_prefix_and_odd_names():
    # real addresses carry 읍/면 and numeric road names; the prefix must not become part of the road name
    assert parse_address("서귀포시 성산읍 신양로122번길 30-8")["road"] == ("신양로122번길", 30, 8)
    assert parse_address("서귀포시 안덕면 화순중앙로 76")["road"] == ("화순중앙로", 76, 0)
    assert parse_address("서귀포시 안덕면 화순중앙로76")["road"] == ("화순중앙로", 76, 0)
    assert parse_address("서귀포시 516로 462")["road"] == ("516로", 462, 0)
    assert parse_address("서귀포시 신서로48번길 49, 101호 (서홍동, 신서한빛)")["road"] == ("신서로48번길", 49, 0)
    assert parse_address("서귀포시 성산읍 한도로 242번길 10-19")["road"] == ("한도로242번길", 10, 19)
    assert parse_address("서귀포시 안덕면 화순리 123-4")["parcel"] == ("화순리", False, 123, 4)


def test_local_road_and_parcel_lookup(local):
    h = local.geocode_address("제주특별자치도 서귀포시 중앙로 62번길 5")
    assert h and h.storable and h.provider == "local" and abs(h.lat - 33.2480) < 1e-4 and abs(h.lon - 126.5650) < 1e-4
    assert local.geocode_address("서귀포시 서홍동 123-4").lat == pytest.approx(33.2480, abs=1e-4)  # parcel (지번) address
    assert local.geocode_address("서귀포시 중앙로 17-2").lat == pytest.approx(33.2531, abs=1e-4)
    assert local.geocode_address("서귀포시 없는길 9") is None


def test_same_road_in_both_cities_is_not_guessed(local):
    assert local.geocode_address("중앙로 1") is None  # 서귀포 and 제주 are ~30 km apart
    assert local.geocode_address("제주시 중앙로 1").lat == pytest.approx(33.5100, abs=1e-4)
    assert local.geocode_address("서귀포시 중앙로 1").lat == pytest.approx(33.2470, abs=1e-4)


def test_local_place_search_by_building_name(local):
    assert local.search_place("서귀포시청")[0].lat == pytest.approx(33.2531, abs=1e-4)
    assert local.search_place("서귀포시청", center=(33.51, 126.52), radius_m=3000) == []  # outside the radius
    assert local.search_place("천지연폭포") == []  # landmarks are not buildings


def test_other_provinces_are_ignored(local):
    assert local.geocode_address("부산광역시 중앙대로 1") is None


def test_unexpected_layout_fails_loudly(tmp_path):
    (tmp_path / "x.txt").write_bytes("\n".join("|".join(["제주특별자치도"] + ["가"] * 28) for _ in range(5)).encode("cp949"))
    with pytest.raises(GeocoderError, match="juso-inspect"):
        LocalJusoGeocoder(tmp_path).geocode_address("서귀포시 중앙로 1")


def test_zip_and_utf8_files_load_and_inspect_reports(tmp_path):
    import zipfile

    with zipfile.ZipFile(tmp_path / "entrc_jeju.zip", "w") as z:
        z.writestr("entrc_jeju.txt", "\n".join(ROWS).encode("utf-8"))
    g = LocalJusoGeocoder(tmp_path)
    assert g.available() and g.geocode_address("서귀포시 태평로 3")
    rep = inspect(tmp_path)
    assert rep["files"][0]["jeju_rows"] == 5 and rep["files"][0]["layout_ok_in_first_200_jeju_rows"] == "5/5"
    assert not LocalJusoGeocoder(tmp_path / "missing").available()


# --- VWorld ---------------------------------------------------------------------------------------
def _vworld(handler):
    return VWorldGeocoder("KEY", domain="localhost", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_vworld_address_tries_road_then_parcel_and_is_not_storable():
    seen = []

    def handler(req):
        q = dict(req.url.params)
        seen.append(q["type"])
        assert q["key"] == "KEY" and q["domain"] == "localhost"
        if q["type"] == "ROAD":
            return httpx.Response(200, json={"response": {"status": "NOT_FOUND"}})
        return httpx.Response(200, json={"response": {"status": "OK", "result": {"point": {"x": "126.565", "y": "33.248"}}}})

    g = _vworld(handler)
    hit = g.geocode_address("서귀포시 서홍동 123-4")
    assert seen == ["ROAD", "PARCEL"] and hit.lat == pytest.approx(33.248) and hit.storable is False


def test_vworld_place_search_filters_by_radius_and_survives_errors():
    items = [{"title": "새연교", "point": {"x": "126.5598", "y": "33.2398"}},
             {"title": "다른 새연교", "point": {"x": "126.9", "y": "33.5"}}]

    def ok(req):
        assert dict(req.url.params)["type"] == "PLACE"
        return httpx.Response(200, json={"response": {"status": "OK", "result": {"items": items}}})

    hits = _vworld(ok).search_place("새연교", center=(33.24, 126.56), radius_m=3000)
    assert [h.label for h in hits] == ["새연교"] and not hits[0].storable
    err = _vworld(lambda req: httpx.Response(200, json={"response": {"status": "ERROR", "error": {"text": "INCORRECT_KEY"}}}))
    assert err.geocode_address("x") is None and err.search_place("x") == []
    assert _vworld(lambda req: httpx.Response(500)).geocode_address("x") is None


# --- chain ----------------------------------------------------------------------------------------
class _Fake:
    def __init__(self, name, storable, hit=None, boom=False):
        self.name, self.storable, self.hit, self.boom = name, storable, hit, boom

    def geocode_address(self, a):
        if self.boom:
            raise RuntimeError("network")
        return self.hit

    def search_place(self, n, center=None, radius_m=3000):
        return [self.hit] if self.hit else []


def test_chain_never_returns_unstorable_coordinates_to_importers():
    from app.geocode import GeoHit

    v = _Fake("vworld", False, GeoHit(33.2, 126.5, "a", "vworld", False))
    chain = GeocoderChain([v])
    assert chain.geocode_address("a").provider == "vworld"  # fine for a lookup / suggestion
    assert chain.address_to_xy("a") is None  # but never what gets stored
    loc = _Fake("local", True, GeoHit(33.3, 126.6, "a", "local", True))
    assert GeocoderChain([v, loc]).address_to_xy("a") == (33.3, 126.6)
    assert GeocoderChain([_Fake("x", True, boom=True), loc]).address_to_xy("a") == (33.3, 126.6)  # failure -> next provider


def test_chain_from_settings_skips_unconfigured_providers(monkeypatch, tmp_path):
    import app.geocode as g
    from app.config import Settings

    (tmp_path / "e.txt").write_bytes("\n".join(ROWS).encode("cp949"))
    cfg = {"juso_entrc_dir": str(tmp_path), "vworld_api_key": "K", "stores_dir": str(tmp_path / "no-stores")}
    monkeypatch.setattr(g, "get_settings", lambda: Settings(_env_file=None, **cfg))
    assert [p.name for p in get_geocoder().providers] == ["local", "vworld"]  # kakao has no key
    assert [p.name for p in get_geocoder("vworld,local").providers] == ["vworld", "local"]
    cfg["juso_entrc_dir"] = str(tmp_path / "nothing")
    assert [p.name for p in get_geocoder().providers] == ["vworld"]
    cfg["vworld_api_key"] = None
    assert not get_geocoder()
