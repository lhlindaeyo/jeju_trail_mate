"""Trace 제주올레 course lines from the official course map images → rough WGS84 polylines.

Needs Pillow + NumPy, which the app itself does not use, so run it with any Python that has them:

    python tools/olle_map_trace.py                  # from backend/, after `python -m app.data_import olle-fetch`

For every course in app/data/olle_map_trace.json:
  1. mask the pixels drawn in the course colours (blue line, orange wheelchair parts, green B-route),
  2. find the cheapest pixel path start → via… → end (off-line pixels cost 40×, so icons and labels lying
     on the line are bridged but the line is followed wherever it is drawn),
  3. georeference: an affine pixel → metre transform fitted on the official start/end coordinates
     (data/raw/olle_official/road_endpoints.json) plus map labels found in OpenStreetMap Nominatim;
     labels that disagree by > OUTLIER_M are dropped (wrong place with the same name),
  4. write data/raw/olle_traced/traces.json and an overlay PNG per course for review.

The result is only a sketch (the maps are simplified and labels sit next to points); the importer
`python -m app.data_import olle-traced` snaps it onto real walkable OSM ways and checks it."""
from __future__ import annotations

import heapq
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
RAW = BACKEND.parent / "data" / "raw"
CONFIG = BACKEND / "app" / "data" / "olle_map_trace.json"
OUT = RAW / "olle_traced"
UA = {"User-Agent": "trail-data-import/0.1"}
COLOR_TOL = 70
OFF_LINE_COST, NEAR_LINE_COST = 40.0, 3.0
OUTLIER_M = 350.0
KY = 110540.0


def mask_for(im: np.ndarray, colors: list[list[int]]) -> np.ndarray:
    m = np.zeros(im.shape[:2], bool)
    for c in colors:
        m |= np.sqrt(((im - np.array(c)) ** 2).sum(-1)) < COLOR_TOL
    return m


def cost_grid(mask: np.ndarray) -> np.ndarray:
    near = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            near |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return np.where(mask, 1.0, np.where(near, NEAR_LINE_COST, OFF_LINE_COST))


def pixel_path(cost: np.ndarray, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
    H, W = cost.shape
    dist, prev, pq = {a: 0.0}, {}, [(0.0, a)]
    while pq:
        d, u = heapq.heappop(pq)
        if u == b:
            break
        if d > dist[u]:
            continue
        x, y = u
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if not (dx or dy):
                    continue
                v = (x + dx, y + dy)
                if not (0 <= v[0] < W and 0 <= v[1] < H):
                    continue
                nd = d + cost[v[1], v[0]] * (1.4142 if dx and dy else 1.0)
                if nd < dist.get(v, 1e18):
                    dist[v], prev[v] = nd, u
                    heapq.heappush(pq, (nd, v))
    out = [b]
    while out[-1] != a:
        out.append(prev[out[-1]])
    return out[::-1]


def nominatim(names: list[str], bbox: tuple[float, float, float, float], cache: dict) -> list[float] | None:
    s, w, n, e = bbox
    for q in names:
        key = f"{q}|{s:.3f},{w:.3f},{n:.3f},{e:.3f}"
        if key not in cache:
            url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
                {"q": q, "format": "jsonv2", "viewbox": f"{w},{n},{e},{s}", "bounded": 1, "limit": 1})
            try:
                res = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
            except Exception as ex:  # network hiccup: just skip this label
                print(f"  nominatim {q}: {type(ex).__name__}")
                res = None
            time.sleep(1.1)  # Nominatim usage policy: max 1 request/second
            if res is None:
                continue
            cache[key] = [float(res[0]["lat"]), float(res[0]["lon"])] if res else None
        if cache[key]:
            return cache[key]
    return None


ANCHOR_WEIGHT = 1.0  # official start/end coordinates are exact; map labels sit beside their points
AFFINE_MIN_SPREAD = 0.05  # minor/major axis ratio of the control points needed to trust an affine fit


def fit(ctrl: list[tuple[list[float], list[float]]], origin: list[float], anchors: int):
    """Weighted least squares from pixel (x, y) to local metres (east, north). The maps are simplified, so an
    affine fit (which absorbs uneven stretching) is used when the control points spread in 2-D; when they lie
    almost on one line — most coastal courses — an affine goes skew, and a similarity (scale + rotation) is used."""
    kx = 111320 * math.cos(math.radians(origin[0]))
    P = np.array([[x, y, 1.0] for (x, y), _ in ctrl])
    Wm = np.array([[(ll[1] - origin[1]) * kx, (ll[0] - origin[0]) * KY] for _, ll in ctrl])
    wt = np.array([ANCHOR_WEIGHT if i < anchors else 1.0 for i in range(len(ctrl))])
    centered = P[:, :2] - P[:, :2].mean(0)
    sv = np.linalg.svd(centered, compute_uv=False) if len(ctrl) >= 3 else np.array([1.0, 0.0])
    if len(ctrl) >= 4 and sv[1] / max(sv[0], 1e-9) >= AFFINE_MIN_SPREAD:
        A, *_ = np.linalg.lstsq(P * wt[:, None], Wm * wt[:, None], rcond=None)
    else:
        # w = a·z + b with z = x − i·y (image y points down); unknowns a = a1 + i·a2, b = b1 + i·b2
        rows, rhs = [], []
        for (x, y, _), (e, n), k in zip(P, Wm, wt):
            rows += [[k * x, k * y, k, 0], [-k * y, k * x, 0, k]]  # east = a1·x + a2·y + b1 ; north = a2·x − a1·y + b2
            rhs += [k * e, k * n]
        a1, a2, b1, b2 = np.linalg.lstsq(np.array(rows, float), np.array(rhs), rcond=None)[0]
        A = np.array([[a1, a2], [a2, -a1], [b1, b2]])  # [x, y, 1] @ A = [east, north]
    res = np.hypot(*(P @ A - Wm).T)
    to_ll = lambda pts: [[origin[0] + n / KY, origin[1] + e / kx] for e, n in np.array([[x, y, 1.0] for x, y in pts]) @ A]
    return A, res, to_ll


