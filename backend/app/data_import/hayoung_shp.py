"""서귀포시 하영올레 SHP → trail_routes.path / route_segments (v1.2 §04 A2, §05).

Actual data (제주데이터허브, 2023-10): one file set per course (hayoung-olle-course-N.*), each
holding ONE multi-part POLYLINE with attributes id, link_type, route_name ("하영올레 1코스"),
start_name, end_name, total_dist ("8.4km"), total_di_1 (8400), address. There are no waypoint
coordinates in the SHP.

The parts are not stored in walking order: some are duplicates (two digitisations of the same
street), some are out-and-back spurs (e.g. 새섬). We rebuild a single walkable loop:
  1. snap part endpoints into graph nodes,
  2. drop near-duplicate parallel parts,
  3. duplicate the shortest connections between odd-degree nodes (route inspection),
  4. walk an Euler circuit from the start node, preferring each part's digitised direction.
The result is checked against the published total distance."""
from __future__ import annotations

import heapq
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RouteSegment, TrailRoute
from ..services.geo import haversine_m, path_length_m
from .common import ImportError_, register

SOURCE_ID = "hayoung_shp"
REQUIRED = [".shp", ".dbf", ".prj"]  # geometry, attributes, coordinate system
RECOMMENDED = [".shx", ".cpg", ".qmd"]  # index (rebuilt by sequential read), encoding, QGIS metadata
SNAP_M = 8.0  # endpoints closer than this are the same node
DUPLICATE_RATIO = 1.6
ROTATE_MAX_M = 200.0  # a closed loop is re-started at the vertex nearest City Hall if it passes this close  # parallel parts whose lengths differ less than this are the same street
ENC_ALIASES = {"UTF-8": "utf-8", "UTF8": "utf-8", "65001": "utf-8", "949": "cp949", "EUC-KR": "cp949", "CP949": "cp949"}


# --- file handling -------------------------------------------------------------------
def _file_sets(folder: Path) -> tuple[list[Path], list[Path], list[str]]:
    shps = sorted(folder.glob("*.shp"))
    if not shps:
        raise ImportError_(f"{folder}에 .shp 파일이 없습니다")
    files, warnings = [], []
    for shp in shps:
        stem = shp.with_suffix("")
        missing = [ext for ext in REQUIRED if not stem.with_suffix(ext).exists()]
        if missing:
            raise ImportError_(f"{shp.name}: 구성 파일이 빠졌습니다 {', '.join(missing)} — 6개 파일을 모두 받아 주세요")
        warnings += [f"{shp.name}: {ext} 없음" for ext in RECOMMENDED if not stem.with_suffix(ext).exists()]
        files += [p for p in folder.iterdir() if p.with_suffix("") == stem]
    return shps, files, warnings


def _open(shp: Path):
    import shapefile  # pyshp
    from pyproj import CRS, Transformer

    cpg = shp.with_suffix(".cpg")
    raw = cpg.read_text(errors="ignore").strip() if cpg.exists() else "cp949"
    enc = ENC_ALIASES.get(raw.upper(), raw or "cp949")
    crs = CRS.from_wkt(shp.with_suffix(".prj").read_text(errors="ignore"))
    tf = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    # Open .shp/.dbf explicitly so a missing .shx index is tolerated (records are read sequentially).
    handles = (open(shp, "rb"), open(shp.with_suffix(".dbf"), "rb"))
    r = shapefile.Reader(shp=handles[0], dbf=handles[1], encoding=enc, encodingErrors="replace")
    r._jtm_handles = handles  # pyshp does not close caller-provided files
    return r, enc, crs, tf


def _close(r) -> None:
    r.close()
    for h in getattr(r, "_jtm_handles", ()):
        h.close()


def inspect(folder: Path) -> dict:
    shps, files, warnings = _file_sets(folder)
    out = {"files": sorted(p.name for p in files), "warnings": warnings, "layers": []}
    for shp in shps:
        r, enc, crs, _tf = _open(shp)
        fields = [f[0] for f in r.fields[1:]]
        layer = {"file": shp.name, "encoding": enc, "crs": crs.name, "epsg": crs.to_epsg(),
                 "shape_type": r.shapeTypeName, "count": len(r), "fields": fields, "features": []}
        for sr in r.iterShapeRecords():
            layer["features"].append({"record": dict(zip(fields, sr.record)), "parts": len(sr.shape.parts),
                                      "points": len(sr.shape.points)})
        _close(r)
        out["layers"].append(layer)
    return out


# --- geometry ---------------------------------------------------------------------------
def _parts(shape, tf) -> list[list[list[float]]]:
    idx = list(shape.parts) + [len(shape.points)]
    out = []
    for a, b in zip(idx, idx[1:]):
        pts = [[lat, lon] for lon, lat in (tf.transform(x, y) for x, y in shape.points[a:b])]
        if len(pts) >= 2:
            out.append(pts)
    return out


