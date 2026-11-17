"""기상청 단기예보 (VilageFcstInfoService_2.0), from 기상청 API허브 and/or 공공데이터포털 — same
data and response format, different host and key parameter. Configured sources are tried in order
(API허브 first); without any key, or when every call fails, we say so explicitly instead of
guessing (spec §11 '예보 확인 필요')."""
import time
from datetime import datetime, timedelta

import httpx

from ..config import get_settings
from .geo import KST, latlon_to_kma_grid, now_kst

KMA_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getVilageFcst"
APIHUB_URL = "https://apihub.kma.go.kr/api/typ02/openApi/VilageFcstInfoService_2.0/getVilageFcst"
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


def _sources() -> list[dict]:
    st = get_settings()
    out = []
    if st.kma_apihub_key:
        out.append({"name": "기상청 API허브", "url": APIHUB_URL, "key_param": "authKey",
                    "key": st.kma_apihub_key, "source_url": "https://apihub.kma.go.kr/"})
    if st.kma_service_key:
        out.append({"name": "공공데이터포털", "url": KMA_URL, "key_param": "serviceKey",
                    "key": st.kma_service_key, "source_url": "https://www.data.go.kr/"})
    return out


def _fetch(src: dict, params: dict, client: httpx.Client) -> list[dict]:
    """Return forecast items, or raise RuntimeError with a short reason."""
    try:
        resp = client.get(src["url"], params={src["key_param"]: src["key"], **params})
        resp.raise_for_status()
        body = resp.json()["response"]
        if body["header"]["resultCode"] != "00":
            raise RuntimeError(f"KMA error {body['header']['resultMsg']}")
        return body["body"]["items"]["item"]
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"HTTP {exc.response.status_code}") from exc
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"request failed: {type(exc).__name__}") from exc


def get_weather(lat: float, lon: float, hours: int = 12, *, client: httpx.Client | None = None) -> dict:
    sources = _sources()
    if not sources:
        return unavailable("KMA_APIHUB_KEY / KMA_SERVICE_KEY not configured")
    nx, ny = latlon_to_kma_grid(lat, lon)
    now = now_kst()
    base_date, base_time = _latest_base(now)
    cache_key = (nx, ny, base_date, base_time, hours)
    hit = _CACHE.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_TTL_S:
        return hit[1]
    params = {
        "pageNo": 1, "numOfRows": 400, "dataType": "JSON",
        "base_date": base_date, "base_time": base_time, "nx": nx, "ny": ny,
    }
    c = client or httpx.Client(timeout=8.0)
    errors = []
    try:
        for src in sources:
            try:
                items = _fetch(src, params, c)
                break
            except RuntimeError as exc:
                errors.append(f"{src['name']}: {exc}")
        else:
            return unavailable("; ".join(errors))
    finally:
        if client is None:
            c.close()  # a client made here is not reused, so close it instead of leaking a connection pool per call

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
        "source": f"기상청 단기예보 ({src['name']})",
        "source_url": src["source_url"],
        "base_time": f"{base_date} {base_time}",
        "fetched_at": now.isoformat(),
        "grid": [nx, ny],
        "forecast": forecast,
    }
    _CACHE[cache_key] = (time.time(), result)
    return result
