"""Nearby places via 한국관광공사 TourAPI (KorService2). Opening hours are NOT verified by this
API, so results are always labeled as such (spec §10 warning)."""
import httpx

from ..config import get_settings

TOUR_URL = "https://apis.data.go.kr/B551011/KorService2/locationBasedList2"
CATEGORY_TO_CONTENT_TYPE = {"food": "39", "cafe": "39", "lodging": "32", "attraction": "12", "culture": "14", "shopping": "38"}


def get_nearby_places(lat: float, lon: float, category: str = "food", radius_m: int = 2000, *, client: httpx.Client | None = None) -> dict:
    key = get_settings().tour_api_key
    base = {
        "source": "한국관광공사 TourAPI",
        "open_status": "unverified",
        "caveat": "영업 여부·시간은 실시간 확인된 정보가 아닙니다. 방문 전 전화나 지도 앱에서 확인하세요.",
    }
    if not key:
        return {**base, "status": "unavailable", "places": [], "reason": "TOUR_API_KEY not configured",
                "message": "검증된 장소 데이터 제공자가 연결되지 않아 주변 장소를 안내할 수 없습니다."}
    params = {
        "serviceKey": key, "MobileOS": "ETC", "MobileApp": "JejuTrailMate", "_type": "json",
        "mapX": lon, "mapY": lat, "radius": min(radius_m, 20000), "numOfRows": 10, "pageNo": 1,
        "arrange": "E",  # distance order
        "contentTypeId": CATEGORY_TO_CONTENT_TYPE.get(category, "39"),
    }
    try:
        c = client or httpx.Client(timeout=8.0)
        resp = c.get(TOUR_URL, params=params)
        resp.raise_for_status()
        items = resp.json()["response"]["body"]["items"]
        items = items.get("item", []) if isinstance(items, dict) else []
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
        return {**base, "status": "unavailable", "places": [], "reason": f"request failed: {type(exc).__name__}"}
    places = [
        {
            "name": it.get("title"),
            "address": it.get("addr1"),
            "distance_m": round(float(it.get("dist", 0))),
            "lat": float(it["mapy"]) if it.get("mapy") else None,
            "lon": float(it["mapx"]) if it.get("mapx") else None,
            "tel": it.get("tel") or None,
        }
        for it in items
    ]
    return {**base, "status": "ok", "places": places}
