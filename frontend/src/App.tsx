import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, auth, type Json } from "./api";
import Onboarding from "./screens/Onboarding";
import Home from "./screens/Home";
import Plan from "./screens/Plan";
import Walk from "./screens/Walk";
import Companion from "./screens/Companion";
import Journal from "./screens/Journal";
import Book from "./screens/Book";
import Settings from "./screens/Settings";

export type Tab = "home" | "plan" | "walk" | "ai" | "journal" | "book" | "settings";

type Ctx = {
  me: Json;
  refreshMe: () => Promise<void>;
  trips: Json[];
  trip: Json | null;
  setTripId: (id: string) => void;
  refreshTrips: () => Promise<void>;
  session: Json | null;
  setSession: (s: Json | null) => void;
  go: (t: Tab) => void;
  pushNotifications: (n: Json[]) => void;
};

const AppCtx = createContext<Ctx | null>(null);
export const useApp = () => {
  const c = useContext(AppCtx);
  if (!c) throw new Error("no app context");
  return c;
};

const TRIP_KEY = "jtm.tripId";

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

const TABS: { id: Tab; label: string; icon: string }[] = [
  { id: "home", label: "홈", icon: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z" },
  { id: "plan", label: "일정", icon: "M4 5h16v15H4zM4 9h16M9 3v4M15 3v4" },
  { id: "walk", label: "걷기", icon: "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z" },
  { id: "ai", label: "동행자", icon: "M4 5h16v11H8l-4 4zM8 10h8M8 13h5" },
  { id: "journal", label: "일기", icon: "M6 3h11a2 2 0 0 1 2 2v16l-4-2-4 2-4-2-3 2V5a2 2 0 0 1 2-2zM9 8h7M9 12h7" },
  { id: "book", label: "책", icon: "M4 4h7a3 3 0 0 1 3 3v13a2 2 0 0 0-2-2H4zM20 4h-5a3 3 0 0 0-3 3" },
];

function readTripId() {
  try { return localStorage.getItem(TRIP_KEY); } catch { return null; }
}

export default function App() {
  const [me, setMe] = useState<Json | null>(null);
  const [loading, setLoading] = useState(Boolean(auth.token));
  const [tab, setTab] = useState<Tab>("home");
  const [trips, setTrips] = useState<Json[]>([]);
  const [tripId, setTripIdState] = useState<string | null>(readTripId());
  const [session, setSession] = useState<Json | null>(null);
  const [toasts, setToasts] = useState<Json[]>([]);
  const lastPoll = useRef<string | null>(null);

  const refreshMe = useCallback(async () => setMe(await api("/v1/me")), []);
  const refreshTrips = useCallback(async () => setTrips((await api("/v1/trips")).trips), []);

  useEffect(() => {
    if (!auth.token) return;
    Promise.all([refreshMe(), refreshTrips(), api("/v1/sessions/current").then((r) => setSession(r.session))])
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [refreshMe, refreshTrips]);

  const pushNotifications = useCallback((items: Json[]) => {
    if (!items?.length) return;
    setToasts((t) => [...items.filter((n) => !t.some((x) => x.id === n.id)), ...t].slice(0, 3));
    if (document.hidden && "Notification" in window && Notification.permission === "granted") {
      for (const n of items) new Notification(n.title, { body: n.message, tag: n.id });
    }
  }, []);

  // Poll for notifications (Web Push/FCM is the production path; see docs).
  useEffect(() => {
    if (!me) return;
    const tick = async () => {
      try {
        const q = lastPoll.current ? `?since=${encodeURIComponent(lastPoll.current)}` : "";
        const r = await api(`/v1/notifications${q}`);
        const fresh = r.notifications.filter((n: Json) => !n.action_taken);
        if (lastPoll.current) pushNotifications(fresh);
        lastPoll.current = new Date().toISOString();
      } catch { /* offline */ }
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => clearInterval(id);
  }, [me, pushNotifications]);

  const trip = useMemo(() => trips.find((t) => t.id === tripId) ?? trips[0] ?? null, [trips, tripId]);
  const setTripId = (id: string) => {
    setTripIdState(id);
    try { localStorage.setItem(TRIP_KEY, id); } catch { /* ignore */ }
  };

  const act = async (n: Json, action: string) => {
    setToasts((t) => t.filter((x) => x.id !== n.id));
    try { await api(`/v1/notifications/${n.id}/action`, { body: { action } }); } catch { /* ignore */ }
    if (action === "open_map") setTab("walk");
    if (action === "replan") setTab("plan");
    if (action === "open_journal") setTab("journal");
    if (action === "open_notice" && n.source?.url) window.open(n.source.url, "_blank", "noopener");
  };

  if (loading) return <div className="app"><p className="muted" style={{ paddingTop: 40 }}>불러오는 중…</p></div>;
  if (!me) return <Onboarding onDone={async () => { await refreshMe(); await refreshTrips(); }} />;

  const ctx: Ctx = { me, refreshMe, trips, trip, setTripId, refreshTrips, session, setSession, go: setTab, pushNotifications };
  const Screen = { home: Home, plan: Plan, walk: Walk, ai: Companion, journal: Journal, book: Book, settings: Settings }[tab];

  return (
    <AppCtx.Provider value={ctx}>
      <div className="toast-stack" role="status" aria-live="polite">
        {toasts.map((n) => (
          <div key={n.id} className={`banner ${n.severity === "safety" ? "safety" : "warn"}`}>
            <div className="spread">
              <strong>{n.severity === "safety" ? "⚠ " : ""}{n.title}</strong>
              <button className="btn ghost small" style={{ minHeight: 32, padding: "2px 10px" }} onClick={() => act(n, "dismissed")} aria-label="알림 닫기">닫기</button>
            </div>
            <div style={{ whiteSpace: "pre-wrap" }}>{n.message}</div>
            <div className="row" style={{ marginTop: 8 }}>
              {(n.actions ?? []).map((a: Json) => (
                <button key={a.id} className="btn secondary" style={{ minHeight: 36, padding: "4px 12px" }} onClick={() => act(n, a.id)}>{a.label}</button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="app">
        <header className="topbar">
          <div className="brand">JEJU TRAIL MATE<small>{trip ? trip.title : "여행을 만들어 시작하세요"}</small></div>
          <button className="btn ghost" onClick={() => setTab("settings")} aria-label="설정">설정</button>
        </header>
        <Screen />
      </div>
      <nav className="tabs" aria-label="주요 화면">
        <div className="tabs-inner">
          {TABS.map((t) => (
            <button key={t.id} className={`tab ${tab === t.id ? "on" : ""}`} onClick={() => setTab(t.id)} aria-current={tab === t.id ? "page" : undefined}>
              <Icon d={t.icon} />
              {t.label}
              {t.id === "walk" && session && session.status !== "ended" ? <span className="sr-only">(진행 중)</span> : null}
            </button>
          ))}
        </div>
      </nav>
    </AppCtx.Provider>
  );
}
