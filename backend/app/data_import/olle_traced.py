"""공식 코스 안내지도에서 따라 그린 선(tools/olle_map_trace.py) → 실제 보행로 위 경로 (jeju_olle).

The traced sketch is ~50–150 m off (simplified maps, labels beside their points), so it is never stored
as is. Instead it steers a route over real walkable OpenStreetMap ways ("corridor matching"):

  cost(way piece) = length × (1 + (distance from the sketch / SIGMA_M)²)

and the cheapest path official start → sketch via points → official end is taken. That keeps the
course on paths that exist while following the shape the official map draws (which OSM-only routing
would not: it takes the shortest way, not the Olle way).

A course is loaded only if the result is within LENGTH_TOL of the official distance and stays close
to the sketch. Courses that already have a verified OSM route relation (olle_osm) are left alone.
The way data is OSM (ODbL) — the drawn line carries the OSM attribution and an "estimated" label."""
from __future__ import annotations

import heapq
import json
import math
import time
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RouteSegment, TrailRoute
from ..services.geo import haversine_m, path_length_m
from .common import ImportError_, register
from .olle_osm import OVERPASS, UA

SOURCE_ID = "olle_map_traced"
FILE_NAME = "traces.json"
SIGMA_M = 40.0
SKETCH_COST = 6.0  # real ways up to ≈ 90 m off the sketch are preferred over walking the sketch itself
SKETCH_STEP_M = 25.0
LINK_M = 50.0
SKETCH_SHARE_MAX = 0.45  # more than this off mapped paths → too little real geometry to trust
GRID_M = 60.0
ICP_REACH_M = 150.0
ALIGN_WINDOW_M = 1200.0
ANCHOR_W = 20.0
PAD_DEG = 0.004  # ≈ 400 m around the sketch for the way download
# Course maps smooth out zigzag forest and coastal trails, so a faithful trace comes out shorter than the
# official distance (up to ~27 % on 13코스, checked on the map); longer than official means a detour.
LENGTH_MIN_RATIO, LENGTH_MAX_RATIO = 0.70, 1.15
# Old course numbers still referenced by saved trips: they follow the course that replaced them.
COURSE_ALIASES = {"15": "15-A"}
MEAN_OFF_MAX_M = 110.0  # the sketch itself is 50–150 m off in places (simplified maps)
WAY_QUERY = ('[out:json][timeout:120];way["highway"]["highway"!~"^(motorway|motorway_link|trunk_link|construction|proposed|'
             'abandoned|bus_guideway|raceway)$"]["access"!~"^(private|no)$"]["foot"!="no"]({s},{w},{n},{e});out geom;')


