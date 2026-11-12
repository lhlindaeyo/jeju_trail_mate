// Demo location simulator ("가상 위치"): stands in for navigator.geolocation so the app can be tried away from
// Jeju — by the developer, or by judges opening the app with ?demo=1. It walks the selected course's path at
// walking speed (or fast-forwarded), can step off the route, and can be moved by tapping the map.
// Points it produces are sent with source="sim" so the server allows the fast-forward speed.
import { useSyncExternalStore } from "react";

export type LatLon = [number, number];
export const SIM_SPEEDS = [1, 5, 20] as const; // must stay ≤ backend SIM_SPEEDUP_MAX
const WALK_MS = 1.25; // ≈ 4.5 km/h
const TICK_MS = 500;
const ACCURACY_M = 8;
const OFF_ROUTE_M = 80; // sideways step for "길 벗어나기" (server flags > 50 m for 2 min)
const SNAP_M = 150; // a map tap this close to the path jumps along the path
const DEFAULT_SPOT: LatLon = [33.2533, 126.56]; // 서귀포시청 제1청사 (start of the 하영올레 loops)
const KEY = "jtm.sim";
const M_PER_DEG = 111_320;

export type SimSnapshot = {
  enabled: boolean; playing: boolean; speed: number; offRoute: boolean;
  hasPath: boolean; along: number; total: number; lat: number; lon: number; free: boolean;
};

type Saved = { enabled?: boolean; speed?: number; courseId?: string | null; along?: number };
function load(): Saved {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}
function save(v: Saved) {
  try { localStorage.setItem(KEY, JSON.stringify(v)); } catch { /* storage unavailable */ }
}

const dist = (a: LatLon, b: LatLon) => {
  const k = Math.cos(((a[0] + b[0]) / 2) * Math.PI / 180);
  return Math.hypot((b[0] - a[0]) * M_PER_DEG, (b[1] - a[1]) * M_PER_DEG * k);
};

class LocationSim {
  private path: LatLon[] = [];
  private cum: number[] = [0];
  private courseId: string | null | undefined = undefined;
  private free: LatLon | null = null;
  private along = 0;
  private playing = false;
  private offRoute = false;
  private speed: number;
  private enabled: boolean;
  private watchers = new Map<number, PositionCallback>();
  private nextId = 1;
  private timer: number | undefined;
  private lastTick = 0;
  private lastSave = 0;
  private saved: Saved;
  private listeners = new Set<() => void>();
  snap: SimSnapshot;

  constructor() {
    this.saved = load();
    const q = new URLSearchParams(location.search).get("demo");
    this.enabled = q === "1" ? true : q === "0" ? false : Boolean(this.saved.enabled);
    this.speed = (SIM_SPEEDS as readonly number[]).includes(this.saved.speed ?? 0) ? this.saved.speed! : 1;
    this.snap = this.makeSnap();
    this.persist();
  }

  // ---- geolocation-compatible API (only the parts the app uses) ----
  watchPosition(success: PositionCallback): number {
    const id = this.nextId++;
    this.watchers.set(id, success);
    if (this.timer === undefined) {
      this.lastTick = performance.now();
      this.timer = window.setInterval(this.tick, TICK_MS);
    }
    window.setTimeout(() => this.watchers.get(id)?.(this.position()), 0);
    return id;
  }
  clearWatch(id: number) {
    this.watchers.delete(id);
    // the app re-subscribes when a walk starts or ends, so keep `playing` — the walk just holds until watched again
    if (!this.watchers.size && this.timer !== undefined) { window.clearInterval(this.timer); this.timer = undefined; }
  }

  // ---- controls ----
  setEnabled(on: boolean) { this.enabled = on; if (!on) this.playing = false; this.changed(true); }
  setCourse(id: string | null, path: LatLon[] | null | undefined, start: LatLon | null | undefined) {
    if (id === this.courseId) return;
    this.courseId = id;
    this.path = path && path.length > 1 ? path : [];
    this.cum = [0];
    for (let i = 1; i < this.path.length; i++) this.cum.push(this.cum[i - 1] + dist(this.path[i - 1], this.path[i]));
    const resume = this.saved.courseId === id && this.saved.along != null; // keep the spot across reloads
    this.along = resume ? Math.min(this.saved.along!, this.total) : 0;
    this.free = this.path.length ? null : start ?? null;
    this.playing = false;
    this.offRoute = false;
    this.changed(true);
  }
  play() { if (this.path.length) { this.free = null; if (this.along >= this.total) this.along = 0; this.playing = true; this.changed(true); } }
  pause() { this.playing = false; this.changed(true); }
  setSpeed(s: number) { this.speed = s; this.changed(true); }
  seek(m: number) { this.free = null; this.along = Math.max(0, Math.min(m, this.total)); this.changed(true); this.emit(); }
  toggleOffRoute() { this.offRoute = !this.offRoute; this.changed(); this.emit(); }
  /** Map tap: near the path → jump along it; elsewhere → stand there. */
  teleport(lat: number, lon: number) {
    const near = this.project([lat, lon]);
    if (near && near.d <= SNAP_M) { this.free = null; this.along = near.along; }
    else { this.free = [lat, lon]; this.playing = false; }
    this.offRoute = false;
    this.changed(true);
    this.emit();
  }

