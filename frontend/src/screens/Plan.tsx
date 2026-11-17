import { useCallback, useEffect, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { t } from "../i18n";

export function NewTripForm({ onCreated }: { onCreated?: () => void }) {
  const { refreshTrips, setTripId } = useApp();
  const [title, setTitle] = useState(() => t("나의 제주 걷기", "My Jeju walk"));
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
      <b style={{ fontSize: 15 }}>{t("새 여행 만들기", "New trip")}</b>
      <label className="field">{t("여행 이름", "Trip name")}<input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="grid2">
        <label className="field">{t("시작일", "First day")}<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
        <label className="field">{t("마지막 날", "Last day")}<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
      </div>
      <label className="field">{t("숙박 위치 메모(선택)", "Where you're staying (optional)")}<input type="text" value={lodging} onChange={(e) => setLodging(e.target.value)} placeholder={t("예: 서귀포 시내", "e.g. downtown Seogwipo")} /></label>
      {err && <p className="err">{err}</p>}
      <button className="btn" onClick={create} disabled={!start || !end}>{t("만들기", "Create")}</button>
    </div>
  );
}

type Block = { key: number; route_ids: string[]; text: string; start: string; rest: boolean };
let blockSeq = 0;

/** Splits the picked trails, in order, into one block per day that fits the daily distance target (+15% slack). */
function suggestBlocks(routes: Json[], ids: string[], dailyKm: number, start: string): Block[] {
  const byId = new Map(routes.map((r) => [r.id, r]));
  const out: Block[] = [];
  let cur: Json[] = [];
  const flush = () => {
    if (!cur.length) return;
    out.push({ key: ++blockSeq, route_ids: cur.map((r) => r.id), text: cur.map((r) => r.name).join(" + "), start, rest: false });
    cur = [];
  };
  for (const id of ids) {
    const r = byId.get(id);
    if (!r) continue;
    const sum = cur.reduce((a, x) => a + (x.distance_km ?? 0), 0) + (r.distance_km ?? 0);
    if (cur.length && (cur.length >= 4 || sum > dailyKm * 1.15)) flush();
    cur.push(r);
  }
  flush();
  return out;
}