def trace_course(no: str, cfg: dict, palette: dict, endpoints: dict, cache: dict) -> dict:
    img = RAW / "olle_map_images" / f"map_{cfg['image']}.jpg"
    if not img.exists():
        return {"error": f"{img.name} 없음 — olle-fetch 먼저"}
    ep = endpoints.get(no)
    if not ep:
        return {"error": "공식 시작·종점 좌표 없음"}
    im = np.asarray(Image.open(img).convert("RGB")).astype(int)
    cost = cost_grid(mask_for(im, [palette[c] for c in cfg["colors"]]))
    stops = [tuple(cfg["start_px"])] + [tuple(v) for v in cfg.get("via_px", [])] + [tuple(cfg["end_px"])]
    px: list[tuple[int, int]] = []
    for a, b in zip(stops, stops[1:]):
        seg = pixel_path(cost, a, b)
        px.extend(seg if not px else seg[1:])
    off_line = sum(1 for x, y in px if cost[y, x] >= OFF_LINE_COST)

    start, end = ep["start"], ep["end"]
    pad = 0.06
    bbox = (min(start[0], end[0]) - pad, min(start[1], end[1]) - pad, max(start[0], end[0]) + pad, max(start[1], end[1]) + pad)
    ctrl = [(cfg["start_px"], start)]
    if math.dist(start, end) > 1e-4:
        ctrl.append((cfg["end_px"], end))
    labels = []
    for c in cfg.get("controls", []):
        ll = nominatim(c["name"], bbox, cache)
        labels.append({"name": c["name"][0], "px": c["px"], "latlon": ll})
        if ll:
            ctrl.append((c["px"], ll))
    fixed = 2 if math.dist(start, end) > 1e-4 else 1
    dropped = []
    while True:
        A, res, to_ll = fit(ctrl, start, fixed)
        worst = int(np.argmax(res[fixed:])) + fixed if len(ctrl) > fixed else None
        if worst is None or len(ctrl) <= 2 or res[worst] <= OUTLIER_M:
            break
        dropped.append(labels[[l["px"] for l in labels].index(ctrl[worst][0])]["name"])
        ctrl.pop(worst)
    if len(ctrl) < 2:
        return {"error": "대응점이 2개 미만 (시작=종점인 코스는 지명 대응점이 필요)"}
    trace = to_ll(px[::2] + [px[-1]])
    # A via point that is also a kept map label goes to the label's real coordinate; others are transformed.
    kept = {tuple(p): ll for p, ll in ctrl}
    via = [kept.get(tuple(v)) or to_ll([v])[0] for v in cfg.get("via_px", [])]
    ov = np.asarray(Image.open(img).convert("RGB")).copy()
    for x, y in px:
        ov[max(0, y - 1):y + 2, max(0, x - 1):x + 2] = [230, 20, 20]
    (OUT / "overlays").mkdir(parents=True, exist_ok=True)
    Image.fromarray(ov).save(OUT / "overlays" / f"trace_{cfg['image']}_{no}.png")
    return {"trace": [[round(a, 6), round(b, 6)] for a, b in trace], "via": [[round(a, 6), round(b, 6)] for a, b in via],
            "image": img.name, "pixels": len(px), "off_line_pixels": off_line,
            "metres_per_px": round(float(np.hypot(*A[0])), 2), "controls_used": len(ctrl),
            "control_residuals_m": [round(float(r)) for r in res], "dropped_labels": dropped,
            "labels_found": sum(1 for l in labels if l["latlon"]), "labels": len(labels),
            "calibration": bool(cfg.get("calibration"))}


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    conf = json.loads(CONFIG.read_text(encoding="utf-8"))
    endpoints = {k: v for k, v in json.loads((RAW / "olle_official" / "road_endpoints.json").read_text(encoding="utf-8"))["courses"].items()}
    OUT.mkdir(parents=True, exist_ok=True)
    cache_f = OUT / "nominatim_cache.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    only = set(argv)
    out_f = OUT / "traces.json"
    result = json.loads(out_f.read_text(encoding="utf-8")) if out_f.exists() and only else {"courses": {}}
    for no, cfg in conf["courses"].items():
        if only and no not in only:
            continue
        r = trace_course(no, cfg, conf["colors"], endpoints, cache)
        result["courses"][no] = r
        cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        print(no, {k: v for k, v in r.items() if k not in ("trace", "via")})
    result["config"] = str(CONFIG.name)
    result["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out_f.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
