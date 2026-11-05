"""Deterministic geo/time helpers. Distance, time and sun calculations are done in code,
never by the LLM (spec §07)."""
import math
from datetime import date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9), "Asia/Seoul")
JEJU_CENTER = (33.38, 126.55)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def valid_coordinate(lat: float, lon: float) -> bool:
    return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0


def now_kst() -> datetime:
    return datetime.now(KST)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; values are stored as UTC, so re-attach it."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_kst(dt: datetime) -> datetime:
    return as_utc(dt).astimezone(KST)


def parse_hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def fmt_hhmm(minutes_from_midnight: float) -> str:
    m = int(round(minutes_from_midnight))
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def hhmm_to_minutes(value: str) -> int:
    t = parse_hhmm(value)
    return t.hour * 60 + t.minute


def sunset_kst(day: date, lat: float = JEJU_CENTER[0], lon: float = JEJU_CENTER[1]) -> datetime:
    """Sunset via the standard sunrise equation (±2 min accuracy; adequate for planning)."""
    n = day.toordinal() - date(2000, 1, 1).toordinal() + 0.0008
    j_star = n - lon / 360.0
    m = math.radians((357.5291 + 0.98560028 * j_star) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    j_transit = 2451545.0 + j_star + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    sin_d = math.sin(lam) * math.sin(math.radians(23.4397))
    cos_d = math.cos(math.asin(sin_d))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * sin_d) / (math.cos(phi) * cos_d)
    w0 = math.degrees(math.acos(max(-1.0, min(1.0, cos_w))))
    j_set = j_transit + w0 / 360.0
    unix = (j_set - 2440587.5) * 86400.0
    return datetime.fromtimestamp(unix, tz=timezone.utc).astimezone(KST)


# --- KMA (기상청) Lambert Conformal Conic grid conversion (동네예보 격자) ---------------------
def latlon_to_kma_grid(lat: float, lon: float) -> tuple[int, int]:
    re, grid = 6371.00877, 5.0
    slat1, slat2, olon, olat = map(math.radians, (30.0, 60.0, 126.0, 38.0))
    xo, yo = 43, 136
    re_g = re / grid
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(
        math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    )
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5) ** sn * math.cos(slat1) / sn
    ro = re_g * sf / math.tan(math.pi * 0.25 + olat * 0.5) ** sn
    ra = re_g * sf / math.tan(math.pi * 0.25 + math.radians(lat) * 0.5) ** sn
    theta = math.radians(lon) - olon
    if theta > math.pi:
        theta -= 2 * math.pi
    if theta < -math.pi:
        theta += 2 * math.pi
    theta *= sn
    x = int(math.floor(ra * math.sin(theta) + xo + 0.5))
    y = int(math.floor(ro - ra * math.cos(theta) + yo + 0.5))
    return x, y