  subscribe = (fn: () => void) => { this.listeners.add(fn); return () => { this.listeners.delete(fn); }; };

  // ---- internals ----
  private get total() { return this.cum[this.cum.length - 1]; }

  private tick = () => {
    const now = performance.now();
    const dt = (now - this.lastTick) / 1000;
    this.lastTick = now;
    if (this.playing && !this.free) {
      this.along = Math.min(this.total, this.along + WALK_MS * this.speed * dt);
      if (this.along >= this.total) this.playing = false;
      this.changed(now - this.lastSave > 5000);
    }
    if (this.playing || now - this.lastEmit > 3000) this.emit(); // standing still: a fix every few seconds, like GPS
  };

  private lastEmit = 0;
  private emit() {
    this.lastEmit = performance.now();
    if (!this.watchers.size) return;
    const p = this.position();
    for (const cb of this.watchers.values()) cb(p);
  }

  private point(): LatLon {
    if (this.free) return this.free;
    const path = this.path;
    if (!path.length) return DEFAULT_SPOT;
    let i = 1;
    while (i < path.length - 1 && this.cum[i] < this.along) i++;
    const a = path[i - 1], b = path[i];
    const seg = this.cum[i] - this.cum[i - 1];
    const t = seg > 0 ? Math.min(1, Math.max(0, (this.along - this.cum[i - 1]) / seg)) : 0;
    const lat = a[0] + (b[0] - a[0]) * t, lon = a[1] + (b[1] - a[1]) * t;
    if (!this.offRoute || seg <= 0) return [lat, lon];
    // step OFF_ROUTE_M to the left of the walking direction
    const k = Math.cos(lat * Math.PI / 180);
    const n = (b[0] - a[0]) * M_PER_DEG, e = (b[1] - a[1]) * M_PER_DEG * k, len = Math.hypot(n, e);
    return [lat + (e / len) * OFF_ROUTE_M / M_PER_DEG, lon - (n / len) * OFF_ROUTE_M / (M_PER_DEG * k)];
  }

  private project(p: LatLon): { d: number; along: number } | null {
    let best: { d: number; along: number } | null = null;
    const k = Math.cos(p[0] * Math.PI / 180);
    for (let i = 1; i < this.path.length; i++) {
      const a = this.path[i - 1], b = this.path[i];
      const bx = (b[1] - a[1]) * k, by = b[0] - a[0], px = (p[1] - a[1]) * k, py = p[0] - a[0];
      const l2 = bx * bx + by * by;
      const t = l2 > 0 ? Math.max(0, Math.min(1, (px * bx + py * by) / l2)) : 0;
      const d = Math.hypot(px - t * bx, py - t * by) * M_PER_DEG;
      if (!best || d < best.d) best = { d, along: this.cum[i - 1] + t * (this.cum[i] - this.cum[i - 1]) };
    }
    return best;
  }

  private position(): GeolocationPosition {
    const [latitude, longitude] = this.point();
    const coords = { latitude, longitude, accuracy: ACCURACY_M, altitude: null, altitudeAccuracy: null, heading: null, speed: null };
    const timestamp = Date.now();
    return { coords: { ...coords, toJSON: () => coords }, timestamp, toJSON: () => ({ coords, timestamp }) } as GeolocationPosition;
  }

  private makeSnap(): SimSnapshot {
    const [lat, lon] = this.point();
    return { enabled: this.enabled, playing: this.playing, speed: this.speed, offRoute: this.offRoute,
      hasPath: this.path.length > 1, along: this.along, total: this.total, lat, lon, free: Boolean(this.free) };
  }

  private persist() {
    this.lastSave = performance.now();
    const spot = this.courseId ? { courseId: this.courseId, along: Math.round(this.along) } : { courseId: this.saved.courseId, along: this.saved.along };
    this.saved = { enabled: this.enabled, speed: this.speed, ...spot };
    save(this.saved);
  }

  private changed(persist = false) {
    this.snap = this.makeSnap();
    if (persist) this.persist();
    for (const fn of this.listeners) fn();
  }
}

export const sim = new LocationSim();

export function useSim<T>(select: (s: SimSnapshot) => T): T {
  return useSyncExternalStore(sim.subscribe, () => select(sim.snap));
}
