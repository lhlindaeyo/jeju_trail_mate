"""대표 관광지·이야기: 내장 큐레이션만 쓰고, 목록에 없는 곳은 지어내지 않는다."""
from app.services import tour_service


def test_find_spots_nearest_first_and_featured_fallback():
    near = tour_service.find_spots(33.2445, 126.5715, limit=3)  # 정방폭포 앞
    assert near["basis"] == "nearby" and near["places"][0]["name"] == "정방폭포"
    assert near["places"][0]["distance_m"] < near["places"][1]["distance_m"]
    featured = tour_service.find_spots(limit=3)
    assert featured["basis"] == "featured" and featured["note"] and featured["places"][0]["distance_m"] is None
    assert "대략" in featured["caveat"] and featured["source"]


def test_story_by_name_and_unknown_spot():
    ok = tour_service.get_story("정방폭포")
    assert ok["status"] == "ok" and "서불과차" in ok["stories"][0]["story"]
    missing = tour_service.get_story("없는관광지")
    assert missing["status"] == "not_found" and "정방폭포" in missing["available"]


def test_every_spot_is_on_jeju_and_has_text():
    for p in tour_service._data()["spots"]:
        assert 33.0 < p["lat"] < 33.7 and 126.0 < p["lon"] < 127.0
        assert p["summary"] and p["story"] and p["name"]


def test_official_text_replaces_curated_story_with_source():
    import json
    from pathlib import Path

    import httpx

    from app.config import get_settings
    from app.data_import import tour_api

    detail = {"title": "정방폭포", "overview": "정방폭포는 바다로 바로 떨어지는 폭포다.<br>서불이 '서불과지'를 새겼다는 이야기가 있다.",
              "mapx": "126.5730", "mapy": "33.2447", "addr1": "제주특별자치도 서귀포시", "modifiedtime": "20260630103645",
              "cpyrhtDivCd": "Type1"}

    def handler(req):
        q = dict(req.url.params)
        if req.url.path.endswith("searchKeyword2"):
            items = [{"title": "소정방폭포", "contentid": "1", "contenttypeid": "12", "addr1": "제주특별자치도", "mapx": "0", "mapy": "0"},
                     {"title": "정방폭포", "contentid": "126437", "contenttypeid": "12", "addr1": "제주특별자치도", "mapx": "0", "mapy": "0"}] \
                if q["keyword"] == "정방폭포" else []
        else:
            items = [detail] if q["contentId"] == "126437" else []
        return httpx.Response(200, json={"response": {"body": {"items": {"item": items} if items else ""}}})

    folder = Path(get_settings().tour_api_dir)
    try:
        rep = tour_api.fetch(folder, "test-key", client=httpx.Client(transport=httpx.MockTransport(handler)))
        assert rep["matched"] == 1 and "천지연폭포" in rep["not_found"]
        assert json.loads((folder / "kto_spots.json").read_text(encoding="utf-8"))["spots"]["jeongbang"]["contentid"] == "126437"
        tour_service.reload()
        story = tour_service.get_story("정방폭포")
        assert story["stories"][0]["official"] and "서불과지" in story["stories"][0]["story"] and "<br" not in story["stories"][0]["story"]
        assert story["source"] == "한국관광공사 TourAPI 국문 관광정보 서비스" and story["fetched_at"] is None
        assert story["source_url"] == "https://www.data.go.kr/data/15101578/openapi.do" and "공식 안내문" in story["caveat"]
        mixed = tour_service.find_spots(33.2445, 126.5715, limit=3)  # 정방폭포 (official) + curated neighbours
        assert "한국관광공사" in mixed["source"] and "모음" in mixed["source"]  # both sources named
    finally:
        (folder / "kto_spots.json").unlink(missing_ok=True)
        tour_service.reload()


HERE = (33.2447, 126.5730)  # 정방폭포 앞
OFFICIAL = {"source": "한국관광공사 TourAPI 국문 관광정보 서비스", "source_url": "https://www.data.go.kr/data/15101578/openapi.do",
            "fetched_at": None}


def _tourapi(calls: list):
    """Fake KorService2: two places near HERE (a 음식점 that must be skipped) plus a keyword hit."""
    import httpx

    near = [{"title": "소정방폭포", "contentid": "129405", "contenttypeid": "12", "addr1": "제주특별자치도 서귀포시", "dist": "180.4"},
            {"title": "식당", "contentid": "9", "contenttypeid": "39", "addr1": "제주특별자치도 서귀포시", "dist": "50"},
            {"title": "서복전시관", "contentid": "1200", "contenttypeid": "14", "addr1": "제주특별자치도 서귀포시 칠십리로", "dist": "410"}]
    details = {"129405": "정방폭포 동쪽의 작은 폭포다.<br>여름 물맞이 명소로 알려져 있다.", "1200": "서불이 다녀갔다는 이야기를 전시한다.",
               "77": "수월봉은 고산리 해안의 작은 화산체다."}

    def handler(req):
        q = dict(req.url.params)
        op = req.url.path.rsplit("/", 1)[-1]
        calls.append((op, q))
        if op == "locationBasedList2":
            items = near
        elif op == "searchKeyword2":
            items = [{"title": "수월봉", "contentid": "77", "contenttypeid": "12", "addr1": "제주특별자치도 제주시 한경면"}] \
                if q["keyword"] == "수월봉" else []
        else:
            cid = q["contentId"]
            items = [{"title": {"129405": "소정방폭포", "1200": "서복전시관", "77": "수월봉"}[cid], "overview": details[cid]}]
        return httpx.Response(200, json={"response": {"body": {"items": {"item": items}}}})

    return lambda: httpx.Client(transport=httpx.MockTransport(handler))


