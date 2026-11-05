import { useCallback, useEffect, useRef, useState } from "react";
import L from "leaflet";
import { api, gpsQueue, type Json } from "../api";
import { useApp } from "../App";
import { NewTripForm } from "./Home";

const MIN_POINT_INTERVAL_MS = 15_000;
const FLUSH_INTERVAL_MS = 60_000;

export default function Walk() {
  const { trip, me, refreshMe, session, setSession, pushNotifications, go } = useApp();
  const [routes, setRoutes] = useState<Json[]>([]);
  const [routeId, setRouteId] = useState<string>("");
  const [pos, setPos] = useState<{ lat: number; lon: number; acc: number } | null>(null);
  const [geoErr, setGeoErr] = useState("");
  const [msg, setMsg] = useState("");
  const [manualKm, setManualKm] = useState("");
  const [track, setTrack] = useState<[number, number][]>([]);
  const mapEl = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layers = useRef<{ route?: L.LayerGroup; me?: L.CircleMarker; acc?: L.Circle; track?: L.Polyline }>({});
  const lastPointAt = useRef(0);

  const active = session && session.status !== "ended";
  const gpsMode = active && session.tracking_mode === "gps";

  useEffect(() => {
    if (!trip) return;
    Promise.all([api("/v1/routes"), api(`/v1/trips/${trip.id}/today`)]).then(([r, t]) => {
      setRoutes(r.routes);
      setRouteId((cur) => cur || t.schedule?.route?.id || "");
    }).catch((e) => setMsg(e.message));
  }, [trip]);

  // --- map ---------------------------------------------------------------------------
  useEffect(() => {
    if (!mapEl.current || map.current) return;
    const m = L.map(mapEl.current, { zoomControl: true }).setView([33.38, 126.55], 10);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "© OpenStreetMap contributors (개발용 타일 — 운영 시 지도 SDK로 교체)",
    }).addTo(m);
    map.current = m;
    return () => { m.remove(); map.current = null; };
  }, [trip]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    layers.current.route?.remove();
    const r = routes.find((x) => x.id === routeId);
    if (r?.start && r?.end) {
      const g = L.layerGroup([
        L.circleMarker(r.start, { radius: 8, color: "#315d49", fillOpacity: 0.9 }).bindTooltip(`시작: ${r.start_point} (근사 위치)`),
        L.circleMarker(r.end, { radius: 8, color: "#c66c45", fillOpacity: 0.9 }).bindTooltip(`종료: ${r.end_point} (근사 위치)`),
      ]).addTo(m);
      layers.current.route = g;
      if (!pos) m.fitBounds(L.latLngBounds([r.start, r.end]).pad(0.4));
    }
  }, [routes, routeId, pos]);

  useEffect(() => {
    const m = map.current;
    if (!m || !pos) return;
    const ll: [number, number] = [pos.lat, pos.lon];
    if (!layers.current.me) {
      layers.current.acc = L.circle(ll, { radius: pos.acc, color: "#2f6177", weight: 1, fillOpacity: 0.1 }).addTo(m);
      layers.current.me = L.circleMarker(ll, { radius: 7, color: "#fff", weight: 2, fillColor: "#2f6177", fillOpacity: 1 }).addTo(m);
      m.setView(ll, 15);
    } else {
      layers.current.me.setLatLng(ll);
      layers.current.acc?.setLatLng(ll).setRadius(pos.acc);
    }
    if (track.length > 1) {
      if (!layers.current.track) layers.current.track = L.polyline(track, { color: "#315d49", weight: 4 }).addTo(m);
      else layers.current.track.setLatLngs(track);
    }
  }, [pos, track]);

  // --- GPS ---------------------------------------------------------------------------
  const flush = useCallback(async () => {
    try {
      const r = await gpsQueue.flush();
      if (r) {
        setSession(r.summary);
        pushNotifications(r.notifications);
      }
    } catch (e) { setMsg(`위치 업로드 대기 중: ${(e as Error).message}`); }
  }, [setSession, pushNotifications]);

  useEffect(() => {
    if (!gpsMode || session.status !== "active") return;
    if (!("geolocation" in navigator)) { setGeoErr("이 기기는 위치 기능을 지원하지 않습니다. 수동 기록을 사용하세요."); return; }
    const watch = navigator.geolocation.watchPosition(
      (p) => {
        const point = { lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy };
        setPos(point);
        setGeoErr(p.coords.accuracy > 50 ? `GPS 정확도가 낮습니다(±${Math.round(p.coords.accuracy)}m). 진행률 추정이 부정확할 수 있어요.` : "");
        const now = Date.now();
        if (now - lastPointAt.current >= MIN_POINT_INTERVAL_MS) {
          lastPointAt.current = now;
          gpsQueue.push(session.session_id, { timestamp: new Date(p.timestamp).toISOString(), lat: point.lat, lon: point.lon, accuracy_m: point.acc });
          if (point.acc <= 50) setTrack((t) => [...t, [point.lat, point.lon]]);
        }
      },
      (e) => setGeoErr(e.code === e.PERMISSION_DENIED
        ? "위치 권한이 거부되었습니다. 브라우저 설정에서 허용하거나, 세션을 종료하고 수동 기록으로 진행하세요."
        : "위치를 가져오지 못했습니다. 하늘이 트인 곳에서 다시 시도해 보세요."),
      { enableHighAccuracy: true, maximumAge: 10_000, timeout: 30_000 },
    );
    const t = setInterval(flush, FLUSH_INTERVAL_MS);
    return () => { navigator.geolocation.clearWatch(watch); clearInterval(t); };
  }, [gpsMode, session?.status, session?.session_id, flush]);

  // --- session controls ----------------------------------------------------------------
  const start = async (mode: "gps" | "manual") => {
    setMsg("");
    try {
      if (mode === "gps" && !me.consents.location) {
        await api("/v1/me/preferences", { method: "PATCH", body: { consent_location: true } });
        await refreshMe();
      }
      const r = await api("/v1/sessions", { body: { trip_id: trip!.id, route_id: routeId || null, tracking_mode: mode } });
      setSession(r);
      setTrack([]);
      pushNotifications(r.notifications);
    } catch (e) { setMsg((e as Error).message); }
  };
  const control = async (action: "pause" | "resume" | "end") => {
    if (action === "end" && !confirm("오늘 트레킹을 종료할까요?")) return;
    if (action !== "resume") await flush();
    try {
      const r = await api(`/v1/sessions/${session!.session_id}/${action}`, { method: "POST" });
      setSession(r);
      pushNotifications(r.notifications);
      if (action === "end") gpsQueue.clear();
    } catch (e) { setMsg((e as Error).message); }
  };
  const saveManual = async () => {
    try {
      const r = await api(`/v1/sessions/${session!.session_id}/manual`, { body: { distance_km: Number(manualKm) } });
      setSession(r);
      pushNotifications(r.notifications);
    } catch (e) { setMsg((e as Error).message); }
  };
  const askEta = () => go("ai");

  if (!trip) return <NewTripForm />;

  return (
    <div>
      <h1>{active ? "걷는 중" : "트레킹"}</h1>
      <div ref={mapEl} className="map" role="region" aria-label="지도" />
      {geoErr && <div className="banner warn">{geoErr}</div>}
      {msg && <div className="banner warn">{msg}</div>}

      {active ? (
        <div className="card">
          <div className="grid2">
            <div><div className="muted small">걸은 거리</div><div className="metric">{session.walked_km}km</div></div>
            <div><div className="muted small">걷는 시간</div><div className="metric">{session.active_minutes}분</div></div>
          </div>
          <div className="small" style={{ marginTop: 8 }}>
            {session.route ?? "코스 미지정"}
            {session.actual_progress != null && <> · 진행률 약 {Math.round(session.actual_progress * 100)}%</>}
            {session.expected_progress != null && <> (계획 {Math.round(session.expected_progress * 100)}%)</>}
            {" · "}{session.tracking_mode === "manual" ? "수동 기록" : `GPS ${session.gps_quality === "good" ? "양호" : session.gps_quality === "poor" ? "약함" : "확인 중"}`}
          </div>
          <p className="muted small">{session.note}</p>
          {session.tracking_mode === "manual" && (
            <div className="row">
              <input type="number" min={0} max={60} step={0.1} value={manualKm} onChange={(e) => setManualKm(e.target.value)} placeholder="지금까지 걸은 km" style={{ flex: 1 }} />
              <button className="btn" onClick={saveManual} disabled={!manualKm}>기록</button>
            </div>
          )}
          <div className="btn-row" style={{ marginTop: 12 }}>
            {session.status === "active"
              ? <button className="btn big secondary" onClick={() => control("pause")}>일시정지</button>
              : <button className="btn big" onClick={() => control("resume")}>다시 걷기</button>}
            <button className="btn big" onClick={() => control("end")}>종료</button>
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn ghost" onClick={askEta}>도착 예상 묻기</button>
            <button className="btn ghost" onClick={() => go("journal")}>지금 순간 기록</button>
          </div>
          {gpsMode && <p className="muted small">화면이 꺼지거나 다른 앱으로 전환되면 브라우저가 위치 기록을 멈출 수 있어요. 기록은 연결이 끊겨도 기기에 보관되었다가 다시 전송됩니다.</p>}
        </div>
      ) : (
        <div className="card">
          <label className="field">오늘 걸을 코스
            <select value={routeId} onChange={(e) => setRouteId(e.target.value)}>
              <option value="">코스 미지정</option>
              {routes.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.distance_km}km)</option>)}
            </select>
          </label>
          {!me.consents.location && <p className="muted small">GPS 기록을 시작하면 위치 기록에 동의하게 됩니다. 위치는 세션 중에만 기록되며 일시정지·종료할 수 있어요. 원치 않으면 수동 기록을 쓰세요.</p>}
          <button className="btn big" onClick={() => start("gps")}>GPS로 트레킹 시작</button>
          <button className="btn ghost" style={{ marginTop: 8, width: "100%" }} onClick={() => start("manual")}>위치 없이 수동 기록</button>
          {session?.status === "ended" && <p className="muted" style={{ marginTop: 10 }}>지난 세션: {session.route ?? "코스 미지정"} {session.walked_km}km · {session.active_minutes}분</p>}
        </div>
      )}

      <div className="banner safety">
        <strong>위급 상황</strong> — 이 앱은 구조 서비스가 아닙니다.{" "}
        <a href="tel:119"><strong>119 전화</strong></a> · 경찰 <a href="tel:112">112</a>
        {pos && <div className="small">현재 좌표: {pos.lat.toFixed(5)}, {pos.lon.toFixed(5)} (±{Math.round(pos.acc)}m)</div>}
      </div>
    </div>
  );
}
