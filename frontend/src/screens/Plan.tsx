import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";

export function NewTripForm({ onCreated }: { onCreated?: () => void }) {
  const { refreshTrips, setTripId } = useApp();
  const [title, setTitle] = useState("나의 제주 걷기");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [lodging, setLodging] = useState("");
  const [err, setErr] = useState("");
  const create = async () => {
    setErr("");
    try {
      const t = await api("/v1/trips", { body: { title, start_date: start, end_date: end, lodging_note: lodging || null } });
      await refreshTrips();
      setTripId(t.id);
      onCreated?.();
    } catch (e) { setErr((e as Error).message); }
  };
  return (
    <div className="card">
      <b style={{ fontSize: 15 }}>새 여행 만들기</b>
      <label className="field">여행 이름<input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="grid2">
        <label className="field">시작일<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field">마지막 날<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
      </div>
      <label className="field">숙박 위치 메모(선택)<input type="text" value={lodging} onChange={(e) => setLodging(e.target.value)} placeholder="예: 서귀포 시내" /></label>
      {err && <p className="err">{err}</p>}
      <button className="btn" onClick={create} disabled={!start || !end}>만들기</button>
    </div>
  );
}

function routesLabel(s: Json) {
  if (!s.route) return s.note ?? "휴식일";
  const names = [s.route, ...(s.extra_routes ?? [])].map((r: Json) => r.name).join(" + ");
  return names;
}

export default function Plan() {
  const { trip, trips, setTripId, refreshCourse } = useApp();
  const [routes, setRoutes] = useState<Json[]>([]);
  const [bundles, setBundles] = useState<Json[]>([]);
  const [plan, setPlan] = useState<Json[]>([]);
  const [proposals, setProposals] = useState<Json[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [type, setType] = useState<"jeju_olle" | "hayoung_olle">("jeju_olle");
  const [msg, setMsg] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    const [r, b] = await Promise.all([api("/v1/routes"), api("/v1/routes/hayoung/bundles")]);
    setRoutes(r.routes); setBundles(b.bundles);
    if (!trip) return;
    const [t, p] = await Promise.all([api(`/v1/trips/${trip.id}`), api(`/v1/trips/${trip.id}/proposals`)]);
    setPlan(t.plan); setProposals(p.proposals.filter((x: Json) => x.status === "pending"));
  }, [trip]);
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [load]);

  if (!trip || creating) return <NewTripForm onCreated={() => setCreating(false)} />;

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setMsg("");
    try { await fn(); await load(); await refreshCourse(); if (ok) setMsg(ok); } catch (e) { setMsg((e as Error).message); }
  };
  const generate = () => run(() => api(`/v1/trips/${trip.id}/plan`, { body: { route_ids: picked.length ? picked : null, trail_type: type } }),
    "초안을 만들었어요. 확인 후 승인해야 확정돼요.");
  const approveAll = () => run(() => api(`/v1/trips/${trip.id}/plan/approve`, { body: {} }), "일정을 확정했어요.");
  const decide = (id: string, approve: boolean) => run(() => api(`/v1/trips/${trip.id}/proposals/${id}/decision`, { body: { approve } }));
  const shown = routes.filter((r) => r.trail_type === type);
  const toggle = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  return (
    <div>
      {trips.length > 1 && (
        <select value={trip.id} onChange={(e) => setTripId(e.target.value)} aria-label="여행 선택" style={{ marginBottom: 10 }}>
          {trips.map((t) => <option key={t.id} value={t.id}>{t.title} ({t.start_date} ~ {t.end_date})</option>)}
        </select>
      )}
      {msg && <div className="card"><span className="note">{msg}</span></div>}
      {proposals.map((p) => (
        <div key={p.id} className="card">
          <b>AI 일정 변경 제안</b> <span className="badge warn">승인 필요</span>
          <p className="note">{p.summary}</p>
          <div className="row"><button className="btn sm" onClick={() => decide(p.id, true)}>승인</button><button className="btn sm ghost" onClick={() => decide(p.id, false)}>거절</button></div>
        </div>
      ))}

      <div className="card">
        <div className="row"><b className="grow" style={{ fontSize: 15 }}>{trip.title}</b>
          {plan.some((d) => d.pending_draft) && <button className="btn sm" onClick={approveAll}>초안 승인</button>}</div>
        {plan.length === 0 && <p className="note">아직 일정이 없어요. 아래에서 길을 고르고 초안을 만들어 보세요.</p>}
        {plan.map((d) => (
          <div key={d.date} className={`day ${d.pending_draft ? "pending" : d.approved ? "approved" : ""}`}>
            <b style={{ fontSize: 14 }}>{fmtDate(d.date)}</b>
            {d.approved && <div style={{ fontSize: 13 }}><span className="badge">확정</span> {routesLabel(d.approved)} {d.approved.planned_start && `· ${d.approved.planned_start}–${d.approved.planned_end ?? "?"}`}
              <div className="note" style={{ margin: 0 }}>{d.approved.route && d.approved.note}</div></div>}
            {d.pending_draft && <div style={{ fontSize: 13, marginTop: 4 }}><span className="badge yellow">승인 대기</span> {routesLabel(d.pending_draft)} {d.pending_draft.planned_start && `· ${d.pending_draft.planned_start}–${d.pending_draft.planned_end ?? "?"}`}
              <div className="note" style={{ margin: 0 }}>{d.pending_draft.route && d.pending_draft.note}</div></div>}
          </div>
        ))}
      </div>

      <h2 className="sec">길 고르기</h2>
      <div className="seg" role="group" aria-label="길 종류">
        <button type="button" aria-pressed={type === "jeju_olle"} onClick={() => { setType("jeju_olle"); setPicked([]); }}>제주올레</button>
        <button type="button" aria-pressed={type === "hayoung_olle"} onClick={() => { setType("hayoung_olle"); setPicked([]); }}>하영올레</button>
      </div>
      <p className="note">{type === "jeju_olle"
        ? "고르지 않으면 하루 거리·종료 시각·일몰을 보고, 전날 종점에서 시작하는 코스를 이어 붙여 추천해요."
        : "하영올레는 모두 서귀포시청 출발·도착이라, 시간이 맞으면 하루에 여러 코스를 묶어요."}</p>
      {type === "hayoung_olle" && (
        <div className="row wrap" style={{ marginBottom: 10 }}>
          {bundles.map((b) => <button key={b.label} className="chip" onClick={() => setPicked(b.route_ids)}>{b.label} · {b.courses.join("+")}</button>)}
        </div>
      )}
      <div className="card">
        {shown.map((r) => {
          const idx = picked.indexOf(r.id);
          return (
            <label key={r.id} className="toggle">
              <input type="checkbox" checked={idx >= 0} onChange={() => toggle(r.id)} />
              <span><b>{idx >= 0 ? `${idx + 1}. ` : ""}{r.name}</b>{r.subtitle && r.trail_type === "jeju_olle" ? ` · ${r.subtitle}` : ""}<br />
                <span className="note" style={{ margin: 0 }}>{r.distance_km != null ? `${r.distance_km}km` : "거리 계산 예정"}{r.duration_min_low ? ` · ${r.duration_min_low / 60}~${r.duration_min_high / 60}시간` : ""}</span></span>
            </label>
          );
        })}
      </div>
      <button className="btn" onClick={generate}>일정 초안 만들기</button>
      <button className="go" style={{ marginTop: 12 }} onClick={() => setCreating(true)}>+ 다른 여행 만들기</button>
    </div>
  );
}