def _fresh_caches():
    tour_service._list_cache.clear()
    tour_service._detail_cache.clear()


def test_story_here_uses_tourapi_nearby_with_distance(monkeypatch):
    calls = []
    monkeypatch.setattr(tour_service, "_http", _tourapi(calls))
    monkeypatch.setattr(tour_service, "_api_key", lambda: "test-key")
    _fresh_caches()
    r = tour_service.get_story(lat=HERE[0], lon=HERE[1])
    assert r["basis"] == "nearby_official"
    assert [s["name"] for s in r["stories"]] == ["소정방폭포", "서복전시관"]  # nearest first, 음식점 skipped
    assert r["stories"][0]["distance_m"] == 180 and "<br" not in r["stories"][0]["story"]
    assert {k: r[k] for k in OFFICIAL} == OFFICIAL
    q = next(p for op, p in calls if op == "locationBasedList2")
    assert q["mapX"] == "126.573" and q["mapY"] == "33.245"  # rounded to ~100 m before leaving the server
    # a place outside the 18 curated spots is looked up by name
    other = tour_service.get_story("수월봉")
    assert other["basis"] == "official_search" and "화산체" in other["stories"][0]["story"]
    _fresh_caches()


def test_nearby_merges_official_spots_the_location_index_misses(monkeypatch):
    calls = []
    monkeypatch.setattr(tour_service, "_http", _tourapi(calls))
    monkeypatch.setattr(tour_service, "_api_key", lambda: "test-key")
    _fresh_caches()
    data = tour_service._data()
    spots = [{**p, "official": True, "contentid": "126437", "story": "정방폭포는 바다로 바로 떨어진다."} if p["id"] == "jeongbang" else p
             for p in data["spots"]]
    monkeypatch.setattr(tour_service, "_data", lambda: {**data, "spots": spots})
    r = tour_service.get_story(lat=HERE[0], lon=HERE[1])
    assert [(s["name"], s["distance_m"]) for s in r["stories"]][:2] == [("정방폭포", r["stories"][0]["distance_m"]), ("소정방폭포", 180)]
    assert r["stories"][0]["distance_m"] < 180  # curated point is ~140 m from HERE
    _fresh_caches()


def test_story_here_falls_back_without_key_or_on_api_error(monkeypatch):
    import httpx

    _fresh_caches()
    first = tour_service.get_story(lat=HERE[0], lon=HERE[1])["stories"][0]  # tests have no key
    assert first["name"] == "정방폭포" and first["distance_m"] < 300  # fallback still says how far
    monkeypatch.setattr(tour_service, "_api_key", lambda: "test-key")
    monkeypatch.setattr(tour_service, "_http", lambda: httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(500))))
    r = tour_service.get_story(lat=HERE[0], lon=HERE[1])
    assert "basis" not in r and r["stories"][0]["name"] == "정방폭포"
    assert tour_service.get_story("없는관광지")["status"] == "not_found"


def test_chat_here_story_needs_location_consent(client, auth, monkeypatch):
    calls = []
    monkeypatch.setattr(tour_service, "_http", _tourapi(calls))
    monkeypatch.setattr(tour_service, "_api_key", lambda: "test-key")
    _fresh_caches()
    body = {"message": "여기 이야기 해줘", "location": {"lat": HERE[0], "lon": HERE[1], "accuracy_m": 15}}
    r = client.post("/v1/agent/chat", headers=auth, json=body).json()
    assert r["tool_calls"][0]["name"] == "get_tour_story" and "소정방폭포 · 180m" in r["reply"]
    assert r["sources"] == [{"name": OFFICIAL["source"], "url": OFFICIAL["source_url"], "checked_at": None}]
    assert "출처" not in r["reply"]
    client.patch("/v1/me/preferences", json={"consent_location": False})
    calls.clear()
    r = client.post("/v1/agent/chat", headers=auth, json=body).json()
    assert not any(op == "locationBasedList2" for op, _ in calls) and "180m" not in r["reply"]
    _fresh_caches()


def test_chat_tour_questions_and_source_registered(client, auth):
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "근처 관광지 알려줘"}).json()
    assert r["tool_calls"][0]["name"] == "find_tour_spots" and "출처" not in r["reply"]  # shown under the answer instead
    assert any("관광지" in s["name"] for s in r["sources"])
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "관광지와 관련된 이야기 알려줘"}).json()
    assert r["tool_calls"][0]["name"] == "get_tour_story" and "전해" in r["reply"]
    ids = {s["id"] for s in client.get("/v1/data-sources").json()["sources"]}
    assert "curated_tour" in ids
