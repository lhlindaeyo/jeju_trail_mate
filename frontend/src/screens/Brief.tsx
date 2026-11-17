import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Json } from "../api";
import { useApp } from "../App";
import { longDate, t, weekday } from "../i18n";
import PageHead from "./PageHead";

const REFRESH_MS = 60_000;
const statusLabel = (s: string) => ({ ok: t("여유 있어요", "Plenty of time"), tight: t("빠듯해요", "Tight"), late: t("일몰 이후 도착", "After sunset"),
  done: t("코스 끝", "Course done"), unavailable: t("확인 불가", "Unavailable") } as Record<string, string>)[s];

const dist = (m: number | null | undefined) => (m == null ? "" : m >= 1000 ? `${(m / 1000).toFixed(1)}km` : `${Math.round(m / 10) * 10}m`);

function Daylight({ d }: { d: Json }) {
  const walking = d.mode === "walking";
  if (d.status === "unavailable") {
    return <div className="card"><b>{t("일몰 대비 도착", "Arrival vs. sunset")}</b><p className="note" style={{ margin: "6px 0 0" }}>{d.message}</p></div>;
  }
  return (
    <div className={`card daylight ${d.status}`}>
      <div className="row"><b className="grow">{t("일몰 대비 도착", "Arrival vs. sunset")}</b><span className={`badge st-${d.status}`}>{statusLabel(d.status) ?? d.status}</span></div>
      <p className="lead">{d.message}</p>
      <div className="stats">
        <div><span>{t("일몰", "Sunset")}</span><b>{d.sunset_hhmm}</b></div>
        <div><span>{walking ? t("도착 예상", "Expected arrival") : t("지금 출발 시 도착", "Arrival if you leave now")}</span><b>{d.eta_hhmm}</b></div>
        {walking
          ? <div><span>{t("남은 거리", "Distance left")}</span><b>{d.remaining_km}km</b></div>
          : <div><span>{t("늦어도 출발", "Leave by")}</span><b>{d.latest_start_hhmm}</b></div>}
      </div>
    </div>
  );
}

