import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { api, gpsQueue } from "../api";
import { useApp } from "../App";
import { t } from "../i18n";
import { SIM_SPEEDS, sim, useSim } from "../sim";

const YELLOW = "#FFC83D";
const JEJU: [number, number] = [33.38, 126.55];
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const tileAttr = () => t("© OpenStreetMap contributors · 개발용 타일", "© OpenStreetMap contributors · dev tiles");

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

const ON_COURSE_M = 500; // farther than this from the course line, the live position says nothing about progress

/** Cumulative metres along a path, cum[i] = distance from the start to path[i]. */
function cumulative(path: [number, number][]): number[] {
  const cum = [0];
  for (let i = 1; i < path.length; i++) cum.push(cum[i - 1] + L.latLng(path[i - 1]).distanceTo(L.latLng(path[i])));
  return cum;
}

/** Where on the path a position is: metres along and metres off. Only the stretch from `minAlong` on is
 *  considered (keeps a walker from jumping back, and picks the start — not the finish — of a loop course). */
function alongOnPath(path: [number, number][], cum: number[], p: [number, number], minAlong = 0) {
  const kx = 111320 * Math.cos((p[0] * Math.PI) / 180), ky = 110540;
  let best: { along: number; off: number } | null = null;
  for (let i = 1; i < path.length; i++) {
    if (cum[i] < minAlong) continue;
    const ax = (path[i - 1][1] - p[1]) * kx, ay = (path[i - 1][0] - p[0]) * ky;
    const bx = (path[i][1] - p[1]) * kx, by = (path[i][0] - p[0]) * ky;
    const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy;
    const t = len2 ? Math.min(1, Math.max(0, -(ax * dx + ay * dy) / len2)) : 0;
    const off = Math.hypot(ax + dx * t, ay + dy * t);
    const along = Math.max(minAlong, cum[i - 1] + t * (cum[i] - cum[i - 1]));
    if (!best || off < best.off - 1) best = { along, off }; // ties keep the earlier stretch
  }
  return best;
}

/** Demo mode ("가상 위치") controls: walk the course, fast-forward, step off the route. */
function SimPanel() {
  const s = useSim((x) => x);
  const { locOn, setLocOn, setSimOn } = useApp();
  const play = () => { if (!locOn) setLocOn(true); sim.play(); };
  return (
    <div className="simpanel" role="group" aria-label={t("가상 위치 시뮬레이터", "Demo location simulator")}>
      <div className="simhead"><b>{t("가상 위치", "Demo location")}</b><span className="badge yellow">{t("시연", "Demo")}</span>
        <button type="button" className="simoff" onClick={() => setSimOn(false)} aria-label={t("가상 위치 끄기", "Turn off demo location")}>{t("끄기", "Off")}</button></div>
      {s.hasPath ? (
        <>
          <input type="range" min={0} max={Math.round(s.total)} step={10} value={Math.round(s.along)}
            onChange={(e) => { if (!locOn) setLocOn(true); sim.seek(Number(e.target.value)); }} aria-label={t("코스 위의 위치", "Position on the course")} />
          <div className="simkm">{s.free ? t("코스 밖 지점", "Off the course") : `${(s.along / 1000).toFixed(2)} / ${(s.total / 1000).toFixed(1)} km`}</div>
          <div className="simrow">
            <button type="button" className="simplay" onClick={s.playing ? () => sim.pause() : play}>{s.playing ? t("멈춤", "Pause") : s.along >= s.total ? t("처음부터", "Restart") : t("걷기", "Walk")}</button>
            {SIM_SPEEDS.map((v) => (
              <button key={v} type="button" className="simspeed" aria-pressed={s.speed === v} onClick={() => sim.setSpeed(v)}>{v}×</button>
            ))}
          </div>
          <button type="button" className={`simoffroute ${s.offRoute ? "on" : ""}`} aria-pressed={s.offRoute} onClick={() => sim.toggleOffRoute()}>
            {s.offRoute ? t("코스로 돌아가기", "Back to the course") : t("길 벗어나기 (80m)", "Step off the route (80 m)")}</button>
        </>
      ) : <p className="simnote">{t("이 코스는 경로 좌표가 없어 자동 걷기를 할 수 없어요.", "This course has no path data, so it can't be walked automatically.")}</p>}
      <p className="simnote">{t("지도를 누르면 그곳으로 이동해요.", "Tap the map to move there.")}</p>
    </div>
  );
}

