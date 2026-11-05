"""기상청 단기예보 (공공데이터포털 VilageFcstInfoService_2.0). Without a key, or when the call
fails, we say so explicitly instead of guessing (spec §11 '예보 확인 필요')."""
import time
from datetime import datetime, timedelta

import httpx

from ..config import get_settings
from .geo import KST, latlon_to_kma_grid, now_kst

KMA_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
BASE_HOURS = [2, 5, 8, 11, 14, 17, 20, 23]
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL_S = 20 * 60
SKY = {"1": "맑음", "3": "구름많음", "4": "흐림"}
PTY = {"0": "없음", "1": "비", "2": "비/눈", "3": "눈", "4": "소나기"}


def _latest_base(now: datetime) -> tuple[str, str]:
    # KMA publishes ~10 minutes after each base hour.
    t = now - timedelta(minutes=15)
    hours = [h for h in BASE_HOURS if h <= t.hour]
    if not hours:
        t = t - timedelta(days=1)
        return t.strftime("%Y%m%d"), "2300"
    return t.strftime("%Y%m%d"), f"{hours[-1]:02d}00"


def unavailable(reason: str) -> dict:
    return {
        "status": "unavailable",
        "reason": reason,
        "message": "실시간 예보를 확인하지 못했습니다. 기상청 날씨누리에서 예보를 직접 확인하세요.",
        "source": "기상청 단기예보",
        "source_url": "https://www.weather.go.kr/",
    }


def get_weather(lat: float, lon: float, hours: int = 12, *, client: httpx.Client | None = None) -> dict:
    key = get_settings().kma_service_key
    if not key:
        return unavailable("KMA_SERVICE_KEY not configured")
    nx, ny = latlon_to_kma_grid(lat, lon)
    now = now_kst()
    base_date, base_time = _latest_base(now)
    cache_key = (nx, ny, base_date, base_time, hours)
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    params = {
        "serviceKey": key, "pageNo": 1, "numOfRows": 400, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny,
    }
    try:
        c = client or httpx.Client(timeout=8.0)
        resp = c.get(KMA_URL, params=params)
        resp.raise_for_status()
        body = resp.json()["response"]
        if body["header"]["resultCode"] != "00":
            return unavailable(f"KMA error {body['header']['resultMsg']}")
        items = body["body"]["items"]["item"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        return unavailable(f"request failed: {type(exc).__name__}")

    by_slot: dict[str, dict] = {}
    for it in items:
        slot = f"{it['fcstDate']}{it['fcstTime']}"
        by_slot.setdefault(slot, {})[it["category"]] = it["fcstValue"]
    horizon = (now + timedelta(hours=hours)).strftime("%Y%m%d%H%M")
    forecast = []
    for slot in sorted(by_slot):
        if slot < now.strftime("%Y%m%d%H00") or slot > horizon:
            continue
        v = by_slot[slot]
        forecast.append({
            "time": datetime.strptime(slot, "%Y%m%d%H%M").replace(tzinfo=KST).isoformat(),
            "temp_c": v.get("TMP"),
            "sky": SKY.get(v.get("SKY", ""), v.get("SKY")),
            "precip_type": PTY.get(v.get("PTY", ""), v.get("PTY")),
            "precip_prob_pct": v.get("POP"),
            "wind_ms": v.get("WSD"),
        })
    result = {
        "status": "ok",
        "source": "기상청 단기예보 (공공데이터포털)",
        "source_url": "https://www.data.go.kr/",
        "base_time": f"{base_date} {base_time}",
        "fetched_at": now.isoformat(),
        "grid": [nx, ny],
        "forecast": forecast,
    }
    _CACHE[cache_key] = (time.time(), result)
    return result
