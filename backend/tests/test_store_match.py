"""Store-list matching (상가정보 / 인허가 files) and the 상가정보 API fetcher — synthetic data, no network.
Column names follow the published layouts as understood without the real files; real files are checked by
`python -m app.data_import geocode --address ...` once downloaded."""
import json

import httpx
import pytest
from pyproj import Transformer

from app.data_import import poi_sources, sbiz_fetch
from app.data_import.common import ImportError_
from app.db import SessionLocal
from app.geocode import GeocoderChain, get_geocoder
from app.geocode.store_match import StoreMatchGeocoder, similar_names

_TO_5174 = Transformer.from_crs("EPSG:4326", "EPSG:5174", always_xy=True)


def _sbiz(path, items):
    path.write_text(json.dumps({"stores": items}, ensure_ascii=False), encoding="utf-8")


def _item(name, addr, lat, lon, branch=""):
    return {"bizesNm": name, "brchNm": branch, "rdnmAdr": addr, "lat": str(lat), "lon": str(lon)}


@pytest.fixture
def stores(tmp_path):
    _sbiz(tmp_path / "sbiz_50130_I2.json", [
        _item("바당국수", "제주특별자치도 서귀포시 동문동로 21-1", 33.2470, 126.5610),
        _item("화순맛집", "제주특별자치도 서귀포시 안덕면 화순중앙로 76", 33.2463, 126.3360),
        _item("카페 A", "제주특별자치도 서귀포시 중앙로 10", 33.2500, 126.5600),
        _item("미용실 B", "제주특별자치도 서귀포시 중앙로 10", 33.2500, 126.5601),  # same building
        _item("건물1", "제주특별자치도 서귀포시 태평로 5", 33.2400, 126.5600),
        _item("건물2", "제주특별자치도 서귀포시 태평로 5", 33.2420, 126.5600),  # same address, 220 m apart
        _item("중앙식당", "제주특별자치도 제주시 중앙로 1", 33.5100, 126.5210),
    ])
    x, y = _TO_5174.transform(126.5700, 33.2440)
    csv = ("사업장명,상세영업상태명,도로명전체주소,좌표정보(X),좌표정보(Y)\n"
           f"신라식당,영업,제주특별자치도 서귀포시 칠십리로 100,{x:.2f},{y:.2f}\n"
           f"폐업식당,폐업,제주특별자치도 서귀포시 칠십리로 102,{x:.2f},{y:.2f}\n")
    (tmp_path / "permits.csv").write_bytes(csv.encode("cp949"))
    return StoreMatchGeocoder(tmp_path)


def test_similar_names():
    assert similar_names("화순맛집", "화순맛집 본점") and similar_names("(주)바당국수", "바당국수")
    assert similar_names("바당국수 서귀포", "바당국수")
    assert not similar_names("바당국수", "해녀의집") and not similar_names("", "바당국수")


def test_match_by_name_and_address(stores):
    h = stores.geocode_business("바당국수", "서귀포시 동문동로 21-1(서귀동)")
    assert h and h.storable and h.provider == "stores" and h.precision == "store"
    assert (h.lat, h.lon) == pytest.approx((33.2470, 126.5610))
    # 읍/면 in the address must not break the road match
    assert stores.geocode_business("화순 맛집", "서귀포시 안덕면 화순중앙로 76").lon == pytest.approx(126.3360)


def test_permit_csv_uses_5174_coordinates_and_skips_closed_shops(stores):
    h = stores.geocode_business("신라식당", "서귀포시 칠십리로 100")
    assert (h.lat, h.lon) == pytest.approx((33.2440, 126.5700), abs=1e-4)
    assert stores.geocode_business("폐업식당", "서귀포시 칠십리로 102") is None


def test_different_name_same_building_falls_back_to_the_building_point(stores):
    h = stores.geocode_business("아무개 이발소", "서귀포시 중앙로 10")
    assert h and h.precision == "address" and h.lat == pytest.approx(33.2500, abs=1e-4)


def test_ambiguous_or_missing_addresses_are_never_guessed(stores):
    assert stores.geocode_business("모르는집", "서귀포시 태평로 5") is None  # two shops 220 m apart, no name match
    assert stores.geocode_business("건물2", "서귀포시 태평로 5").lat == pytest.approx(33.2420)  # name decides
    assert stores.geocode_business("바당국수", "서귀포시 동문동로 99") is None  # unknown number
    assert stores.geocode_business("중앙식당", "서귀포시 중앙로 1") is None  # that one is in 제주시
    assert stores.geocode_business("중앙식당", "제주시 중앙로 1").lat == pytest.approx(33.5100)


def test_chain_business_to_xy_prefers_stores_with_names_and_skips_unstorable(stores):
    class Unstorable:
        name, storable = "vworld", False

        def geocode_address(self, a):
            raise AssertionError("must not be consulted for storing")

    chain = GeocoderChain([Unstorable(), stores])
    assert chain.business_to_xy("바당국수", "서귀포시 동문동로 21-1") == pytest.approx((33.2470, 126.5610))
    assert chain.business_to_xy("없는집", "서귀포시 없는로 1") is None


