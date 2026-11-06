import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, gpsQueue, isOnboarded, type Json } from "./api";
import Splash from "./screens/Splash";
import Onboarding from "./screens/Onboarding";
import Home from "./screens/Home";
import Explore from "./screens/Explore";
import Companion from "./screens/Companion";
import Brief from "./screens/Brief";
import { MenuSheet, Sheet, type SheetName } from "./screens/Sheets";

export type Page = "home" | "explore" | "ai" | "brief";
const PAGES: Page[] = ["home", "explore", "ai", "brief"];
const COURSE_KEY = "jtm.courseId";
const TRIP_KEY = "jtm.tripId";
const JEJU_BOUNDS = { minLat: 33.0, maxLat: 33.7, minLon: 126.0, maxLon: 127.0 };
const MIN_POINT_INTERVAL_MS = 15_000;
const FLUSH_INTERVAL_MS = 60_000;
const SPLASH_MIN_MS = 1800; // how long the splash stays at least, even when data is ready sooner
const SPLASH_FADE_MS = 900; // must match the splash exit animation in styles.css

export type Position = { lat: number; lon: number; acc: number; onIsland: boolean };

type Ctx = {
  me: Json; refreshMe: () => Promise<void>;
  trips: Json[]; trip: Json | null; setTripId: (id: string) => void; refreshTrips: () => Promise<void>;
  session: Json | null; setSession: (s: Json | null) => void;
  course: Json | null; setCourseId: (id: string | null) => void; refreshCourse: () => Promise<void>;
  go: (p: Page) => void; openSheet: (s: SheetName) => void;
  toast: (msg: string) => void; pushNotifications: (n: Json[]) => void; notifications: Json[];
  locOn: boolean; setLocOn: (on: boolean) => void; pos: Position | null; locMsg: string;
  flushGps: () => Promise<void>;
};
const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("no app context");
  return c;
};

function store(k: string, v?: string | null): string | null {
  try {
    if (v === undefined) return localStorage.getItem(k);
    if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v);
  } catch { /* storage unavailable */ }
  return null;
}
const pageFromHash = (): Page => (PAGES as string[]).includes(location.hash.slice(1)) ? (location.hash.slice(1) as Page) : "home";

const NAV_ICONS: Record<Page, ReactNode> = {
  home: <svg viewBox="0 0 24 24"><path d="M4 11.5 12 4l8 7.5V20H4z" /><path d="M10 20v-5h4v5" /></svg>,
  explore: <svg viewBox="0 0 24 24"><rect x="4" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5" /><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5" /><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5" /></svg>,
  ai: <svg viewBox="0 0 24 24"><path d="M5 5h14a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-7l-4 3.5V16H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z" /><path d="M12 8.2v4.6M9.7 10.5h4.6" /></svg>,
  brief: <svg viewBox="0 0 24 24"><path d="M4 17h16" /><path d="M7 17a5 5 0 0 1 10 0" /><path d="M12 6v2.5M5.6 9.6l1.7 1.7M18.4 9.6l-1.7 1.7" /><path d="M8 20.5h8" /></svg>,
};
const NAV_LABEL: Record<Page, string> = { home: "홈", explore: "코스탐색", ai: "AI동행자", brief: "앞길브리핑" };