function DayBlocks({ blocks, setBlocks, routes, tripStart, tripDays, onSave }: {
  blocks: Block[]; setBlocks: (b: Block[]) => void; routes: Json[]; tripStart: string; tripDays: number; onSave: () => void;
}) {
  const [drag, setDrag] = useState<number | null>(null);
  const byId = new Map(routes.map((r) => [r.id, r]));
  const move = (from: number, to: number) => {
    if (to < 0 || to >= blocks.length || from === to) return;
    const next = [...blocks];
    next.splice(to, 0, next.splice(from, 1)[0]);
    setBlocks(next);
  };
  const patch = (i: number, p: Partial<Block>) => setBlocks(blocks.map((b, j) => (j === i ? { ...b, ...p } : b)));
  const dateOf = (i: number) => { const d = new Date(`${tripStart}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + i); return d.toISOString().slice(0, 10); };
  const over = blocks.length > tripDays;
  return (
    <div className="card">
      <b style={{ fontSize: 15 }}>{t("하루치 블록", "Day blocks")}</b>
      <p className="note">{t("끌어서 순서를 바꾸거나 ◀ ▶ 로 옮기세요. 글자를 눌러 바로 고칠 수 있어요. 위에서부터 여행 첫날부터 차례로 배정돼요.",
        "Drag to reorder or move with ◀ ▶. Tap the text to edit it. Blocks are assigned from the first day of the trip, top to bottom.")}</p>
      {over && <p className="err">{t(`블록이 ${blocks.length}개인데 여행은 ${tripDays}일이에요. 블록을 줄이거나 여행 기간을 늘려 주세요.`,
        `There are ${blocks.length} blocks but the trip is ${tripDays} days. Remove blocks or make the trip longer.`)}</p>}
      <div className="blocks">
        {blocks.map((b, i) => {
          const km = b.route_ids.reduce((a, id) => a + (byId.get(id)?.distance_km ?? 0), 0);
          return (
            <div key={b.key} className={`block ${b.rest ? "rest" : ""} ${drag === i ? "dragging" : ""}`} draggable
              onDragStart={() => setDrag(i)} onDragEnd={() => setDrag(null)} onDragOver={(e) => e.preventDefault()}
              onDrop={() => { if (drag !== null) move(drag, i); setDrag(null); }}>
              <div className="row">
                <b className="grow">{t(`${i + 1}일차`, `Day ${i + 1}`)} <span className="muted small">{dateOf(i)}</span></b>
                <span className="badge">{b.rest ? t("휴식", "Rest") : `${km.toFixed(1)}km`}</span>
                <button type="button" className="chip" aria-label={t("앞으로", "Move earlier")} disabled={i === 0} onClick={() => move(i, i - 1)}>◀</button>
                <button type="button" className="chip" aria-label={t("뒤로", "Move later")} disabled={i === blocks.length - 1} onClick={() => move(i, i + 1)}>▶</button>
                <button type="button" className="chip" aria-label={t("블록 삭제", "Delete block")} onClick={() => setBlocks(blocks.filter((_, j) => j !== i))}>✕</button>
              </div>
              <input type="text" value={b.text} maxLength={500} aria-label={t(`${i + 1}일차 내용`, `Day ${i + 1} details`)} onChange={(e) => patch(i, { text: e.target.value })} />
              {!b.rest && <label className="muted small">{t("출발", "Start")} <input type="time" value={b.start} onChange={(e) => patch(i, { start: e.target.value })} /></label>}
            </div>
          );
        })}
      </div>
      <div className="row wrap" style={{ marginTop: 8 }}>
        <button type="button" className="chip" onClick={() => setBlocks([...blocks, { key: ++blockSeq, route_ids: [], text: t("휴식일", "Rest day"), start: "", rest: true }])}>{t("+ 휴식 블록", "+ Rest block")}</button>
        <button type="button" className="btn sm" disabled={over || !blocks.length} onClick={onSave}>{t("이 블록으로 초안 저장", "Save these blocks as a draft")}</button>
      </div>
    </div>
  );
}

function routesLabel(s: Json) {
  if (!s.route) return s.note ?? t("휴식일", "Rest day");
  const names = [s.route, ...(s.extra_routes ?? [])].map((r: Json) => r.name).join(" + ");
  return names;
}

export default function Plan() {
  const { trip, trips, setTripId, refreshTrips, refreshCourse, setSession } = useApp();
  const [routes, setRoutes] = useState<Json[]>([]);
  const [bundles, setBundles] = useState<Json[]>([]);
  const [plan, setPlan] = useState<Json[]>([]);
  const [proposals, setProposals] = useState<Json[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [type, setType] = useState<"jeju_olle" | "hayoung_olle">("jeju_olle");
  const [msg, setMsg] = useState("");
  const [creating, setCreating] = useState(false);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [confirmDel, setConfirmDel] = useState(false);

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
    t("초안을 만들었어요. 확인 후 '일정 등록'을 눌러 주세요.", "Draft created. Check it, then tap 'Register plan'."));
  const tripDays = Math.round((Date.parse(trip.end_date) - Date.parse(trip.start_date)) / 86400000) + 1;
  const suggest = async () => {
    setMsg("");
    try {
      const me = await api("/v1/me");
      setBlocks(suggestBlocks(routes, picked, Number(me.daily_distance_target_km) || 15, me.walk_start_time || "08:30"));
    } catch (e) { setMsg((e as Error).message); }
  };
  const saveBlocks = () => {
    const d0 = new Date(`${trip.start_date}T00:00:00Z`);
    const changes = blocks.map((b, i) => {
      const d = new Date(d0); d.setUTCDate(d.getUTCDate() + i);
      return { date: d.toISOString().slice(0, 10), route_id: b.rest ? null : b.route_ids[0] ?? null,
        extra_route_ids: b.rest ? [] : b.route_ids.slice(1), planned_start: b.rest || !b.start ? null : b.start, note: b.text || null };
    });
    return run(() => api(`/v1/trips/${trip.id}/plan/changes`, { body: changes }),
      t("블록을 초안으로 저장했어요. 확인 후 '일정 등록'을 눌러 주세요.", "Blocks saved as a draft. Check it, then tap 'Register plan'."));
  };
  const approveAll = () => run(() => api(`/v1/trips/${trip.id}/plan/approve`, { body: {} }), t("일정을 등록했어요.", "Plan registered."));
  const removeTrip = async () => {
    setMsg("");
    setConfirmDel(false);
    try {
      await api(`/v1/trips/${trip.id}`, { method: "DELETE" });
      setPlan([]); setProposals([]); setBlocks([]); setPicked([]);
      await refreshTrips();
      setSession((await api("/v1/sessions/current")).session);
      await refreshCourse();
    } catch (e) { setMsg((e as Error).message); }
  };
  const decide = (id: string, approve: boolean) => run(() => api(`/v1/trips/${trip.id}/proposals/${id}/decision`, { body: { approve } }));
  const shown = routes.filter((r) => r.trail_type === type);
  const toggle = (id: string) => setPicked(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id]);

  return (
    <div>
      {trips.length > 1 && (
        <select value={trip.id} onChange={(e) => { setConfirmDel(false); setTripId(e.target.value); }} aria-label={t("여행 선택", "Choose trip")} style={{ marginBottom: 10 }}>
          {trips.map((t) => <option key={t.id} value={t.id}>{t.title} ({t.start_date} ~ {t.end_date})</option>)}
        </select>
      )}
      {msg && <div className="card"><span className="note">{msg}</span></div>}
      {proposals.map((p) => (
        <div key={p.id} className="card">
          <b>{t("AI 일정 변경 제안", "AI plan change suggestion")}</b> <span className="badge warn">{t("승인 필요", "Needs approval")}</span>
          <p className="note">{p.summary}</p>
          <div className="row"><button className="btn sm" onClick={() => decide(p.id, true)}>{t("승인", "Approve")}</button><button className="btn sm ghost" onClick={() => decide(p.id, false)}>{t("거절", "Decline")}</button></div>
        </div>
      ))}

      <div className="card">
        <div className="row"><b className="grow" style={{ fontSize: 15 }}>{trip.title}</b>
          {plan.some((d) => d.pending_draft) && <button className="btn sm" onClick={approveAll}>{t("일정 등록", "Register plan")}</button>}
          <button className="btn sm ghost" onClick={() => setConfirmDel(true)} disabled={confirmDel}>{t("여행 삭제", "Delete trip")}</button></div>
        {confirmDel && (
          <div role="alertdialog" aria-label={t("여행 삭제 확인", "Confirm trip deletion")} style={{ margin: "8px 0" }}>
            <p className="err" style={{ margin: "0 0 6px" }}>{t(`"${trip.title}" 여행을 삭제할까요? 일정과 걷기 기록이 모두 지워지고 되돌릴 수 없어요.`,
              `Delete the trip "${trip.title}"? Its plan and walk records will all be erased, and this can't be undone.`)}</p>
            <div className="row"><button className="btn sm" onClick={removeTrip}>{t("삭제", "Delete")}</button><button className="btn sm ghost" onClick={() => setConfirmDel(false)}>{t("취소", "Cancel")}</button></div>
          </div>
        )}
        {plan.length === 0 && <p className="note">{t("아직 일정이 없어요. 아래에서 길을 고르고 초안을 만들어 보세요.", "No plan yet. Pick trails below and make a draft.")}</p>}
        {plan.map((d) => (
          <div key={d.date} className={`day ${d.pending_draft ? "pending" : d.approved ? "approved" : ""}`}>
            <b style={{ fontSize: 14 }}>{fmtDate(d.date)}</b>
            {d.approved && <div style={{ fontSize: 13 }}><span className="badge">{t("등록", "Registered")}</span> {routesLabel(d.approved)} {d.approved.planned_start && `· ${d.approved.planned_start}–${d.approved.planned_end ?? "?"}`}
              <div className="note" style={{ margin: 0 }}>{d.approved.route && d.approved.note}</div></div>}
            {d.pending_draft && <div style={{ fontSize: 13, marginTop: 4 }}><span className="badge yellow">{t("등록 대기", "Draft")}</span> {routesLabel(d.pending_draft)} {d.pending_draft.planned_start && `· ${d.pending_draft.planned_start}–${d.pending_draft.planned_end ?? "?"}`}
              <div className="note" style={{ margin: 0 }}>{d.pending_draft.route && d.pending_draft.note}</div></div>}
          </div>
        ))}
      </div>

      <h2 className="sec">{t("길 고르기", "Pick trails")}</h2>
      <div className="seg" role="group" aria-label={t("길 종류", "Trail type")}>
        <button type="button" aria-pressed={type === "jeju_olle"} onClick={() => { setType("jeju_olle"); setPicked([]); }}>{t("제주올레", "Jeju Olle")}</button>
        <button type="button" aria-pressed={type === "hayoung_olle"} onClick={() => { setType("hayoung_olle"); setPicked([]); }}>{t("하영올레", "Hayoung Olle")}</button>
      </div>
      <p className="note">{type === "jeju_olle"
        ? t("고르지 않으면 하루 거리·종료 시각·일몰을 보고, 전날 종점에서 시작하는 코스를 이어 붙여 추천해요.",
          "If you pick none, courses are suggested by daily distance, end time and sunset, each starting where the previous day ended.")
        : t("하영올레는 모두 서귀포시청 출발·도착이라, 시간이 맞으면 하루에 여러 코스를 묶어요.",
          "Hayoung Olle courses all start and finish at Seogwipo City Hall, so several can share a day when time allows.")}</p>
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
                <span className="note" style={{ margin: 0 }}>{r.distance_km != null ? `${r.distance_km}km` : t("거리 계산 예정", "distance not yet known")}{r.duration_min_low ? t(` · ${r.duration_min_low / 60}~${r.duration_min_high / 60}시간`, ` · ${r.duration_min_low / 60}–${r.duration_min_high / 60} h`) : ""}</span></span>
            </label>
          );
        })}
      </div>
      <button className="btn" onClick={generate}>{t("일정 초안 만들기", "Make a draft plan")}</button>
      <button className="go" style={{ marginTop: 8 }} disabled={!picked.length} onClick={suggest}>{t("고른 길을 하루치 블록으로 나누기 (직접 편집)", "Split picked trails into day blocks (edit yourself)")}</button>
      {blocks.length > 0 && <DayBlocks blocks={blocks} setBlocks={setBlocks} routes={routes} tripStart={trip.start_date} tripDays={tripDays} onSave={saveBlocks} />}
      <button className="go" style={{ marginTop: 12 }} onClick={() => setCreating(true)}>{t("+ 다른 여행 만들기", "+ Create another trip")}</button>
    </div>
  );
}