class _Sketch:
    """Nearest-distance queries against the traced polyline (grid-bucketed segments, local metres)."""

    def __init__(self, pts: list[list[float]]):
        self.lat0, self.lon0 = pts[0]
        self.kx = 111320 * math.cos(math.radians(self.lat0))
        self.xy = [self.to_xy(p) for p in pts]
        self.grid: dict[tuple[int, int], set[int]] = {}
        for i in range(len(self.xy) - 1):
            (x1, y1), (x2, y2) = self.xy[i], self.xy[i + 1]
            n = max(1, int(math.hypot(x2 - x1, y2 - y1) // (GRID_M / 2)))
            for k in range(n + 1):
                t = k / n
                self.grid.setdefault((int((x1 + (x2 - x1) * t) // GRID_M), int((y1 + (y2 - y1) * t) // GRID_M)), set()).add(i)

    def to_xy(self, p) -> tuple[float, float]:
        return (p[1] - self.lon0) * self.kx, (p[0] - self.lat0) * 110540

    def dist(self, p) -> float:
        x, y = self.to_xy(p)
        cx, cy = int(x // GRID_M), int(y // GRID_M)
        best = math.inf
        for r in range(0, 12):
            for i in range(cx - r, cx + r + 1):
                for j in range(cy - r, cy + r + 1):
                    if max(abs(i - cx), abs(j - cy)) != r:
                        continue
                    for s in self.grid.get((i, j), ()):
                        best = min(best, _seg_dist(x, y, self.xy[s], self.xy[s + 1]))
            if best <= r * GRID_M:
                break
        return best


def _seg_dist(px, py, a, b) -> float:
    (x1, y1), (x2, y2) = a, b
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L))
    return math.hypot(px - x1 - t * dx, py - y1 - t * dy)


def bbox(pts: list[list[float]]) -> tuple[float, float, float, float]:
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    return min(lats) - PAD_DEG, min(lons) - PAD_DEG, max(lats) + PAD_DEG, max(lons) + PAD_DEG


def fetch_ways(pts: list[list[float]], *, client: httpx.Client, waits: tuple[float, ...] = (0, 30, 90)) -> dict:
    """Public Overpass servers answer 429/504 when queried back to back, so each round tries every mirror
    and the next round waits longer."""
    s, w, n, e = bbox(pts)
    q = WAY_QUERY.format(s=round(s, 5), w=round(w, 5), n=round(n, 5), e=round(e, 5))
    errors = []
    for wait in waits:
        time.sleep(wait)
        for url in OVERPASS:
            try:
                return client.post(url, data={"data": q}).raise_for_status().json()
            except (httpx.HTTPError, ValueError) as ex:
                code = getattr(getattr(ex, "response", None), "status_code", "")
                errors.append(f"{url.split('/')[2]} {type(ex).__name__} {code}".strip())
    raise ImportError_("Overpass 서버가 응답하지 않습니다 (" + "; ".join(errors[-3:]) + ")")


def build_graph(ways: list[dict], sketch: _Sketch):
    nodes: dict[tuple[float, float], int] = {}
    coords: list[list[float]] = []
    adj: list[list[tuple[int, float]]] = []

    def nid(lat: float, lon: float) -> int:
        k = (round(lat, 7), round(lon, 7))
        if k not in nodes:
            nodes[k] = len(coords)
            coords.append([lat, lon])
            adj.append([])
        return nodes[k]

    dcache: dict[int, float] = {}

    def d(i: int) -> float:
        if i not in dcache:
            dcache[i] = sketch.dist(coords[i])
        return dcache[i]

    for w in ways:
        ids = [nid(g["lat"], g["lon"]) for g in (w.get("geometry") or []) if g]
        for a, b in zip(ids, ids[1:]):
            pa, pb = coords[a], coords[b]
            L = haversine_m(pa[0], pa[1], pb[0], pb[1])
            mid = sketch.dist([(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2])
            off = max(d(a), d(b), mid)
            c = L * (1 + (off / SIGMA_M) ** 2)
            adj[a].append((b, c))
            adj[b].append((a, c))
    n_real = len(coords)
    _add_sketch_edges(coords, adj, sketch, n_real)
    return coords, adj, n_real


def _add_sketch_edges(coords, adj, sketch: _Sketch, n_real: int) -> None:
    """Fallback for trail pieces OSM does not have (coastal rocks, 곶자왈 paths): the sketch line itself, at
    SKETCH_COST × its length, joined to real ways within LINK_M. A real way up to ~SIGMA·√(SKETCH_COST−1) off
    the sketch stays cheaper, so the sketch is only walked where no real path runs alongside it."""
    pts = [sketch.xy[0]]
    for p in sketch.xy[1:]:
        last = pts[-1]
        n = max(1, int(math.hypot(p[0] - last[0], p[1] - last[1]) // SKETCH_STEP_M))
        pts += [(last[0] + (p[0] - last[0]) * k / n, last[1] + (p[1] - last[1]) * k / n) for k in range(1, n + 1)]
    cell: dict[tuple[int, int], list[int]] = {}
    for i in range(n_real):
        if adj[i]:
            x, y = sketch.to_xy(coords[i])
            cell.setdefault((int(x // LINK_M), int(y // LINK_M)), []).append(i)
    prev = None
    for x, y in pts:
        i = len(coords)
        coords.append([sketch.lat0 + y / 110540, sketch.lon0 + x / sketch.kx])
        adj.append([])
        if prev is not None:
            L = haversine_m(*coords[prev], *coords[i])
            adj[prev].append((i, L * SKETCH_COST))
            adj[i].append((prev, L * SKETCH_COST))
        cx, cy = int(x // LINK_M), int(y // LINK_M)
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in cell.get((gx, gy), ()):
                    L = haversine_m(*coords[i], *coords[j])
                    if L <= LINK_M:
                        adj[i].append((j, L * SKETCH_COST))
                        adj[j].append((i, L * SKETCH_COST))
        prev = i


def align_sketch(sketch_pts: list[list[float]], ways: list[dict], anchors: list[list[float]]) -> list[list[float]]:
    """Pull the sketch onto the real path network before matching (iterative closest point).

    The sketch comes from a simplified map placed with a few control points, so it is shifted, scaled and
    locally stretched by 50–150 m. Its shape, though, is the shape of real streets and trails. So:
      1. global: similarity transform (scale, rotation, shift) fitted to nearest mapped points within
         ICP_REACH_M, the official start/end anchors held in place with a heavy weight;
      2. local: each stretch gets the mean offset to nearby mapped points (window ALIGN_WINDOW_M along the
         line), smoothed so neighbouring stretches move together; anchors fade the shift out at the ends."""
    ref = _Sketch(sketch_pts)
    xy = [ref.to_xy(p) for p in sketch_pts]
    # mapped points, densified to ~15 m, bucketed
    cell: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for w in ways:
        g = [ref.to_xy([p["lat"], p["lon"]]) for p in (w.get("geometry") or []) if p]
        for (x1, y1), (x2, y2) in zip(g, g[1:]):
            n = max(1, int(math.hypot(x2 - x1, y2 - y1) // 15))
            for k in range(n + 1):
                x, y = x1 + (x2 - x1) * k / n, y1 + (y2 - y1) * k / n
                cell.setdefault((int(x // ICP_REACH_M), int(y // ICP_REACH_M)), []).append((x, y))
    if not cell:
        return sketch_pts

    def nearest(x, y):
        cx, cy = int(x // ICP_REACH_M), int(y // ICP_REACH_M)
        best, bd = None, ICP_REACH_M
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for q in cell.get((gx, gy), ()):
                    d = math.hypot(q[0] - x, q[1] - y)
                    if d < bd:
                        best, bd = q, d
        return best

    anc = [ref.to_xy(a) for a in anchors]
    for _ in range(8):  # global similarity
        pairs = [(p, q, 1.0) for p in xy[::2] if (q := nearest(*p))]
        idx = [min(range(len(xy)), key=lambda i: math.hypot(xy[i][0] - a[0], xy[i][1] - a[1])) for a in anc]
        pairs += [(xy[i], a, ANCHOR_W * max(1, len(pairs) // 50)) for i, a in zip(idx, anc)]
        if len(pairs) < 10:
            break
        xy = _apply_similarity(xy, _fit_similarity(pairs))
    # local smoothing shifts
    along = [0.0]
    for (x1, y1), (x2, y2) in zip(xy, xy[1:]):
        along.append(along[-1] + math.hypot(x2 - x1, y2 - y1))
    for _ in range(4):
        offs = [((q[0] - p[0], q[1] - p[1]) if (q := nearest(*p)) else None) for p in xy]
        shifted = []
        lo = 0
        for i, p in enumerate(xy):
            while along[i] - along[lo] > ALIGN_WINDOW_M / 2:
                lo += 1
            hi = i
            while hi + 1 < len(xy) and along[hi + 1] - along[i] <= ALIGN_WINDOW_M / 2:
                hi += 1
            win = [o for o in offs[lo:hi + 1] if o]
            dx = sum(o[0] for o in win) / len(win) if len(win) >= 5 else 0.0
            dy = sum(o[1] for o in win) / len(win) if len(win) >= 5 else 0.0
            fade = min(1.0, along[i] / ALIGN_WINDOW_M, (along[-1] - along[i]) / ALIGN_WINDOW_M)  # ends stay on anchors
            shifted.append((p[0] + dx * fade, p[1] + dy * fade))
        xy = shifted
    return [[ref.lat0 + y / 110540, ref.lon0 + x / ref.kx] for x, y in xy]


def _fit_similarity(pairs):
    """Weighted LSQ for q ≈ a·p + b in the complex plane (a = scale·rotation)."""
    W = sum(w for _, _, w in pairs)
    px = sum(w * p[0] for p, _, w in pairs) / W
    py = sum(w * p[1] for p, _, w in pairs) / W
    qx = sum(w * q[0] for _, q, w in pairs) / W
    qy = sum(w * q[1] for _, q, w in pairs) / W
    sxx = sum(w * ((p[0] - px) ** 2 + (p[1] - py) ** 2) for p, _, w in pairs) or 1.0
    re = sum(w * ((p[0] - px) * (q[0] - qx) + (p[1] - py) * (q[1] - qy)) for p, q, w in pairs) / sxx
    im = sum(w * ((p[0] - px) * (q[1] - qy) - (p[1] - py) * (q[0] - qx)) for p, q, w in pairs) / sxx
    scale = math.hypot(re, im)
    if not 0.8 <= scale <= 1.25:  # never let a bad match rescale the course wildly
        re, im = re / scale, im / scale
    return re, im, px, py, qx, qy


def _apply_similarity(xy, t):
    re, im, px, py, qx, qy = t
    return [(qx + re * (x - px) - im * (y - py), qy + im * (x - px) + re * (y - py)) for x, y in xy]


def _nearest(coords: list[list[float]], adj, p: list[float]) -> int:
    return min((i for i in range(len(coords)) if adj[i]), key=lambda i: haversine_m(coords[i][0], coords[i][1], p[0], p[1]))


def _dijkstra(adj, s: int, t: int) -> list[int]:
    dist, prev, pq = {s: 0.0}, {}, [(0.0, s)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == t:
            break
        if d > dist[u]:
            continue
        for v, c in adj[u]:
            nd = d + c
            if nd < dist.get(v, math.inf):
                dist[v], prev[v] = nd, u
                heapq.heappush(pq, (nd, v))
    if t != s and t not in prev:
        raise ImportError_("보행로가 끊겨 있어 경로를 잇지 못했습니다")
    out = [t]
    while out[-1] != s:
        out.append(prev[out[-1]])
    return out[::-1]


def corridor_route(ways: list[dict], sketch_pts: list[list[float]], stops: list[list[float]],
                   stats: dict | None = None) -> list[list[float]]:
    sketch = _Sketch(sketch_pts)
    coords, adj, n_real = build_graph(ways, sketch)
    if not n_real:
        raise ImportError_("보행로 데이터가 없습니다")
    ids = [_nearest(coords[:n_real], adj, p) for p in stops]
    seq: list[int] = []
    for a, b in zip(ids, ids[1:]):
        part = _dijkstra(adj, a, b)
        seq.extend(part if not seq else part[1:])
    if stats is not None:  # how much of the result had to follow the sketch instead of a mapped path
        on = sum(haversine_m(*coords[u], *coords[v]) for u, v in zip(seq, seq[1:]) if u >= n_real or v >= n_real)
        total = sum(haversine_m(*coords[u], *coords[v]) for u, v in zip(seq, seq[1:])) or 1.0
        stats["sketch_share"] = round(on / total, 3)
    return [coords[i] for i in seq]


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False,
        client: httpx.Client | None = None, only: set[str] | None = None) -> dict:
    f = folder / FILE_NAME
    if not f.exists():
        raise ImportError_(f"{f} 가 없습니다 — tools/olle_map_trace.py 를 먼저 실행하세요")
    data = json.loads(f.read_text(encoding="utf-8"))
    routes = {r.course_no: r for r in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "jeju_olle"))}
    report = {"file": f.name, "loaded": {}, "rejected": {}, "skipped": {}}
    c = client or httpx.Client(timeout=180, headers=UA)
    try:
        for no, t in data.get("courses", {}).items():
            if only and no not in only:
                continue
            route = routes.get(no)
            if "trace" not in t:
                report["skipped"][no] = t.get("error", "trace 없음")
                continue
            if route is None:
                report["skipped"][no] = "공식 코스표에 없음"
                continue
            if t.get("calibration"):
                report["skipped"][no] = "검증용 코스(OSM 경로 사용)"
                continue
            if route.segments and route.segments[0].source_id == "olle_osm":
                report["skipped"][no] = "OSM 검증 경로가 이미 있음"
                continue
            if route.start_lat is None or route.end_lat is None:
                report["skipped"][no] = "공식 시작·종점 좌표 없음 — olle-endpoints 먼저"
                continue
            cache = folder / f"ways_{no}.json"
            if cache.exists():
                ways = json.loads(cache.read_text(encoding="utf-8"))
            else:
                try:
                    ways = fetch_ways(t["trace"], client=c)
                except ImportError_ as e:
                    report["skipped"][no] = str(e)  # try again later; the other courses still load
                    continue
                cache.write_text(json.dumps(ways), encoding="utf-8")
                time.sleep(2)
            info: dict = {"sketch_km": round(path_length_m(t["trace"]) / 1000, 2), "official_km": route.distance_km,
                          "controls_used": t.get("controls_used")}
            stats: dict = {}
            way_list = [w for w in ways.get("elements", []) if w.get("type") == "way"]
            start, end = [route.start_lat, route.start_lon], [route.end_lat, route.end_lon]
            aligned = align_sketch(t["trace"], way_list, [start, end])
            info["align_shift_m"] = round(sum(haversine_m(*a, *b) for a, b in zip(t["trace"], aligned)) / len(aligned))
            # via points ride along with the sketch point they sit on, unless pinned to a named place
            pinned = t.get("via_pinned") or [False] * len(t.get("via", []))
            via = [v if pin else aligned[min(range(len(t["trace"])), key=lambda i: haversine_m(*t["trace"][i], *v))]
                   for v, pin in zip(t.get("via", []), pinned)]
            try:
                path = corridor_route(way_list, aligned, [start, *via, end], stats)
                info.update(stats)
            except ImportError_ as e:
                report["rejected"][no] = {**info, "problems": [str(e)]}
                continue
            sketch = _Sketch(aligned)
            offs = [sketch.dist(p) for p in path]
            length = path_length_m(path)
            info.update({"length_km": round(length / 1000, 2), "mean_off_sketch_m": round(sum(offs) / len(offs)),
                         "max_off_sketch_m": round(max(offs)),
                         "start_off_m": round(haversine_m(*path[0], route.start_lat, route.start_lon)),
                         "end_off_m": round(haversine_m(*path[-1], route.end_lat, route.end_lon))})
            problems = []
            if route.distance_km and not LENGTH_MIN_RATIO <= length / 1000 / route.distance_km <= LENGTH_MAX_RATIO:
                problems.append(f"길이 {length / 1000:.2f}km ≠ 공식 {route.distance_km}km")
            if info["mean_off_sketch_m"] > MEAN_OFF_MAX_M:
                problems.append(f"안내지도 선과 평균 {info['mean_off_sketch_m']}m 떨어져 있습니다")
            if info.get("sketch_share", 0) > SKETCH_SHARE_MAX:
                problems.append(f"경로의 {info['sketch_share']:.0%}가 지도에 없는 길(안내지도 선)입니다")
            if max(info["start_off_m"], info["end_off_m"]) > 300:
                problems.append("시작·종점 근처에 보행로가 없습니다")
            if problems:
                report["rejected"][no] = {**info, "problems": problems}
                continue
            report["loaded"][no] = info
            if dry_run:
                continue
            route.path = path
            route.has_geometry = True
            route.segments = [RouteSegment(seq=0, coords=path, length_m=length, source_id=SOURCE_ID)]
            route.version = (route.version or 1) + 1
    finally:
        if client is None:
            c.close()
    for old, new in COURSE_ALIASES.items():
        src, dst = routes.get(new), routes.get(old)
        if not (src and dst and new in report["loaded"]) or (only and new not in only):
            continue
        report["loaded"][old] = {"same_as": new}
        if not dry_run:
            dst.path, dst.has_geometry = src.path, True
            dst.start_lat, dst.start_lon, dst.end_lat, dst.end_lon = src.start_lat, src.start_lon, src.end_lat, src.end_lon
            dst.segments = [RouteSegment(seq=0, coords=src.path, length_m=path_length_m(src.path), source_id=SOURCE_ID)]
            dst.version = (dst.version or 1) + 1
    report["loaded_count"], report["rejected_count"] = len(report["loaded"]), len(report["rejected"])
    if not dry_run and report["loaded"]:
        register(db, SOURCE_ID, [f], license=license, ref_date=(data.get("generated_at") or "")[:10] or None)
        db.commit()
    return report