class _Graph:
    def __init__(self):
        self.nodes: list[list[float]] = []
        self.edges: list[dict] = []  # {a, b, pts, length, part}

    def node(self, p: list[float]) -> int:
        for i, n in enumerate(self.nodes):
            if haversine_m(n[0], n[1], p[0], p[1]) <= SNAP_M:
                return i
        self.nodes.append(p)
        return len(self.nodes) - 1

    def add(self, pts: list[list[float]], part: int) -> None:
        a, b = self.node(pts[0]), self.node(pts[-1])
        self.edges.append({"a": a, "b": b, "pts": pts, "length": path_length_m(pts), "part": part})

    def dedupe(self) -> list[int]:
        """Drop parallel parts of similar length (two digitisations of one street). Returns dropped part ids."""
        keep, dropped = [], []
        for e in sorted(self.edges, key=lambda e: e["part"]):
            twin = next((k for k in keep if {k["a"], k["b"]} == {e["a"], e["b"]} and k["a"] != k["b"]
                         and max(k["length"], e["length"]) / max(min(k["length"], e["length"]), 1) < DUPLICATE_RATIO), None)
            if twin:
                dropped.append(e["part"])
            else:
                keep.append(e)
        self.edges = keep
        return dropped

    def degree(self) -> dict[int, int]:
        d: dict[int, int] = defaultdict(int)
        for e in self.edges:
            d[e["a"]] += 1
            d[e["b"]] += 1
        return d

    def _shortest(self, s: int, t: int) -> list[int]:
        dist, prev, pq = {s: 0.0}, {}, [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u == t:
                break
            if d > dist.get(u, float("inf")):
                continue
            for i, e in enumerate(self.edges):
                if u not in (e["a"], e["b"]) or e["a"] == e["b"]:
                    continue
                v = e["b"] if e["a"] == u else e["a"]
                nd = d + e["length"]
                if nd < dist.get(v, float("inf")):
                    dist[v], prev[v] = nd, (u, i)
                    heapq.heappush(pq, (nd, v))
        if t not in prev and s != t:
            raise ImportError_("경로 조각이 서로 연결되지 않습니다")
        path, cur = [], t
        while cur != s:
            u, i = prev[cur]
            path.append(i)
            cur = u
        return path

    def make_eulerian(self) -> list[int]:
        """Duplicate shortest connections between odd nodes (greedy pairing) so a closed walk exists."""
        dup_parts = []
        while True:
            odd = [n for n, d in self.degree().items() if d % 2]
            if not odd:
                return dup_parts
            best = None
            for i in range(len(odd)):
                for j in range(i + 1, len(odd)):
                    p = self._shortest(odd[i], odd[j])
                    L = sum(self.edges[k]["length"] for k in p)
                    if best is None or L < best[0]:
                        best = (L, p)
            for k in best[1]:
                e = dict(self.edges[k])
                self.edges.append(e)
                dup_parts.append(e["part"])

    def circuit(self, start: int) -> list[list[float]]:
        """Hierholzer walk; at each node prefer parts in their digitised direction, then longer ones."""
        unused = set(range(len(self.edges)))
        stack, out = [(start, None)], []
        while stack:
            u, via = stack[-1]
            options = [i for i in unused if u in (self.edges[i]["a"], self.edges[i]["b"])]
            if options:
                options.sort(key=lambda i: (self.edges[i]["a"] != u, -self.edges[i]["length"]))
                i = options[0]
                unused.remove(i)
                e = self.edges[i]
                fwd = e["a"] == u
                stack.append((e["b"] if fwd else e["a"], (i, fwd)))
            else:
                stack.pop()
                out.append(via)
        walk = [v for v in reversed(out) if v is not None]
        path: list[list[float]] = []
        for i, fwd in walk:
            pts = self.edges[i]["pts"] if fwd else list(reversed(self.edges[i]["pts"]))
            path.extend(pts if not path else pts[1:])
        return path


def _course_no(rec: dict, shp: Path) -> str | None:
    for v in (rec.get("route_name"), shp.stem):
        m = re.search(r"(\d+)\s*코스", str(v or "")) or re.search(r"course-(\d+)", str(v or ""))
        if m:
            return m.group(1)
    return None


def _official_m(rec: dict) -> float | None:
    for k in ("total_di_1", "total_dist"):
        v = rec.get(k)
        if v in (None, ""):
            continue
        m = re.search(r"\d+(\.\d+)?", str(v))
        if m:
            x = float(m.group())
            return x * 1000 if "km" in str(v) else x
    return None


def run(db: Session, folder: Path, *, license: str | None = None, dry_run: bool = False) -> dict:
    shps, files, warnings = _file_sets(folder)
    graphs: dict[str, tuple[_Graph, dict]] = {}
    report = {"files": sorted(p.name for p in files), "warnings": warnings, "routes": {}, "problems": []}
    for shp in shps:
        r, enc, crs, tf = _open(shp)
        fields = [f[0] for f in r.fields[1:]]
        for sr in r.iterShapeRecords():
            rec = dict(zip(fields, sr.record))
            no = _course_no(rec, shp)
            if not no:
                report["problems"].append(f"{shp.name}: 코스 번호를 찾지 못했습니다 {rec}")
                continue
            if "LINE" not in r.shapeTypeName.upper():
                report["problems"].append(f"{shp.name}: 선형이 아닌 지오메트리({r.shapeTypeName})")
                continue
            g, meta = graphs.setdefault(no, (_Graph(), {"record": rec, "file": shp.name, "crs": crs.name, "encoding": enc, "parts": 0}))
            for pts in _parts(sr.shape, tf):
                g.add(pts, meta["parts"])
                meta["parts"] += 1
        _close(r)

    # The start/finish (서귀포시청 제1청사) is the endpoint node shared by the most courses.
    shared, best = None, 1
    all_nodes = [(no, n) for no, (g, _) in graphs.items() for n in g.nodes]
    for _no, cand in all_nodes:
        count = len({no for no, n in all_nodes if haversine_m(cand[0], cand[1], n[0], n[1]) <= SNAP_M})
        if count > best:
            shared, best = cand, count

    for no, (g, meta) in sorted(graphs.items()):
        info = {"file": meta["file"], "crs": meta["crs"], "parts": meta["parts"]}
        info["dropped_duplicate_parts"] = g.dedupe()
        info["repeated_parts"] = g.make_eulerian()
        start = 0
        if shared:
            start = min(range(len(g.nodes)), key=lambda i: haversine_m(g.nodes[i][0], g.nodes[i][1], shared[0], shared[1]))
        path = g.circuit(start)
        info["rotated_to_shared_start"] = False
        if shared and haversine_m(path[0][0], path[0][1], shared[0], shared[1]) > SNAP_M                 and haversine_m(path[0][0], path[0][1], path[-1][0], path[-1][1]) <= SNAP_M:
            # Closed loop digitised from elsewhere: start it at the vertex closest to City Hall.
            i = min(range(len(path)), key=lambda k: haversine_m(path[k][0], path[k][1], shared[0], shared[1]))
            if haversine_m(path[i][0], path[i][1], shared[0], shared[1]) <= ROTATE_MAX_M:
                path = path[i:] + path[1:i + 1]
                info["rotated_to_shared_start"] = True
        length = path_length_m(path)
        official = _official_m(meta["record"])
        info.update({"length_km": round(length / 1000, 2), "official_km": round(official / 1000, 2) if official else None,
                     "start": [round(c, 6) for c in path[0]], "direction": "SHP 선 방향 기준 — 경유지 좌표(locate-waypoints)로 검증"})
        if official and abs(length - official) / official > 0.12:
            report["problems"].append(f"{no}코스: 복원한 경로 {length / 1000:.2f}km가 공시 거리 {official / 1000:.1f}km와 12% 넘게 다릅니다")
        report["routes"][no] = info
        if dry_run:
            continue
        route = db.scalar(select(TrailRoute).where(TrailRoute.trail_type == "hayoung_olle", TrailRoute.course_no == no))
        if route is None:
            report["problems"].append(f"{no}코스: 시드에 없는 코스 — 건너뜀")
            continue
        route.segments = [RouteSegment(seq=i, coords=e["pts"], length_m=e["length"], source_id=SOURCE_ID)
                          for i, e in enumerate(sorted({e["part"]: e for e in g.edges}.values(), key=lambda e: e["part"]))]
        route.path = path
        route.has_geometry = True
        route.distance_km = round(length / 1000, 2)
        route.start_lat, route.start_lon = path[0]
        route.end_lat, route.end_lon = path[-1]
        route.version = (route.version or 1) + 1
        for w in route.waypoints:  # endpoints are known: the loop starts and ends at City Hall
            if w.kind == "S":
                w.lat, w.lon, w.along_m = path[0][0], path[0][1], 0.0
            elif w.kind == "F":
                w.lat, w.lon, w.along_m = path[-1][0], path[-1][1], length
    seeded = {r.course_no for r in db.scalars(select(TrailRoute).where(TrailRoute.trail_type == "hayoung_olle"))}
    missing = sorted(seeded - set(graphs))
    if missing:
        report["missing_courses"] = [f"하영올레 {n}코스" for n in missing]
    if not dry_run:
        register(db, SOURCE_ID, files, license=license)
        db.commit()
    return report
