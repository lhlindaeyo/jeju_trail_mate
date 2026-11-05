import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { NewTripForm } from "./Home";

function DayRow({ s }: { s: Json }) {
  return (
    <div>
      {s.route ? (
        <>
          <strong>{s.route.name}</strong> <span className="muted small">{s.route.distance_km}km · {s.route.difficulty}</span>
          <div className="small">{s.planned_start}–{s.planned_end} · {s.note}</div>
        </>
      ) : <div>{s.note ?? "휴식일"}</div>}
    </div>
  );
}

export default function Plan() {
  const { trip } = useApp();
  const [routes, setRoutes] = useState<Json[]>([]);
  const [plan, setPlan] = useState<Json[]>([]);
  const [proposals, setProposals] = useState<Json[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [filter, setFilter] = useState("");
  const [msg, setMsg] = useState("");
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!trip) return;
    const [r, t, p] = await Promise.all([api("/v1/routes"), api(`/v1/trips/${trip.id}`), api(`/v1/trips/${trip.id}/proposals`)]);
    setRoutes(r.routes);
    setPlan(t.plan);
    setProposals(p.proposals.filter((x: Json) => x.status === "pending"));
  }, [trip]);
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [load]);

  if (!trip) return <NewTripForm />;

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setMsg("");
    try { await fn(); await load(); if (ok) setMsg(ok); } catch (e) { setMsg((e as Error).message); }
  };
  const generate = () => run(() => api(`/v1/trips/${trip.id}/plan`, { body: { route_ids: picked.length ? picked : null } }), "초안을 만들었어요. 확인 후 승인하세요.");
  const approveAll = () => run(() => api(`/v1/trips/${trip.id}/plan/approve`, { body: {} }), "일정을 확정했어요.");
  const change = (date: string, routeId: string | null) =>
    run(() => api(`/v1/trips/${trip.id}/plan/changes`, { body: [{ date, route_id: routeId }] }), "변경 초안을 만들었어요. 승인해야 반영돼요.").then(() => setEditing(null));
  const decide = (id: string, approve: boolean) => run(() => api(`/v1/trips/${trip.id}/proposals/${id}/decision`, { body: { approve } }));
  const shown = routes.filter((r) => !filter || r.tags.includes(filter) || r.difficulty === filter);
  const hasPending = plan.some((d) => d.pending_draft);

  return (
    <div>
      <h1>여행 일정</h1>
      {msg && <div className="banner">{msg}</div>}

      {proposals.map((p) => (
        <div key={p.id} className="banner warn">
          <strong>AI 일정 변경 제안</strong> — {p.summary}
          <ul>{p.payload.changes.map((c: Json) => <li key={c.date}>{fmtDate(c.date)}: {c.route_id ? routes.find((r) => r.id === c.route_id)?.name ?? "코스" : "휴식"}</li>)}</ul>
          <div className="row"><button className="btn" onClick={() => decide(p.id, true)}>승인</button><button className="btn ghost" onClick={() => decide(p.id, false)}>거절</button></div>
        </div>
      ))}

      <div className="card">
        <div className="spread"><h2>날짜별 일정</h2>{hasPending && <button className="btn" onClick={approveAll}>초안 승인</button>}</div>
        {plan.length === 0 && <p className="muted">아직 일정이 없어요. 아래에서 코스를 고르고 초안을 만들어 보세요.</p>}
        {plan.map((d) => (
          <div key={d.date} className={`day ${d.pending_draft ? "pending" : d.approved ? "approved" : ""}`}>
            <div className="spread"><strong>{fmtDate(d.date)}</strong>
              <button className="btn ghost small" style={{ minHeight: 32 }} onClick={() => setEditing(editing === d.date ? null : d.date)}>변경</button></div>
            {d.approved && <div><span className="tag">확정</span> <DayRow s={d.approved} /></div>}
            {d.pending_draft && <div style={{ marginTop: 6 }}><span className="tag warn">승인 대기 초안</span> <DayRow s={d.pending_draft} /></div>}
            {editing === d.date && (
              <select defaultValue="" onChange={(e) => e.target.value && change(d.date, e.target.value === "rest" ? null : e.target.value)} aria-label="코스 변경">
                <option value="">코스 선택…</option>
                <option value="rest">휴식일로</option>
                {routes.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.distance_km}km)</option>)}
              </select>
            )}
          </div>
        ))}
      </div>

      <div className="card">
        <h2>코스 고르기</h2>
        <p className="muted small">고른 순서대로 날짜에 배정됩니다. 고르지 않으면 관심사·하루 거리·종료 시각·일몰을 고려해 추천해요.</p>
        <div className="chips" style={{ marginBottom: 8 }}>
          {["", "바다", "오름", "숲", "카페", "문화", "하", "중"].map((f) => (
            <button key={f || "all"} className={`chip ${filter === f ? "on" : ""}`} onClick={() => setFilter(f)}>{f ? (f.length === 1 ? `난이도 ${f}` : f) : "전체"}</button>
          ))}
        </div>
        {shown.map((r) => {
          const idx = picked.indexOf(r.id);
          return (
            <label key={r.id} className="toggle">
              <input type="checkbox" checked={idx >= 0} onChange={() => setPicked(idx >= 0 ? picked.filter((x) => x !== r.id) : [...picked, r.id])} />
              <span>
                <strong>{idx >= 0 ? `${idx + 1}. ` : ""}{r.name}</strong> <span className="muted small">{r.distance_km}km · 난이도 {r.difficulty} · 약 {Math.round(r.estimated_duration_min / 6) / 10}h</span><br />
                <span className="small">{r.start_point} → {r.end_point}</span>{" "}
                {r.freshness.data_status !== "verified" && <span className="tag warn">최신 확인 필요</span>}
              </span>
            </label>
          );
        })}
        <button className="btn big" style={{ marginTop: 10 }} onClick={generate}>일정 초안 만들기</button>
        <p className="muted small">코스 정보 출처: <a href="https://www.jejuolle.org/trail#/" target="_blank" rel="noopener">제주올레 공식 사이트</a>. 최종 정보는 공식 사이트에서 확인하세요.</p>
      </div>
    </div>
  );
}