export default function Home() {
  const { course, session, setSession, trip, me, refreshMe, pos, locOn, setLocOn, locMsg, openSheet, toast, pushNotifications, flushGps, go, refreshCourse, simOn } = useApp();
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);
  const meMarker = useRef<L.Marker | null>(null);
  const lineCredit = useRef("");
  const [spin, setSpin] = useState(false);
  const [busy, setBusy] = useState(false);

  const active = session && session.status !== "ended" && session.route_id === course?.id;
  const doneM = active && session.done_km != null ? session.done_km * 1000 : 0;
  const totalM = course?.distance_km != null ? course.distance_km * 1000 : null;

  // Live progress from the current position on the course line, recomputed on every position update instead of
  // waiting for the recorded track to reach the server. The line is often shorter or longer than the official
  // distance, so the share walked along the line is applied to the official distance.
  const path: [number, number][] = course?.path ?? [];
  const cum = useMemo(() => (path.length > 1 ? cumulative(path) : []), [course?.id, path.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const live = useMemo(() => {
    if (!pos?.onIsland || cum.length < 2 || totalM == null) return null;
    const lineM = cum[cum.length - 1];
    const fromM = active ? Math.max(doneM - 200, 0) : 0; // the recorded progress is metres along this same line
    const hit = alongOnPath(path, cum, [pos.lat, pos.lon], fromM);
    return hit && hit.off <= ON_COURSE_M ? (hit.along / lineM) * totalM : null;
  }, [pos?.lat, pos?.lon, pos?.onIsland, cum, totalM, active, doneM]); // eslint-disable-line react-hooks/exhaustive-deps
  const shownDoneM = live ?? doneM;
  const leftM = totalM != null ? Math.max(totalM - shownDoneM, 0) : null;

  // ---- maps ---------------------------------------------------------------------------
  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { zoomControl: false, attributionControl: true }).setView(JEJU, 10);
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: tileAttr() }).addTo(m);
    layer.current = L.layerGroup().addTo(m);
    m.on("click", (e: L.LeafletMouseEvent) => { if (sim.snap.enabled) sim.teleport(e.latlng.lat, e.latlng.lng); });
    map.current = m;
    return () => { m.remove(); map.current = null; meMarker.current = null; };
  }, []);

  // route: done = yellow solid, remaining = white dashed, S/F yellow dots, waypoints white
  useEffect(() => {
    const m = map.current, g = layer.current;
    if (!m || !g) return;
    g.clearLayers();
    // the drawn line's own credit (OSM-derived lines are ODbL) joins the map's attribution
    if (lineCredit.current) m.attributionControl.removeAttribution(lineCredit.current);
    lineCredit.current = course?.geometry_attribution ?? "";
    if (lineCredit.current) m.attributionControl.addAttribution(lineCredit.current);
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
    const icon = L.divIcon({ className: "", html: `<div class="me-marker"><span role="img" aria-label="${t("내 위치", "My location")}">🍊</span></div>`, iconSize: [32, 32], iconAnchor: [16, 16] });
    if (!meMarker.current) {
      meMarker.current = L.marker(ll, { icon, keyboard: false, interactive: false }).addTo(m);
      m.setView(ll, Math.max(m.getZoom(), 15));
    } else {
      meMarker.current.setLatLng(ll);
      if (!m.getBounds().contains(ll)) m.panTo(ll); // keep a moving (e.g. simulated) position on screen
    }
  }, [pos, course]);

  // ---- session controls -----------------------------------------------------------------
  const start = async () => {
    if (!trip) { openSheet("plan"); toast(t("먼저 여행을 만들어 주세요", "Create a trip first")); return; }
    if (!course) { go("explore"); toast(t("걸을 코스를 먼저 골라 주세요", "Pick a course to walk first")); return; }
    setBusy(true);
    try {
      if (!me.consents.location) {
        if (!confirm(t("걷는 동안 위치를 기록해 진행률·경유지 도착을 계산해요. 세션을 끝내면 기록을 멈춥니다. 위치 기록에 동의할까요?",
          "While you walk, your location is recorded to work out progress and waypoint arrivals. Recording stops when you end the walk. Allow location recording?"))) {
          // A recording without positions has nothing to measure (the app has no screen to type a distance into).
          toast(t("위치 기록에 동의해야 걷기를 기록할 수 있어요. 지도와 앞길브리핑은 그대로 쓸 수 있어요",
            "Walks can only be recorded with location consent. The map and trail brief still work"));
          return;
        }
        await api("/v1/me/preferences", { method: "PATCH", body: { consent_location: true } });
        await refreshMe();
      }
      const r = await api("/v1/sessions", { body: { trip_id: trip.id, route_id: course.id } });
      setSession(r); pushNotifications(r.notifications);
      if (!locOn) setLocOn(true);
      toast(t("걷기 기록을 시작했어요", "Started recording your walk"));
    } catch (e) { toast((e as Error).message); } finally { setBusy(false); }
  };
  const control = async (action: "pause" | "resume" | "end") => {
    if (action === "end" && !confirm(t("오늘 걷기를 마칠까요?", "End today's walk?"))) return;
    if (action !== "resume") await flushGps();
    try {
      const r = await api(`/v1/sessions/${session!.session_id}/${action}`, { method: "POST" });
      setSession(r); pushNotifications(r.notifications);
      if (action === "end") { gpsQueue.clear(); toast(t("수고했어요! 오늘 걷기를 마쳤어요", "Well done! Today's walk is finished")); }
    } catch (e) { toast((e as Error).message); }
  };
  const refresh = async () => {
    setSpin(true); setTimeout(() => setSpin(false), 650);
    await flushGps();
    if (session) { try { setSession((await api(`/v1/sessions/${session.session_id}`))); } catch { /* ignore */ } }
    await refreshCourse();
  };

  const isHy = course?.trail_type === "hayoung_olle";
  const region = !course ? t("코스탐색에서 걸을 길을 골라 주세요", "Pick a trail in Courses")
    : isHy ? t("서귀포시 · 서귀포시청 순환", "Seogwipo · loop from Seogwipo City Hall")
    : course.start_point && course.end_point ? `${course.start_point} → ${course.end_point}` : course.name;

  return (
    <section aria-label={t("홈", "Home")}>
      <div id="map" ref={mapEl} role="region" aria-label={t("코스 지도", "Course map")} />
      <div className="vignette" />
      <div className="topfade" />
      <header className="top">
        <div>
          <h1>{course ? course.name : "JEJU TRAIL MATE"}
            {course && <span className={`badge typebadge ${isHy ? "yellow" : ""}`}>{course.trail_label}</span>}</h1>
          <p>{region}</p>
        </div>
        <button className="menu" aria-label={t("메뉴", "Menu")} onClick={() => openSheet("menu")}><i /></button>
      </header>
      <button className={`locate ${locOn ? "on" : ""}`} type="button" onClick={() => setLocOn(!locOn)} aria-pressed={locOn}>
        <b /><span>{locOn ? locMsg || t("위치 확인 중", "Finding location") : simOn ? t("가상 위치 켜기", "Turn on demo location") : t("내 위치 켜기", "Show my location")}</span>
      </button>
      {simOn && <SimPanel />}

      <div className="stat">
        <div className="n">{fmtKm(leftM)}<small>km</small></div>
        <div className="l">{t("목적지까지", "To the finish")}</div>
        <button className={`refresh ${spin ? "spin" : ""}`} type="button" onClick={refresh}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M20 11a8 8 0 1 0-2.3 5.7" /><path d="M20 4v7h-7" /></svg>{t("새로고침", "Refresh")}
        </button>
      </div>

      <div className="ctrl">
        {!active && <button className="primary" onClick={start} disabled={busy}>{t("걷기 시작", "Start walk")}</button>}
        {active && session.status === "active" && <button onClick={() => control("pause")}>{t("일시정지", "Pause")}</button>}
        {active && session.status === "paused" && <button className="primary" onClick={() => control("resume")}>{t("다시 걷기", "Resume")}</button>}
        {active && <button className="dark" onClick={() => control("end")}>{t("종료", "End")}</button>}
      </div>

      <div className="summary">
        <div className={`avatar ${active ? "rec" : ""}`} aria-hidden="true">
        </div>
        <div className="name">
          <b>{course?.name ?? t("코스 미선택", "No course picked")}</b>
          <span>{active ? `${t("기록 중", "Recording")} · GPS ${session.gps_quality === "poor" ? t("약함", "weak") : session.gps_accuracy_m != null ? `±${session.gps_accuracy_m}m` : t("확인 중", "checking")}`
            : totalM != null ? t(`전체 ${course!.distance_km.toFixed(1)} km`, `${course!.distance_km.toFixed(1)} km total`) : course ? t("거리 계산 예정", "Distance not yet known") : "—"}</span>
        </div>
        <div className="kv"><b>{active ? fmtKm(shownDoneM) : "0.0"}</b><span>{t("진행 km", "km done")}</span></div>
        <div className="kv"><b>{fmtKm(leftM)}</b><span>{t("남은 km", "km left")}</span></div>
      </div>
    </section>
  );
}
