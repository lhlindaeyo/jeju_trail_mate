import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtDate, todayKst, type Json } from "../api";
import { useApp } from "../App";
import { listen, voiceSupported } from "../voice";
import { NewTripForm } from "./Home";

function tripDates(t: Json): string[] {
  const out: string[] = [];
  const d = new Date(t.start_date + "T00:00:00Z");
  const end = new Date(t.end_date + "T00:00:00Z");
  while (d <= end) { out.push(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() + 1); }
  return out;
}

export default function Journal() {
  const { trip, me, refreshMe } = useApp();
  const dates = trip ? tripDates(trip) : [];
  const [day, setDay] = useState(() => (dates.includes(todayKst()) ? todayKst() : dates[0] ?? todayKst()));
  const [entries, setEntries] = useState<Json[]>([]);
  const [memo, setMemo] = useState("");
  const [fromVoice, setFromVoice] = useState(false);
  const [highlight, setHighlight] = useState(false);
  const [listening, setListening] = useState(false);
  const [draftText, setDraftText] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const stopRef = useRef<() => void>(() => undefined);
  const fileRef = useRef<HTMLInputElement>(null);
  const [caption, setCaption] = useState("");

  const load = useCallback(async () => {
    if (!trip) return;
    const r = await api(`/v1/trips/${trip.id}/journals?day=${day}`);
    setEntries(r.entries);
    const daily = r.entries.find((e: Json) => e.kind === "daily");
    setDraftText(daily ? daily.user_edited_text ?? daily.ai_draft : null);
  }, [trip, day]);
  useEffect(() => { load().catch((e) => setMsg(e.message)); }, [load]);

  if (!trip) return <NewTripForm />;
  const daily = entries.find((e) => e.kind === "daily");
  const memos = entries.filter((e) => e.kind === "memo");

  const withBusy = async (fn: () => Promise<unknown>) => {
    setBusy(true); setMsg("");
    try { await fn(); await load(); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };

  const ensureConsent = async (key: "microphone" | "photos", label: string) => {
    if (me.consents[key]) return true;
    if (!confirm(`${label}을(를) 저장하려면 동의가 필요해요. 동의할까요? (설정에서 언제든 철회 가능)`)) return false;
    await api("/v1/me/preferences", { method: "PATCH", body: { [`consent_${key}`]: true } });
    await refreshMe();
    return true;
  };

  const getLocation = () => new Promise<{ lat: number; lon: number } | null>((resolve) => {
    if (!me.consents.location || !("geolocation" in navigator)) return resolve(null);
    navigator.geolocation.getCurrentPosition((p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude }), () => resolve(null), { timeout: 8000, maximumAge: 60_000 });
  });

  const addMemo = () => withBusy(async () => {
    const loc = day === todayKst() ? await getLocation() : null;
    await api("/v1/journals", { body: { trip_id: trip.id, date: day, text: memo, source: fromVoice ? "voice" : "text", is_highlight: highlight, ...(loc ?? {}) } });
    setMemo(""); setFromVoice(false); setHighlight(false);
  });

  const voice = async () => {
    if (listening) { stopRef.current(); return; }
    if (!(await ensureConsent("microphone", "음성 기록"))) return;
    setListening(true);
    stopRef.current = listen((t) => { setMemo(t); setFromVoice(true); }, (err) => { setListening(false); if (err) setMsg(err); });
  };

  const uploadPhoto = async (file: File) => {
    if (!(await ensureConsent("photos", "사진"))) return;
    await withBusy(async () => {
      const entry = await api("/v1/journals", { body: { trip_id: trip.id, date: day, text: caption || null } });
      const fd = new FormData();
      fd.append("file", file);
      if (caption) fd.append("caption", caption);
      await api(`/v1/journals/${entry.id}/media`, { form: fd });
      setCaption("");
    });
  };

  const makeDraft = () => withBusy(async () => {
    const r = await api(`/v1/trips/${trip.id}/journals/daily-draft`, { body: { date: day } });
    setDraftText(r.ai_draft);
  });
  const saveDaily = () => withBusy(() => api(`/v1/journals/${daily!.id}`, { method: "PUT", body: { text: draftText } }));
  const remove = (id: string) => confirm("이 기록을 삭제할까요?") && withBusy(() => api(`/v1/journals/${id}`, { method: "DELETE" }));
  const toggleHl = (e: Json) => withBusy(() => api(`/v1/journals/${e.id}`, { method: "PUT", body: { text: e.user_edited_text ?? e.text ?? "(사진)", is_highlight: !e.is_highlight } }));

  return (
    <div>
      <h1>여행 일기</h1>
      <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="날짜">
        {dates.map((d) => <option key={d} value={d}>{fmtDate(d)}</option>)}
      </select>
      {msg && <div className="banner warn">{msg}</div>}

      <div className="card">
        <h2>순간 기록</h2>
        <textarea value={memo} onChange={(e) => { setMemo(e.target.value); }} placeholder="예: 오늘 바다가 정말 예뻤어" aria-label="메모" style={{ minHeight: 80 }} />
        <label className="toggle"><input type="checkbox" checked={highlight} onChange={(e) => setHighlight(e.target.checked)} /><span>기억에 남는 순간으로 표시</span></label>
        <div className="row">
          {voiceSupported() && <button className={`btn ${listening ? "danger" : "secondary"}`} onClick={voice}>{listening ? "■ 멈춤" : "🎤 말로 기록"}</button>}
          <button className="btn" onClick={addMemo} disabled={busy || !memo.trim()}>저장</button>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <input type="text" value={caption} onChange={(e) => setCaption(e.target.value)} placeholder="사진 설명(선택) — 책과 일기에 쓰여요" style={{ flex: 1 }} />
          <button className="btn secondary" onClick={() => fileRef.current?.click()} disabled={busy}>📷 사진</button>
          <input ref={fileRef} type="file" accept="image/*" capture="environment" hidden onChange={(e) => e.target.files?.[0] && uploadPhoto(e.target.files[0])} />
        </div>
      </div>

      <div className="card">
        <h2>{fmtDate(day)}의 기록 ({memos.length})</h2>
        {memos.length === 0 && <p className="muted">아직 기록이 없어요.</p>}
        {memos.map((e) => (
          <div key={e.id} className="day approved">
            <div className="spread">
              <span className="muted small">{e.captured_at?.slice(11, 16)} {e.is_highlight && "★"}</span>
              <span className="row">
                <button className="btn ghost small" style={{ minHeight: 32 }} onClick={() => toggleHl(e)}>{e.is_highlight ? "★ 해제" : "☆ 하이라이트"}</button>
                <button className="btn ghost small" style={{ minHeight: 32 }} onClick={() => remove(e.id)}>삭제</button>
              </span>
            </div>
            {(e.user_edited_text ?? e.text) && <div style={{ whiteSpace: "pre-wrap" }}>{e.user_edited_text ?? e.text}</div>}
            <div className="row">{e.media.map((m: Json) => m.media_type === "photo" && m.url ? <img key={m.id} className="thumb" src={m.url} alt={m.caption ?? "여행 사진"} /> : null)}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="spread"><h2>하루 일기</h2>{daily && <span className={`tag ${daily.status === "saved" ? "" : "warn"}`}>{daily.status === "saved" ? "저장됨" : "초안"}</span>}</div>
        <p className="muted small">남긴 기록과 이동 거리만으로 초안을 만들어요. 말하지 않은 사건·감정은 쓰지 않습니다.</p>
        {draftText !== null && (
          <>
            {daily?.ai_draft_label && daily.status !== "saved" && <div className="tag warn">{daily.ai_draft_label}</div>}
            <textarea value={draftText} onChange={(e) => setDraftText(e.target.value)} aria-label="일기 본문" style={{ minHeight: 200, marginTop: 8 }} />
          </>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn secondary" onClick={makeDraft} disabled={busy}>{daily ? "초안 다시 만들기" : "AI 초안 만들기"}</button>
          {daily && draftText && <button className="btn" onClick={saveDaily} disabled={busy}>수정 완료·저장</button>}
        </div>
      </div>
    </div>
  );
}