export default function App() {
  const [me, setMe] = useState<Json | null>(null);
  const [loading, setLoading] = useState(true);
  const [onboarded, setOnboarded] = useState(isOnboarded);
  const [splash, setSplash] = useState<"show" | "leaving" | "gone">("show");
  const [splashMinDone, setSplashMinDone] = useState(false);
  const [page, setPage] = useState<Page>(pageFromHash);
  const [sheet, setSheet] = useState<SheetName | null>(null);
  const [trips, setTrips] = useState<Json[]>([]);
  const [tripId, setTripIdState] = useState<string | null>(store(TRIP_KEY));
  const [session, setSession] = useState<Json | null>(null);
  const [course, setCourse] = useState<Json | null>(null);
  const [courseId, setCourseIdState] = useState<string | null>(store(COURSE_KEY));
  const [toasts, setToasts] = useState<Json[]>([]);
  const [notifications, setNotifications] = useState<Json[]>([]);
  const [pill, setPill] = useState("");
  const [locOn, setLocOn] = useState(false);
  const [pos, setPos] = useState<Position | null>(null);
  const [locMsg, setLocMsg] = useState("");
  const lastPoll = useRef<string | null>(null);
  const lastPointAt = useRef(0);
  const pillTimer = useRef<number | undefined>(undefined);

  const refreshMe = useCallback(async () => setMe(await api("/v1/me")), []);
  const refreshTrips = useCallback(async () => setTrips((await api("/v1/trips")).trips), []);

  const load = useCallback(() => {
    return Promise.all([refreshMe(), refreshTrips(), api("/v1/sessions/current").then((r) => setSession(r.session))])
      .catch(() => undefined).finally(() => setLoading(false)); // on failure `me` stays null → retry screen
  }, [refreshMe, refreshTrips]);
  useEffect(() => { load(); }, [load]);

  // Splash: stay at least SPLASH_MIN_MS and until the first data load is done, then fade into the app.
  useEffect(() => {
    const t = window.setTimeout(() => setSplashMinDone(true), SPLASH_MIN_MS);
    return () => window.clearTimeout(t);
  }, []);
  useEffect(() => {
    if (splash === "show" && splashMinDone && !loading) setSplash("leaving");
    if (splash !== "leaving") return;
    const t = window.setTimeout(() => setSplash("gone"), SPLASH_FADE_MS);
    return () => window.clearTimeout(t);
  }, [splash, splashMinDone, loading]);

  // hash ⇄ page (back/forward and reload keep the selected tab)
  useEffect(() => {
    const onHash = () => setPage(pageFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = useCallback((p: Page) => {
    if (location.hash.slice(1) !== p) location.hash = p;
    setPage(p);
  }, []);

  const toast = useCallback((msg: string) => {
    setPill(msg);
    window.clearTimeout(pillTimer.current);
    pillTimer.current = window.setTimeout(() => setPill(""), 1800);
  }, []);

  const pushNotifications = useCallback((items: Json[]) => {
    if (!items?.length) return;
    setNotifications((all) => [...items.filter((n) => !all.some((x) => x.id === n.id)), ...all].slice(0, 50));
    setToasts((t) => [...items.filter((n) => !t.some((x) => x.id === n.id)), ...t].slice(0, 2));
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      for (const n of items) new Notification(n.title, { body: n.message, tag: n.id });
    }
  }, []);

  useEffect(() => {
    if (!me) return;
    const tick = async () => {
      try {
        const q = lastPoll.current ? `?since=${encodeURIComponent(lastPoll.current)}` : "";
        const r = await api(`/v1/notifications${q}`);
        if (lastPoll.current) pushNotifications(r.notifications.filter((n: Json) => !n.action_taken));
        else setNotifications(r.notifications);
        lastPoll.current = new Date().toISOString();
      } catch { /* offline */ }
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => clearInterval(id);
  }, [me, pushNotifications]);

  // ---- current course: session route → user pick → today's schedule ------------------
  const trip = useMemo(() => trips.find((t) => t.id === tripId) ?? trips[0] ?? null, [trips, tripId]);
  const effectiveCourseId = session && session.status !== "ended" && session.route_id ? session.route_id : courseId;
  const refreshCourse = useCallback(async () => {
    let id = effectiveCourseId;
    if (!id && trip) {
      try { id = (await api(`/v1/trips/${trip.id}/today`)).schedule?.route?.id ?? null; } catch { /* ignore */ }
    }
    if (!id) { setCourse(null); return; }
    try { setCourse(await api(`/v1/routes/${id}`)); } catch { setCourse(null); }
  }, [effectiveCourseId, trip]);
  useEffect(() => { if (me) refreshCourse(); }, [me, refreshCourse]);
  const setCourseId = (id: string | null) => { setCourseIdState(id); store(COURSE_KEY, id); };
  const setTripId = (id: string) => { setTripIdState(id); store(TRIP_KEY, id); };

  // ---- location: only after the user taps "내 위치 켜기"; GPS recording only during a session ----
  const flushGps = useCallback(async () => {
    try {
      const r = await gpsQueue.flush();
      if (r) { setSession(r.summary); pushNotifications(r.notifications); }
    } catch { /* keep queued, retry next tick */ }
  }, [pushNotifications]);

  const recording = Boolean(session && session.status === "active" && session.tracking_mode === "gps");
  useEffect(() => {
    if (!locOn) { setPos(null); setLocMsg(""); return; }
    if (!("geolocation" in navigator)) { setLocMsg("이 기기에서는 위치를 쓸 수 없어요"); setLocOn(false); return; }
    setLocMsg("위치 확인 중");
    const watch = navigator.geolocation.watchPosition((p) => {
      const { latitude: lat, longitude: lon, accuracy: acc } = p.coords;
      const onIsland = lat > JEJU_BOUNDS.minLat && lat < JEJU_BOUNDS.maxLat && lon > JEJU_BOUNDS.minLon && lon < JEJU_BOUNDS.maxLon;
      setPos({ lat, lon, acc, onIsland });
      setLocMsg(onIsland ? `실시간 · 오차 ${Math.round(acc)}m` : "제주 밖 · 예시 위치 표시");
      if (recording && onIsland && Date.now() - lastPointAt.current >= MIN_POINT_INTERVAL_MS) {
        lastPointAt.current = Date.now();
        gpsQueue.push(session!.session_id, { timestamp: new Date(p.timestamp).toISOString(), lat, lon, accuracy_m: acc });
      }
    }, (e) => {
      setLocOn(false);
      toast(e.code === e.PERMISSION_DENIED ? "위치 권한을 허용해 주세요" : "위치를 가져오지 못했어요");
    }, { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
    return () => navigator.geolocation.clearWatch(watch);
  }, [locOn, recording, session?.session_id, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!recording) return;
    const t = setInterval(flushGps, FLUSH_INTERVAL_MS);
    return () => clearInterval(t);
  }, [recording, flushGps]);

  const act = async (n: Json, action: Json | string) => {
    const id = typeof action === "string" ? action : action.id;
    setToasts((t) => t.filter((x) => x.id !== n.id));
    try { await api(`/v1/notifications/${n.id}/action`, { body: { action: id } }); } catch { /* ignore */ }
    if (id === "open_map") go("home");
    if (id === "replan") setSheet("plan");
    if (id === "open_brief") go("brief");
    if (id === "open_notice" && n.source?.url) window.open(n.source.url, "_blank", "noopener");
  };

  const ctx: Ctx | null = me && {
    me, refreshMe, trips, trip, setTripId, refreshTrips, session, setSession, course, setCourseId, refreshCourse,
    go, openSheet: setSheet, toast, pushNotifications, notifications, locOn, setLocOn, pos, locMsg, flushGps,
  };
  const Screen = { home: Home, explore: Explore, ai: Companion, brief: Brief }[page];

  const app = ctx && (
    <AppCtx.Provider value={ctx}>
      <div className="shell" data-page={page}>
        <div className={page === "home" ? "page" : "page scroll"}><Screen /></div>

        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((n) => (
            <div key={n.id} className={`toast-card ${n.severity === "safety" ? "safety" : ""}`}>
              <div className="row"><b className="grow">{n.title}</b>
                <button className="go" onClick={() => act(n, "dismissed")} aria-label="알림 닫기">닫기</button></div>
              <p>{n.message}</p>
              <div className="row wrap">{(n.actions ?? []).map((a: Json) => (
                <button key={a.id + (a.waypoint_id ?? "")} className="btn sm ghost" onClick={() => act(n, a)}>{a.label}</button>))}</div>
            </div>
          ))}
        </div>

        {page !== "home" && <div className="fadebot" aria-hidden="true" />}
        <nav className="nav" aria-label="주요 메뉴" style={{ ["--i" as string]: PAGES.indexOf(page) }}>
          <span className="dot" aria-hidden="true" />
          {PAGES.map((p) => (
            <button key={p} type="button" aria-label={NAV_LABEL[p]} title={NAV_LABEL[p]} aria-current={page === p ? "page" : undefined} onClick={() => go(p)}>
              {NAV_ICONS[p]}
              {p === "home" && recording && <span className="live" aria-label="기록 중" />}
            </button>
          ))}
        </nav>
        <div className={`pilltoast ${pill ? "show" : ""}`} role="status">{pill}</div>
        {sheet === "menu" && <MenuSheet onClose={() => setSheet(null)} />}
        {sheet && sheet !== "menu" && <Sheet name={sheet} onClose={() => setSheet(null)} />}
      </div>
    </AppCtx.Provider>
  );

  let body: ReactNode;
  if (loading) {
    body = <div className="shell" />;
  } else if (!me) {
    body = (
      <div className="shell scroll">
        <h1 className="title">서버에 연결할 수 없어요</h1>
        <p className="sub">백엔드가 켜져 있는지 확인한 뒤 다시 시도해 주세요.</p>
        <button className="btn big" onClick={() => { setLoading(true); load(); }}>다시 시도</button>
      </div>
    );
  } else if (!onboarded && trips.length === 0) {
    // first launch: short intro + walking preferences (no account step — the app is single-user)
    body = <Onboarding onDone={async () => { await refreshMe(); await refreshTrips(); setOnboarded(true); }} />;
  } else {
    body = app;
  }

  return (
    <>
      {body}
      {splash !== "gone" && <Splash leaving={splash === "leaving"} fadeMs={SPLASH_FADE_MS} />}
    </>
  );
}
