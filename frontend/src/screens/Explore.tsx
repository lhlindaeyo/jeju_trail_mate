import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api, type Json } from "../api";
import { useApp } from "../App";
import PageHead from "./PageHead";

type TT = "jeju_olle" | "hayoung_olle";

const JEJU: [number, number] = [33.38, 126.55];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TEAL = "#0f8f9b";

function dur(r: Json) {
  if (r.duration_min_low && r.duration_min_high) return `${r.duration_min_low / 60}~${r.duration_min_high / 60}시간`;
  return null;
}

/** Jeju overview map. A picked course is drawn briefly on top when its path is known. */
function CourseMap({ route, detail, type }: { route: Json | null; detail: Json | null; type: TT }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    if (!el.current || map.current) return;
    const m = L.map(el.current, { zoomControl: false, attributionControl: false, scrollWheelZoom: false, dragging: !L.Browser.mobile }).setView(JEJU, 9);
    L.tileLayer(TILE_URL, { maxZoom: 19 }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = null; layer.current = null; };
  }, []);

  const path: [number, number][] = route && detail && detail.id === route.id ? detail.path ?? [] : [];
  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    if (path.length > 1) {
      L.polyline(path, { color: "#fff", weight: 9, opacity: 0.9, lineCap: "round" }).addTo(g);
      L.polyline(path, { color: TEAL, weight: 5, lineCap: "round" }).addTo(g);
      for (const w of detail?.waypoints ?? []) {
        if (!w.location) continue;
        const end = w.kind !== "W";
        L.circleMarker(w.location, { radius: end ? 6 : 4, color: "#10312a", weight: 1.5, fillColor: end ? "#ffc83d" : "#fff", fillOpacity: 1 })
          .bindTooltip(w.name).addTo(g);
      }
      m.fitBounds(L.latLngBounds(path).pad(0.35), { maxZoom: 14, animate: true });
    } else if (!route || !route.has_geometry) {
      // 선택이 없거나 그릴 경로가 없는 코스일 때만 섬 전체로 돌아간다.
      // 경로가 아직 로딩 중일 때 축소 애니메이션을 시작하면, 곧 도착하는 확대(fitBounds)가 애니메이션 중이라 무시된다.
      m.setView(JEJU, 9, { animate: false });
    }
  }, [route?.id, detail, path.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="xmap-wrap">
      <div className="xmap" ref={el} role="region" aria-label="제주도 지도" />
      {route && (
        <div className="xmap-chip" aria-live="polite">
          <>
            <b>{type === "jeju_olle" ? `올레 ${route.course_no}코스` : route.name}</b>
            <span>
              {route.start_point && route.end_point ? ` · ${route.start_point} → ${route.end_point}` : ""}
              {route.distance_km != null ? ` · ${Number(route.distance_km).toFixed(1)}km` : ""}
            </span>
          </>
        </div>
      )}
    </div>
  );
}

export default function Explore() {
  const { setCourseId, go, toast } = useApp();
  const [type, setType] = useState<TT>(() => {
    try { return (localStorage.getItem("jtm.exploreType") as TT) || "jeju_olle"; } catch { return "jeju_olle"; }
  });
  const [routes, setRoutes] = useState<Json[]>([]);
  const [err, setErr] = useState("");
  const [selId, setSelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Json | null>(null);
  const detailCache = useRef<Record<string, Json>>({});

  useEffect(() => {
    setErr("");
    setSelId(null);
    api(`/v1/routes?trail_type=${type}`).then((r) => setRoutes(r.routes)).catch((e) => setErr(e.message));
    try { localStorage.setItem("jtm.exploreType", type); } catch { /* ignore */ }
  }, [type]);

  const select = (r: Json) => {
    setSelId(r.id);
    if (!r.has_geometry) { setDetail(null); return; }
    const hit = detailCache.current[r.id];
    if (hit) { setDetail(hit); return; }
    api(`/v1/routes/${r.id}`).then((d) => { detailCache.current[r.id] = d; setDetail(d); }).catch(() => setDetail(null));
  };
  const selected = routes.find((r) => r.id === selId) ?? null;

  const pick = (r: Json) => {
    setCourseId(r.id);
    go("home");
    toast(`${r.name}로 바꿨어요`);
  };

  return (
    <section aria-label="코스탐색">
      <PageHead title="코스탐색" sub="걷고 싶은 길을 고르세요." />
      <div className="seg" role="group" aria-label="길 종류">
        <button type="button" aria-pressed={type === "jeju_olle"} onClick={() => setType("jeju_olle")}>제주올레</button>
        <button type="button" aria-pressed={type === "hayoung_olle"} onClick={() => setType("hayoung_olle")}>하영올레</button>
      </div>
      <CourseMap route={selected} detail={detail} type={type} />
      {err && <p className="err">{err}</p>}

      {routes.map((r) => (
        <div key={r.id} className={`card course ${r.id === selId ? "sel" : ""}`} onClick={() => select(r)} aria-current={r.id === selId ? "true" : undefined}>
          <div className="row">
            <div className={`num ${type === "hayoung_olle" ? "hy" : ""}`}>{r.course_no}</div>
            <div className="grow">
              <b>{type === "jeju_olle" ? `올레 ${r.course_no}코스 · ${r.subtitle ?? ""}` : r.name}</b>
              <span>
                {type === "jeju_olle"
                  ? `${r.distance_km?.toFixed(1)} km · ${dur(r) ?? "시간 정보 없음"}${r.start_point && r.end_point ? ` · ${r.start_point} - ${r.end_point}` : ""}`
                  : `서귀포시청 출발·도착 · 경유지 ${r.waypoints.length - 2}곳 · ${r.distance_km != null ? `${r.distance_km} km` : "거리 계산 예정"}`}
              </span>
            </div>
            <button className="go" type="button" onClick={(e) => { e.stopPropagation(); pick(r); }}>홈에서 보기</button>
          </div>
          {type === "hayoung_olle" && (
            <div className="wps">{r.waypoints.filter((w: Json) => w.kind === "W").map((w: Json) => <i key={w.id}>{w.name}</i>)}</div>
          )}
        </div>
      ))}
    </section>
  );
}
