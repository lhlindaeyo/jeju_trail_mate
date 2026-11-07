import { useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api, gpsQueue } from "../api";
import { useApp } from "../App";

const YELLOW = "#FFC83D";
const JEJU: [number, number] = [33.38, 126.55];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTR = "© OpenStreetMap contributors · 개발용 타일";

function fmtKm(m: number | null | undefined) {
  return m == null ? "—" : (m / 1000).toFixed(1);
}

/** Split a path at `doneM` metres: [done part, remaining part]. */
function splitPath(path: [number, number][], doneM: number): [[number, number][], [number, number][]] {
  if (!path.length || doneM <= 0) return [[], path];
  let acc = 0;
  for (let i = 1; i < path.length; i++) {
    const seg = L.latLng(path[i - 1]).distanceTo(L.latLng(path[i]));
    if (acc + seg >= doneM) {
      const t = (doneM - acc) / seg;
      const mid: [number, number] = [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * t, path[i - 1][1] + (path[i][1] - path[i - 1][1]) * t];
      return [[...path.slice(0, i), mid], [mid, ...path.slice(i)]];
    }
    acc += seg;
  }
  return [path, []];
}

export default function Home() {
  const { course, session, setSession, trip, me, refreshMe, pos, locOn, setLocOn, locMsg, openSheet, toast, pushNotifications, flushGps, go, refreshCourse } = useApp();
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const meMarker = useRef<L.Marker | null>(null);
  const [spin, setSpin] = useState(false);
  const [busy, setBusy] = useState(false);

  const active = session && session.status !== "ended" && session.route_id === course?.id;
  const doneM = active && session.done_km != null ? session.done_km * 1000 : 0;
  const totalM = course?.distance_km != null ? course.distance_km * 1000 : null;
  const leftM = totalM != null ? Math.max(totalM - doneM, 0) : null;
  const nextWp = active ? session.next_waypoint : null;

  // ---- maps ---------------------------------------------------------------------------
  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { zoomControl: false, attributionControl: true }).setView(JEJU, 10);
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: TILE_ATTR }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = null; meMarker.current = null; };
  }, []);

  // route: done = yellow solid, remaining = white dashed, S/F yellow dots, waypoints white
  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    if (!course) return;
    const path: [number, number][] = course.path ?? [];
    if (path.length > 1) {
      const [done, rest] = splitPath(path, doneM);
      L.polyline(path, { color: "#10312a", opacity: .5, weight: 9, lineCap: "round" }).addTo(g);
      L.polyline(rest, { color: "#fff", weight: 4, dashArray: "1 8", lineCap: "round" }).addTo(g);
      if (done.length > 1) L.polyline(done, { color: YELLOW, weight: 5, lineCap: "round" }).addTo(g);
    }
    const visited = new Set<string>(active ? session.visited_waypoints : []);
    for (const w of course.waypoints ?? []) {
      if (!w.location) continue;
      const end = w.kind !== "W";
      L.circleMarker(w.location, { radius: end ? 6 : 4, color: "#10312a", weight: end ? 2 : 1.5,
        fillColor: end || visited.has(w.name) ? YELLOW : "#fff", fillOpacity: 1 })
        .bindTooltip(`${w.code} ${w.name}`).addTo(g);
    }
    if (path.length > 1) m.fitBounds(L.latLngBounds(path).pad(0.25), { paddingTopLeft: [140, 120], paddingBottomRight: [20, 170] });
    else if (course.start) m.setView(course.start, 13);
  }, [course, doneM, active, session?.visited_waypoints]); // eslint-disable-line react-hooks/exhaustive-deps

  // my position (outside Jeju → shown at the course start or island centre, labelled as an example)
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (!pos) {
      meMarker.current?.remove(); meMarker.current = null;
      return;
    }
    const ll: [number, number] = pos.onIsland ? [pos.lat, pos.lon] : (course?.path?.[0] ?? course?.start ?? JEJU);
    const icon = L.divIcon({ className: "", html: '<div class="me-marker"><span role="img" aria-label="내 위치">🍊</span></div>', iconSize: [32, 32], iconAnchor: [16, 16] });
    if (!meMarker.current) {
      meMarker.current = L.marker(ll, { icon, keyboard: false, interactive: false }).addTo(m);
      m.setView(ll, Math.max(m.getZoom(), 15));
    } else {
      meMarker.current.setLatLng(ll);
    }
  }, [pos, course]);

  // ---- session controls -----------------------------------------------------------------
  const start = async () => {
    if (!trip) { openSheet("plan"); toast("먼저 여행을 만들어 주세요"); return; }
    if (!course) { go("explore"); toast("걸을 코스를 먼저 골라 주세요"); return; }
    setBusy(true);
    try {
      if (!me.consents.location) {
        if (!confirm("걷는 동안 위치를 기록해 진행률·경유지 도착을 계산해요. 세션을 끝내면 기록을 멈춥니다. 위치 기록에 동의할까요?")) {
          const r = await api("/v1/sessions", { body: { trip_id: trip.id, route_id: course.id, tracking_mode: "manual" } });
          setSession(r); pushNotifications(r.notifications); toast("위치 없이 기록을 시작했어요");
          return;
        }
        await api("/v1/me/preferences", { method: "PATCH", body: { consent_location: true } });
        await refreshMe();
      }
      const r = await api("/v1/sessions", { body: { trip_id: trip.id, route_id: course.id } });
      setSession(r); pushNotifications(r.notifications);
      if (!locOn) setLocOn(true);
      toast("걷기 기록을 시작했어요");
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const control = async (action: "pause" | "resume" | "end") => {
    if (action === "end" && !confirm("오늘 걷기를 마칠까요?")) return;
    if (action !== "resume") await flushGps();
    try {
      const r = await api(`/v1/sessions/${session!.session_id}/${action}`, { method: "POST" });
      setSession(r); pushNotifications(r.notifications);
      if (action === "end") { gpsQueue.clear(); toast("수고했어요! 오늘 걷기를 마쳤어요"); }
    } catch (e) { toast((e as Error).message); }
  };
  const refresh = async () => {
    setSpin(true); setTimeout(() => setSpin(false), 650);
    await flushGps();
    if (session) { try { setSession((await api(`/v1/sessions/${session.session_id}`))); } catch { /* ignore */ } }
    await refreshCourse();
  };

  const isHy = course?.trail_type === "hayoung_olle";
  const region = !course ? "코스탐색에서 걸을 길을 골라 주세요"
    : isHy ? "서귀포시 · 서귀포시청 순환" : `${course.start_point} → ${course.end_point}`;
  const visitedCount = active ? session.visited_waypoints.filter((n: string) => n !== "서귀포시청제1청사").length : 0;

  return (
    <section aria-label="홈">
      <div id="map" ref={mapEl} role="region" aria-label="코스 지도" />
      <div className="vignette" />
      <div className="topfade" />
      <header className="top">
        <div>
          <h1>{course ? course.name : "JEJU TRAIL MATE"}
            {course && <span className={`badge typebadge ${isHy ? "yellow" : ""}`}>{course.trail_label}</span>}</h1>
          <p>{region}</p>
        </div>
        <button className="menu" aria-label="메뉴" onClick={() => openSheet("menu")}><i /></button>
      </header>
      <button className={`locate ${locOn ? "on" : ""}`} type="button" onClick={() => setLocOn(!locOn)} aria-pressed={locOn}>
        <b /><span>{locOn ? locMsg || "위치 확인 중" : "내 위치 켜기"}</span>
      </button>

      <div className="stat">
        <div className="n">{nextWp?.distance_m != null ? (nextWp.distance_m / 1000).toFixed(1) : "—"}<small>km</small></div>
        <div className="l">{nextWp ? `다음 포인트 · ${nextWp.name}` : "다음 포인트까지"}</div>
        <div className="n">{fmtKm(leftM)}<small>km</small></div>
        <div className="l">목적지까지</div>
        <button className={`refresh ${spin ? "spin" : ""}`} type="button" onClick={refresh}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></svg>새로고침
        </button>
      </div>

      <div className="chip2"><div className="n">{course?.course_no ?? "—"}</div><div className="l">코스</div>
        <div className="n">{course?.waypoints?.length ? visitedCount : "—"}</div><div className="l">포인트</div></div>

      <div className="ctrl">
        {!active && <button className="primary" onClick={start} disabled={busy}>걷기 시작</button>}
        {active && session.status === "active" && <button onClick={() => control("pause")}>일시정지</button>}
        {active && session.status === "paused" && <button className="primary" onClick={() => control("resume")}>다시 걷기</button>}
        {active && <button className="dark" onClick={() => control("end")}>종료</button>}
      </div>

      <div className="summary">
        <div className={`avatar ${active ? "rec" : ""}`} aria-hidden="true">
        </div>
        <div className="name">
          <b>{course?.name ?? "코스 미선택"}</b>
          <span>{active ? (session.tracking_mode === "manual" ? "수동 기록 중" : `기록 중 · GPS ${session.gps_quality === "poor" ? "약함" : session.gps_accuracy_m != null ? `±${session.gps_accuracy_m}m` : "확인 중"}`)
            : totalM != null ? `전체 ${course!.distance_km.toFixed(1)} km` : course ? "거리 계산 예정" : "—"}</span>
        </div>
        <div className="kv"><b>{active ? fmtKm(doneM) : "0.0"}</b><span>진행 km</span></div>
        <div className="kv"><b>{fmtKm(leftM)}</b><span>남은 km</span></div>
      </div>
    </section>
  );
}