def test_broken_or_unrecognised_files_do_not_hide_good_ones(tmp_path):
    (tmp_path / "junk.csv").write_bytes("a,b\n1,2\n".encode("utf-8"))
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    _sbiz(tmp_path / "ok.json", [_item("바당국수", "서귀포시 동문동로 21-1", 33.247, 126.561)])
    assert StoreMatchGeocoder(tmp_path).geocode_business("바당국수", "서귀포시 동문동로 21-1")
    assert not StoreMatchGeocoder(tmp_path / "missing").available()


def test_chain_from_settings_includes_stores_only_when_files_exist(monkeypatch, tmp_path):
    import app.geocode as g
    from app.config import Settings

    cfg = {"stores_dir": str(tmp_path), "juso_entrc_dir": str(tmp_path / "none"), "geocoder_chain": "local,stores"}
    monkeypatch.setattr(g, "get_settings", lambda: Settings(_env_file=None, **cfg))
    assert not get_geocoder()
    _sbiz(tmp_path / "x.json", [_item("a", "서귀포시 중앙로 1", 33.25, 126.56)])
    assert [p.name for p in get_geocoder().providers] == ["stores"]


GOODPRICE_XML = """<?xml version="1.0" encoding="UTF-8"?><Response><total>2</total><page>1</page><pageSize>50</pageSize><items>
<item><shop_code>001</shop_code><shop_title>바당국수</shop_title><shop_tel>064-000-0000</shop_tel><shop_local>서귀포시</shop_local>
<shop_address>서귀포시 동문동로 21-1(서귀동)</shop_address><shop_goods>멸치국수 5,000원</shop_goods><shop_holiday>연중</shop_holiday><shop_thumb>a.jpg</shop_thumb></item>
<item><shop_code>004</shop_code><shop_title>바다해호텔</shop_title><shop_tel>064-762-5300</shop_tel><shop_local>서귀포시</shop_local>
<shop_address>서귀포시 천지연로 3-1(서귀동)</shop_address><shop_goods>더블 45000</shop_goods><shop_holiday/><shop_thumb/></item>
</items></Response>"""


def test_goodprice_import_fills_coordinates_for_matched_shops_only(client, stores):
    chain = GeocoderChain([stores])
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=GOODPRICE_XML.encode("utf-8")))
    with SessionLocal() as db:
        rep = poi_sources.goodprice_api(db, client=httpx.Client(transport=transport), dry_run=True,
                                        geocode=lambda addr, name=None: chain.business_to_xy(name, addr))
    assert rep["fetched"] == 2 and rep["with_coords"] == 1  # 바다해호텔 is not in any store list -> stays address-only


# --- 상가정보 API fetcher ---------------------------------------------------------------------------
def _api(pages, seen):
    def handler(req):
        q = dict(req.url.params)
        seen.append(q)
        body = pages[int(q["pageNo"]) - 1] if q["indsLclsCd"] == "I2" else {"items": [], "totalCount": 0}
        return httpx.Response(200, json={"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": body})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sbiz_fetch_paginates_and_writes_json(tmp_path):
    item = lambda n: {"bizesNm": n, "rdnmAdr": f"서귀포시 중앙로 {n}", "lat": "33.25", "lon": "126.56", "extra": "dropped"}
    seen = []
    rep = sbiz_fetch.fetch("SECRET", tmp_path, classes=("I2", "I1"), client=_api(
        [{"items": [item("1"), item("2")], "totalCount": 3}, {"items": item("3"), "totalCount": 3}], seen))
    assert rep["files"]["sbiz_50130_I2.json"] == {"stores": 3, "with_coords": 3} and rep["files"]["sbiz_50130_I1.json"]["stores"] == 0
    assert [q["pageNo"] for q in seen if q["indsLclsCd"] == "I2"] == ["1", "2"]
    assert seen[0]["divId"] == "signguCd" and seen[0]["key"] == "50130" and seen[0]["serviceKey"] == "SECRET"
    saved = json.loads((tmp_path / "sbiz_50130_I2.json").read_text(encoding="utf-8"))
    assert [s["bizesNm"] for s in saved["stores"]] == ["1", "2", "3"] and "extra" not in saved["stores"][0]
    # and the saved file is exactly what the matcher reads
    assert StoreMatchGeocoder(tmp_path).geocode_business("2", "서귀포시 중앙로 2")


def test_sbiz_fetch_reports_key_and_api_errors_without_leaking_the_key(tmp_path):
    with pytest.raises(ImportError_, match="SBIZ_SERVICE_KEY"):
        sbiz_fetch.fetch(None, tmp_path)
    err = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(
        200, json={"header": {"resultCode": "30", "resultMsg": "SERVICE KEY IS NOT REGISTERED ERROR."}})))
    with pytest.raises(ImportError_, match="SERVICE KEY IS NOT REGISTERED"):
        sbiz_fetch.fetch("SECRET", tmp_path, client=err)
    html = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html>SECRET denied</html>")))
    with pytest.raises(ImportError_) as e:
        sbiz_fetch.fetch("SECRET", tmp_path, client=html)
    assert "SECRET" not in str(e.value)
