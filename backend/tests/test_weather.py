import httpx
import pytest

from app.config import Settings
from app.services import weather_service
from app.services.geo import now_kst


def _ok_body():
    slot = now_kst().strftime("%Y%m%d%H00")
    d, t = slot[:8], slot[8:]
    items = [{"fcstDate": d, "fcstTime": t, "category": c, "fcstValue": v}
             for c, v in [("TMP", "18"), ("SKY", "1"), ("PTY", "0"), ("POP", "10"), ("WSD", "3")]]
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL_SERVICE"},
                         "body": {"items": {"item": items}}}}


@pytest.fixture(autouse=True)
def _clear_cache():
    weather_service._CACHE.clear()
    yield
    weather_service._CACHE.clear()


def _keys(monkeypatch, hub, portal):
    cfg = Settings(_env_file=None, kma_apihub_key=hub or None, kma_service_key=portal or None)
    monkeypatch.setattr(weather_service, "get_settings", lambda: cfg)


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_apihub_is_tried_first_with_authkey(monkeypatch):
    _keys(monkeypatch, hub="hub-key", portal="portal-key")
    seen = []

    def handler(req):
        seen.append(req.url)
        return httpx.Response(200, json=_ok_body())

    w = weather_service.get_weather(33.5, 126.53, client=_client(handler))
    assert w["status"] == "ok" and "API허브" in w["source"]
    assert len(seen) == 1 and seen[0].host == "apihub.kma.go.kr"
    assert seen[0].params["authKey"] == "hub-key" and "serviceKey" not in seen[0].params
    assert w["forecast"][0]["temp_c"] == "18"


def test_falls_back_to_data_go_kr_when_apihub_fails(monkeypatch):
    _keys(monkeypatch, hub="hub-key", portal="portal-key")

    def handler(req):
        if req.url.host == "apihub.kma.go.kr":
            return httpx.Response(401, json={"result": {"status": 401}})
        assert req.url.params["serviceKey"] == "portal-key"
        return httpx.Response(200, json=_ok_body())

    w = weather_service.get_weather(33.5, 126.53, client=_client(handler))
    assert w["status"] == "ok" and "공공데이터포털" in w["source"]


def test_all_sources_fail_reports_each_reason(monkeypatch):
    _keys(monkeypatch, hub="hub-key", portal="portal-key")
    w = weather_service.get_weather(33.5, 126.53, client=_client(lambda req: httpx.Response(403, text="no")))
    assert w["status"] == "unavailable"
    assert "API허브: HTTP 403" in w["reason"] and "공공데이터포털: HTTP 403" in w["reason"]


def test_apihub_only(monkeypatch):
    _keys(monkeypatch, hub="hub-key", portal="")
    w = weather_service.get_weather(33.5, 126.53, client=_client(lambda req: httpx.Response(200, json=_ok_body())))
    assert w["status"] == "ok", w
    assert w["source_url"] == "https://apihub.kma.go.kr/"
