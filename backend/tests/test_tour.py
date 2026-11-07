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


def test_chat_tour_questions_and_source_registered(client, auth):
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "근처 관광지 알려줘"}).json()
    assert r["tool_calls"][0]["name"] == "find_tour_spots" and "출처" in r["reply"]
    assert any("관광지" in s["name"] for s in r["sources"])
    r = client.post("/v1/agent/chat", headers=auth, json={"message": "관광지와 관련된 이야기 알려줘"}).json()
    assert r["tool_calls"][0]["name"] == "get_tour_story" and "전해" in r["reply"]
    ids = {s["id"] for s in client.get("/v1/data-sources").json()["sources"]}
    assert "curated_tour" in ids
