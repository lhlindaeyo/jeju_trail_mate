import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Json } from "../api";
import { useApp } from "../App";

const REFRESH_MS = 60_000;
const STATUS_LABEL: Record<string, string> = { ok: "여유 있어요", tight: "빠듯해요", late: "일몰 이후 도착", done: "코스 끝", unavailable: "확인 불가" };

const dist = (m: number | null | undefined) => (m == null ? "" : m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m / 10) * 10}m`);

function Daylight({ d }: { d: Json }) {
  const walking = d.mode === "walking";
  if (d.status === "unavailable") {
    return <div className="card"><b>일몰 대비 도착</b><p className="note" style={{ margin: "6px 0 0" }}>{d.message}</p></div>;
  }
  return (
    <div className={`card daylight ${d.status}`}>
      <div className="row"><b className="grow">일몰 대비 도착</b><span className={`badge st-${d.status}`}>{STATUS_LABEL[d.status] ?? d.status}</span></div>
      <p className="lead">{d.message}</p>
      <div className="stats">
        <div><span>일몰</span><b>{d.sunset_hhmm}</b></div>
        <div><span>{walking ? "도착 예상" : "지금 출발 시 도착"}</span><b>{d.eta_hhmm}</b></div>
        {walking
          ? <div><span>남은 거리</span><b>{d.remaining_km}km</b></div>
          : <div><span>늦어도 출발</span><b>{d.latest_start_hhmm}</b></div>}
      </div>
      <p className="note" style={{ margin: "10px 0 0" }}>
        해 지기 {d.buffer_min}분 전({d.finish_by_hhmm})까지 마치는 것을 기준으로 해요. {d.assumptions}
        {d.confidence === "low" && " 지금은 GPS 신호가 약하거나 오래되어 추정이 부정확할 수 있어요."}
      </p>
    </div>
  );
}

function Supplies({ s }: { s: Json }) {
  if (s.status !== "ok") {
    return <div className="card"><b>앞쪽 화장실·식당·쉼터</b><p className="note" style={{ margin: "6px 0 0" }}>{s.message}</p></div>;
  }
  return (
    <div className="card">
      <b>{s.basis === "along_route" ? "앞쪽 화장실·식당·쉼터" : "내 주변 화장실·식당·쉼터"}</b>
      <p className="note" style={{ margin: "2px 0 6px" }}>{s.scope}</p>
      {s.note && <p className="note" style={{ margin: "0 0 6px" }}>{s.note}</p>}
      {s.groups.map((g: Json) => (
        <div key={g.category} className="supply">
          <h3>{g.label}</h3>
          {g.places.length === 0 && <p className="none">{g.message}</p>}
          {g.places.map((p: Json) => (
            <div key={p.name + p.distance_m} className="place">
              <span className="where">{p.ahead_m != null ? `앞 ${dist(p.ahead_m)}` : dist(p.distance_m)}</span>
              <span className="grow"><b>{p.name}</b>{p.detail && <i>{String(p.detail).slice(0, 40)}</i>}</span>
            </div>
          ))}
        </div>
      ))}
      <p className="note" style={{ margin: "8px 0 0" }}>
        {s.caveat} {s.sources.length > 0 && `출처: ${s.sources.map((x: Json) => `${x.name}(${x.ref_date ?? "기준일 미상"})`).join(", ")}`}
      </p>
    </div>
  );
}

function Weather({ w }: { w: Json }) {
  if (w.status !== "ok") {
    return <div className="card"><b>앞 6시간 날씨</b><p className="note" style={{ margin: "6px 0 0" }}>{w.message}</p></div>;
  }
  return (
    <div className="card">
      <b>앞 6시간 날씨</b>
      <p className="lead">{w.headline}</p>
      <div className="hours" role="list">
        {w.hours.map((h: Json) => (
          <div key={h.time} role="listitem" className={Number(h.precip_prob_pct) >= 60 ? "wet" : ""}>
            <span>{h.time.slice(11, 16)}</span><b>{h.temp_c}°</b><span>{h.sky}</span><span>비 {h.precip_prob_pct}%</span><span>{h.wind_ms}m/s</span>
          </div>
        ))}
      </div>
      <p className="note" style={{ margin: "8px 0 0" }}>출처: {w.source} (발표 {w.base_time})</p>
    </div>
  );
}

export default function Brief() {
  const { trip, session, course, pos, locOn, setLocOn, go } = useApp();
  const [data, setData] = useState<Json | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const posRef = useRef(pos);
  posRef.current = pos;
  const hasPos = Boolean(pos?.onIsland);
  const active = Boolean(session && session.status !== "ended");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const q = new URLSearchParams();
      if (trip) q.set("trip_id", trip.id);
      if (!active && course) q.set("route_id", course.id);
      const p = posRef.current;
      if (p?.onIsland) { q.set("lat", String(p.lat)); q.set("lon", String(p.lon)); }
      setData(await api(`/v1/brief?${q}`));
      setErr("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }, [trip, course, active]);

  useEffect(() => { load(); }, [load, hasPos]);
  useEffect(() => {
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [load]);

  const at = data?.generated_at ? String(data.generated_at).slice(11, 16) : "";
  // offline, the service worker serves the last copy: say how old it is instead of passing it off as current
  const ageMin = data?.generated_at ? Math.round((Date.now() - new Date(data.generated_at).getTime()) / 60000) : 0;
  const fromStart = data?.location?.source === "route_start";
  return (
    <section aria-label="앞길브리핑">
      <h1 className="title">앞길 브리핑</h1>
      <p className="sub">
        {data?.route ? `${data.route.name} · ${data.mode === "walking" ? "걷는 중" : "출발 전"}` : "지금 걸어도 되는지, 앞에 뭐가 있는지 한눈에"}
        {at && ` · ${at} 기준`}
      </p>

      {err && <p className="err">{err}</p>}
      {data && ageMin >= 10 && (
        <p className="note"><span className="badge warn">{ageMin}분 전 정보</span> 연결이 끊겼거나 새로 확인하지 못했어요. 시각이 지났으니 도착·일몰 여유는 다시 확인해 주세요.</p>
      )}
      {data && data.mode === "none" && (
        <div className="card">
          <b>코스를 먼저 골라 주세요</b>
          <p className="note" style={{ margin: "6px 0 10px" }}>코스를 고르면 일몰 여유와 앞길 정보를 보여 드려요.</p>
          <button className="btn sm" onClick={() => go("explore")}>코스탐색 열기</button>
        </div>
      )}
      {data && data.mode !== "none" && !hasPos && data.location?.source !== "session" && (
        <div className="card">
          <p className="note" style={{ margin: "0 0 8px" }}>
            {fromStart ? "내 위치를 몰라 코스 시작점 기준으로 보여 드려요." : "내 위치를 켜면 지금 있는 곳 기준으로 보여 드려요."}
          </p>
          {!locOn && <button className="btn sm" onClick={() => setLocOn(true)}>내 위치 켜기</button>}
        </div>
      )}
      {data && data.mode !== "none" && (
        <>
          <Daylight d={data.daylight} />
          <Supplies s={data.supplies} />
          <Weather w={data.weather} />
        </>
      )}
      {!data && !err && <p className="note">불러오는 중…</p>}

      <button className="btn ghost" onClick={load} disabled={busy} style={{ marginTop: 6 }}>{busy ? "확인하는 중…" : "다시 확인"}</button>
      <p className="note">{data?.note ?? "정보는 요청한 순간 계산하고 저장하지 않아요."}</p>
    </section>
  );
}
