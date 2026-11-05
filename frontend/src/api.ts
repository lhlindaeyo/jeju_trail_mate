/* eslint-disable @typescript-eslint/no-explicit-any */
export type Json = Record<string, any>;

const TOKEN_KEY = "jtm.token";
const QUEUE_KEY = "jtm.gpsQueue";

function safeGet(k: string): string | null {
  try { return localStorage.getItem(k); } catch { return null; }
}
function safeSet(k: string, v: string | null) {
  try { v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); } catch { /* storage unavailable */ }
}

export const auth = {
  get token() { return safeGet(TOKEN_KEY); },
  set(t: string | null) { safeSet(TOKEN_KEY, t); },
  async logout() {
    safeSet(TOKEN_KEY, null);
    safeSet(QUEUE_KEY, null);
    try { await caches.delete("jtm-api-v1"); } catch { /* no cache api */ }
  },
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T = Json>(path: string, opts: { method?: string; body?: unknown; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (auth.token) headers.Authorization = `Bearer ${auth.token}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form;
  else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  let res: Response;
  try {
    res = await fetch(path, { method: opts.method ?? (body ? "POST" : "GET"), headers, body });
  } catch {
    throw new ApiError(0, "네트워크에 연결할 수 없습니다. 저장된 정보만 볼 수 있어요.");
  }
  if (res.status === 401) { await auth.logout(); location.reload(); }
  if (res.status === 204) return undefined as T;
  const ctype = res.headers.get("content-type") ?? "";
  const data = ctype.includes("json") ? await res.json() : await res.text();
  if (!res.ok) {
    const detail = typeof data === "object" ? data.detail : data;
    const msg = Array.isArray(detail) ? detail.map((d: Json) => d.msg).join(", ") : String(detail ?? res.statusText);
    throw new ApiError(res.status, msg);
  }
  return data as T;
}

// ---- GPS batching with an offline queue -------------------------------------------
export type GpsPoint = { timestamp: string; lat: number; lon: number; accuracy_m: number | null };

export const gpsQueue = {
  load(): { sessionId: string; points: GpsPoint[] } | null {
    try { return JSON.parse(safeGet(QUEUE_KEY) || "null"); } catch { return null; }
  },
  push(sessionId: string, p: GpsPoint) {
    const q = this.load();
    const points = q && q.sessionId === sessionId ? q.points : [];
    points.push(p);
    safeSet(QUEUE_KEY, JSON.stringify({ sessionId, points: points.slice(-2000) }));
  },
  async flush(): Promise<Json | null> {
    const q = this.load();
    if (!q || q.points.length === 0) return null;
    const batch = q.points.slice(0, 500);
    const res = await api(`/v1/sessions/${q.sessionId}/points`, { body: { points: batch } });
    const rest = (this.load()?.points ?? []).slice(batch.length);
    safeSet(QUEUE_KEY, JSON.stringify({ sessionId: q.sessionId, points: rest }));
    return res;
  },
  clear() { safeSet(QUEUE_KEY, null); },
};

export const fmtDate = (iso: string) => {
  const d = new Date(iso + "T00:00:00");
  return `${d.getMonth() + 1}월 ${d.getDate()}일 (${"일월화수목금토"[d.getDay()]})`;
};

export const todayKst = () => new Date(Date.now() + 9 * 3600_000).toISOString().slice(0, 10);