function Supplies({ s }: { s: Json }) {
  if (s.status !== "ok") {
    return <div className="card"><b>{t("앞쪽 화장실·식당·쉼터", "Toilets, food & rest ahead")}</b><p className="note" style={{ margin: "6px 0 0" }}>{s.message}</p></div>;
  }
  return (
    <div className="card">
      <b>{s.basis === "along_route" ? t("앞쪽 화장실·식당·쉼터", "Toilets, food & rest ahead") : t("내 주변 화장실·식당·쉼터", "Toilets, food & rest around you")}</b>
      <p className="note" style={{ margin: "2px 0 6px" }}>{s.scope}</p>
      {s.groups.map((g: Json) => (
        <div key={g.category} className="supply">
          <h3>{g.label}</h3>
          {g.places.length === 0 && <p className="none">{g.message}</p>}
          {g.places.map((p: Json) => (
            <div key={p.name + p.distance_m} className="place">
              <span className="where">{p.ahead_m != null ? t(`앞 ${dist(p.ahead_m)}`, `${dist(p.ahead_m)} ahead`) : dist(p.distance_m)}</span>
              <span className="grow"><b>{p.name}</b></span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** '2026-11-16T14:00:00+09:00' → '11/16(월)' / 'Mon 11/16'. Read off the KST date in the string, not the device clock's zone. */
function dayLabel(iso: string, long = false) {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  const wdi = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return long ? longDate(m, d, wdi) : t(`${m}/${d}(${weekday(wdi)})`, `${weekday(wdi)} ${m}/${d}`);
}

/** Consecutive runs of hours on the same date: [{day: '2026-11-16T…', from: 0, count: 5}, …] */
function days(hours: Json[]) {
  const out: { day: string; from: number; count: number }[] = [];
  hours.forEach((h, i) => {
    const last = out[out.length - 1];
    if (last && last.day.slice(0, 10) === h.time.slice(0, 10)) last.count += 1;
    else out.push({ day: h.time, from: i, count: 1 });
  });
  return out;
}

function Weather({ w }: { w: Json }) {
  if (w.status !== "ok") {
    return <div className="card"><b>{t("하루 날씨", "Weather today")}</b><p className="note" style={{ margin: "6px 0 0" }}>{w.message}</p></div>;
  }
  const hours: Json[] = w.hours;
  const first = hours[0]?.time, last = hours[hours.length - 1]?.time;
  const span = first ? (first.slice(0, 10) === last?.slice(0, 10) ? dayLabel(first, true) : `${dayLabel(first, true)} ~ ${dayLabel(last, true)}`) : "";
  return (
    <div className="card">
      <div className="row"><b className="grow">{t("하루 날씨", "Weather today")}</b>{span && <span className="note" style={{ margin: 0 }}>{span}</span>}</div>
      <p className="lead">{w.headline}</p>
      <div className="hours" role="list">
        {/* one bar per date, as wide as that date's hours */}
        {days(hours).map((d) => (
          <div key={d.day} className="daybar" aria-hidden="true" style={{ gridRow: 1, gridColumn: `${d.from + 1} / span ${d.count}` }}>
            <span>{dayLabel(d.day)}</span>
          </div>
        ))}
        {hours.map((h: Json, i: number) => (
          <div key={h.time} role="listitem" aria-label={`${dayLabel(h.time, true)} ${h.time.slice(11, 16)}`}
               className={`hour ${Number(h.precip_prob_pct) >= 60 ? "wet" : ""}`} style={{ gridRow: 2, gridColumn: i + 1 }}>
            <span>{h.time.slice(11, 16)}</span><b>{h.temp_c}°</b><span>{h.sky}</span><span>{t("비", "Rain")} {h.precip_prob_pct}%</span><span>{h.wind_ms}m/s</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Notices({ n }: { n: Json }) {
  if (n.status !== "ok") {
    return <div className="card"><b>{t("공지", "Notices")}</b><p className="note" style={{ margin: "6px 0 0" }}>{n.message}</p></div>;
  }
  return (
    <div className="card">
      <b>{t("공지", "Notices")}</b>
      {n.notices.length === 0 && <p className="note" style={{ margin: "6px 0 0" }}>{t("확인된 우회·통제 공지가 없어요.", "No known detours or closures.")}</p>}
      {n.notices.map((x: Json) => (
        <div key={x.id} className="place">
          <span className="grow"><b>{x.title}</b>{x.body && <i>{String(x.body).slice(0, 80)}</i>}</span>
        </div>
      ))}
      {n.official_url && <a href={n.official_url} target="_blank" rel="noopener" style={{ fontSize: 12 }}>{t("공식 공지 확인", "Check official notices")}</a>}
    </div>
  );
}

const ALERT_ICON: Record<string, string> = {
  pin: '<path d="M12 21s7-6 7-11a7 7 0 0 0-14 0c0 5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  warn: '<path d="M12 4 3 20h18z"/><path d="M12 10v4M12 17v.5"/>',
  info: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8v.5"/>',
};
const alertIconFor = (t: string) => (t === "waypoint_arrival" || t === "endpoint_near" ? "pin"
  : ["schedule_delay", "end_time_risk", "rest_reminder"].includes(t) ? "clock" : ["route_notice", "off_route"].includes(t) ? "warn" : "info");

function ago(iso: string) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? t("방금", "just now") : m < 60 ? t(`${m}분 전`, `${m} min ago`)
    : m < 1440 ? t(`${Math.round(m / 60)}시간 전`, `${Math.round(m / 60)} h ago`) : t(`${Math.round(m / 1440)}일 전`, `${Math.round(m / 1440)} d ago`);
}

/** Keep only the newest alert of each kind (event_type). Older ones leave the screen; the server keeps them. */
function latestPerType(items: Json[]): Json[] {
  const newest = new Map<string, Json>();
  for (const n of items) {
    const cur = newest.get(n.event_type);
    if (!cur || new Date(n.created_at).getTime() > new Date(cur.created_at).getTime()) newest.set(n.event_type, n);
  }
  return [...newest.values()].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

function Alerts({ items: all }: { items: Json[] }) {
  const items = latestPerType(all);
  return (
    <>
      <h2 className="sec">{t("최근 알림", "Recent alerts")}</h2>
      {items.length === 0 && <p className="note">{t("아직 알림이 없어요. 걷기를 시작하면 일정·공지 알림이 여기에 모여요.", "No alerts yet. Once you start walking, schedule and notice alerts gather here.")}</p>}
      {items.map((n) => (
        <div key={n.id} className="card alert">
          <div className={`ic ${n.severity === "safety" ? "safety" : ""}`}><svg viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: ALERT_ICON[alertIconFor(n.event_type)] }} /></div>
          <div className="grow">
            <b>{n.title}</b>
            <p>{n.message}</p>
            <span className={`badge ${n.severity === "safety" ? "safety" : "gray"}`}>{ago(n.created_at)}{n.severity === "safety" ? t(" · 안전", " · safety") : ""}</span>
          </div>
        </div>
      ))}
    </>
  );
}

export default function Brief() {
  const { trip, session, course, pos, locOn, setLocOn, go, notifications } = useApp();
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
    <section aria-label={t("앞길브리핑", "Trail brief")}>
      <PageHead
        title={t("앞길 브리핑", "Trail brief")}
        sub={<>
          {data?.route ? `${data.route.name} · ${data.mode === "walking" ? t("걷는 중", "walking") : t("출발 전", "before you start")}`
            : t("지금 걸어도 되는지, 앞에 뭐가 있는지 한눈에", "Is now a good time to walk, and what's ahead — at a glance")}
          {at && t(` · ${at} 기준`, ` · as of ${at}`)}
        </>}
      />

      {err && <p className="err">{err}</p>}
      {data && ageMin >= 10 && (
        <p className="note"><span className="badge warn">{t(`${ageMin}분 전 정보`, `${ageMin} min old`)}</span> {t("연결이 끊겼거나 새로 확인하지 못했어요. 시각이 지났으니 도착·일몰 여유는 다시 확인해 주세요.",
          "The connection dropped or this couldn't be refreshed. Time has passed, so re-check your arrival and sunset margin.")}</p>
      )}
      {data && data.mode === "none" && (
        <div className="card">
          <b>{t("코스를 먼저 골라 주세요", "Pick a course first")}</b>
          <p className="note" style={{ margin: "6px 0 10px" }}>{t("코스를 고르면 일몰 여유와 앞길 정보를 보여 드려요.", "Once you pick a course, you'll see the sunset margin and what's ahead.")}</p>
          <button className="btn sm" onClick={() => go("explore")}>{t("코스탐색 열기", "Open Courses")}</button>
        </div>
      )}
      {data && data.mode !== "none" && !hasPos && data.location?.source !== "session" && (
        <div className="card">
          <p className="note" style={{ margin: "0 0 8px" }}>
            {fromStart ? t("내 위치를 몰라 코스 시작점 기준으로 보여 드려요.", "Your location is unknown, so this is based on the course start.")
              : t("내 위치를 켜면 지금 있는 곳 기준으로 보여 드려요.", "Turn on your location to see this for where you are now.")}
          </p>
          {!locOn && <button className="btn sm" onClick={() => setLocOn(true)}>{t("내 위치 켜기", "Show my location")}</button>}
        </div>
      )}
      {data && data.mode !== "none" && (
        <>
          <Daylight d={data.daylight} />
          <Weather w={data.weather} />
          <Supplies s={data.supplies} />
          <Notices n={data.notices} />
        </>
      )}
      <Alerts items={notifications} />
      {!data && !err && <p className="note">{t("불러오는 중…", "Loading…")}</p>}

      <button className="btn ghost" onClick={load} disabled={busy} style={{ marginTop: 6 }}>{busy ? t("확인하는 중…", "Checking…") : t("다시 확인", "Check again")}</button>
    </section>
  );
}
