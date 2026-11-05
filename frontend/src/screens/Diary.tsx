import { useCallback, useEffect, useRef, useState } from "react";
import { api, todayKst, type Json } from "../api";
import { useApp } from "../App";

const MAX_LINES = 3;
const MAX_CHARS = 150;
const WP_KEY = "jtm.diaryWaypoint";

/** Shrink to ≤ 800 px on the long side (v1.2 diary spec) and re-encode as JPEG. */
function resize(file: File): Promise<Blob> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const s = Math.min(1, 800 / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * s);
      c.height = Math.round(img.height * s);
      c.getContext("2d")!.drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      c.toBlob((b) => (b ? resolve(b) : reject(new Error("encode"))), "image/jpeg", 0.8);
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("decode")); };
    img.src = url;
  });
}

export default function Diary() {
  const { trip, me, refreshMe, session, course, openSheet, toast } = useApp();
  const [photo, setPhoto] = useState<Blob | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [entries, setEntries] = useState<Json[]>([]);
  const [waypointId, setWaypointId] = useState<string>(() => { try { return localStorage.getItem(WP_KEY) || ""; } catch { return ""; } });
  const [elig, setElig] = useState<Json | null>(null);
  const [draft, setDraft] = useState<Json | null>(null);
  const [draftText, setDraftText] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const today = todayKst();

  const load = useCallback(async () => {
    if (!trip) return;
    const [j, e] = await Promise.all([api(`/v1/trips/${trip.id}/journals`), api(`/v1/trips/${trip.id}/book/eligibility`)]);
    setEntries(j.entries.filter((x: Json) => x.kind === "memo").reverse());
    const daily = j.entries.find((x: Json) => x.kind === "daily" && x.date === today);
    setDraft(daily ?? null);
    setDraftText(daily ? daily.user_edited_text ?? daily.ai_draft ?? "" : "");
    setElig(e);
  }, [trip, today]);
  useEffect(() => { load().catch((e) => setErr(e.message)); }, [load]);
  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  if (!trip) {
    return (
      <section aria-label="일기작성">
        <h1 className="title">일기작성</h1>
        <p className="sub">여행을 만들면 매일 사진 한 장과 세 줄 일기를 남길 수 있어요.</p>
        <button className="btn" onClick={() => openSheet("plan")}>여행 만들기</button>
      </section>
    );
  }

  // place candidates: waypoints checked in during the current session first, then the course's waypoints
  const visitedNames = new Set<string>(session?.visited_waypoints ?? []);
  const places: Json[] = (course?.waypoints ?? []).filter((w: Json) => w.kind === "W")
    .sort((a: Json, b: Json) => Number(visitedNames.has(b.name)) - Number(visitedNames.has(a.name)));
  const lines = text.split("\n");
  const lineCount = text.trim() ? lines.filter((l) => l.trim()).length : 0;

  const onText = (v: string) => {
    let next = v.split("\n").slice(0, MAX_LINES).join("\n");
    if (next.length > MAX_CHARS) next = next.slice(0, MAX_CHARS);
    setText(next);
  };

  const pickPhoto = async (f: File) => {
    try {
      const b = await resize(f);
      setPhoto(b);
      setPreview(URL.createObjectURL(b));
      setErr("");
    } catch { setErr("사진을 불러오지 못했어요. 다른 사진을 골라 주세요."); }
  };

  const save = async () => {
    if (!photo) { setErr("사진 한 장을 먼저 골라 주세요."); return; }
    if (!text.trim()) { setErr("한 줄 이상 적어 주세요."); return; }
    if (!me.consents.photos) {
      if (!confirm("일기 사진을 비공개 저장소에 보관해요. 사진 저장에 동의할까요? (설정에서 언제든 철회)")) return;
      await api("/v1/me/preferences", { method: "PATCH", body: { consent_photos: true } });
      await refreshMe();
    }
    setBusy(true); setErr("");
    try {
      const fd = new FormData();
      fd.append("trip_id", trip.id);
      fd.append("text", text.trim());
      fd.append("photo", photo, "photo.jpg");
      if (waypointId) fd.append("waypoint_id", waypointId);
      await api("/v1/diary", { form: fd });
      setPhoto(null); setPreview(null); setText(""); setWaypointId("");
      try { localStorage.removeItem(WP_KEY); } catch { /* ignore */ }
      if (fileRef.current) fileRef.current.value = "";
      toast("일기를 저장했어요");
      await load();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };

  const remove = async (id: string) => {
    if (!confirm("이 일기를 삭제할까요?")) return;
    await api(`/v1/journals/${id}`, { method: "DELETE" });
    toast("일기를 삭제했어요");
    await load();
  };

  const makeDraft = async () => {
    setBusy(true); setErr("");
    try {
      const d = await api(`/v1/trips/${trip.id}/journals/daily-draft`, { body: { date: today } });
      setDraft(d); setDraftText(d.ai_draft);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const saveDraft = async () => {
    await api(`/v1/journals/${draft!.id}`, { method: "PUT", body: { text: draftText } });
    toast("하루 요약을 저장했어요");
    await load();
  };

  const d = new Date(today + "T00:00:00");
  return (
    <section aria-label="일기작성">
      <h1 className="title">일기작성</h1>
      <p className="sub">{d.getFullYear()}.{d.getMonth() + 1}.{d.getDate()} 오늘의 기록 · {trip.title}</p>

      <label className="photo" htmlFor="photoIn">
        {preview ? <img src={preview} alt="고른 사진 미리보기" /> : (
          <span className="ph"><svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z" /><circle cx="12" cy="13" r="3.5" /></svg>사진 한 장 고르기</span>
        )}
      </label>
      <input ref={fileRef} type="file" id="photoIn" accept="image/*" hidden onChange={(e) => e.target.files?.[0] && pickPhoto(e.target.files[0])} />
      <textarea className="diary" value={text} onChange={(e) => onText(e.target.value)} placeholder="오늘 걸으며 남기고 싶은 말을 세 줄까지 적어요."
        aria-label="일기 내용, 최대 3줄" maxLength={MAX_CHARS}
        onKeyDown={(e) => { if (e.key === "Enter" && text.split("\n").length >= MAX_LINES) e.preventDefault(); }} />
      <div className="meta"><span className="err">{err}</span><span>{lineCount} / {MAX_LINES}줄</span></div>
      {places.length > 0 && (
        <label className="field">장소 연결 (선택 — 확인한 경우만)
          <select value={waypointId} onChange={(e) => setWaypointId(e.target.value)}>
            <option value="">연결 안 함</option>
            {places.map((w: Json) => <option key={w.id} value={w.id}>{visitedNames.has(w.name) ? "✓ 지나온 곳 · " : ""}{w.name}</option>)}
          </select>
        </label>
      )}
      <button className="btn" type="button" onClick={save} disabled={busy}>일기 저장</button>

      <h2 className="sec">저장한 일기</h2>
      {entries.length === 0 && <p className="note">아직 저장한 일기가 없어요. 사진 한 장과 글을 남겨 보세요.</p>}
      {entries.map((e) => {
        const ph = e.media.find((m: Json) => m.media_type === "photo");
        return (
          <div key={e.id} className="card entry">
            {ph?.url ? <img src={ph.url} alt={e.waypoint?.name ?? "일기 사진"} /> : <img alt="" />}
            <div className="grow">
              <div className="d"><span>{e.date.replaceAll("-", ".")} {e.captured_at?.slice(11, 16)}{e.waypoint ? ` · ${e.waypoint.name}` : ""}</span>
                <button className="del" type="button" onClick={() => remove(e.id)}>삭제</button></div>
              <p>{e.user_edited_text ?? e.text}</p>
            </div>
          </div>
        );
      })}

      <h2 className="sec">하루 요약</h2>
      <div className="card">
        <p className="note" style={{ marginTop: 0 }}>오늘 남긴 일기와 지나온 곳만으로 초안을 만들어요. 쓰지 않은 감정·사건은 넣지 않아요.</p>
        {draft && (
          <>
            {draft.status !== "saved" && <span className="badge warn">AI 초안 — 저장 전 확인·수정</span>}
            <textarea className="long" value={draftText} onChange={(e) => setDraftText(e.target.value)} aria-label="하루 요약" style={{ marginTop: 8 }} />
          </>
        )}
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn sm ghost" onClick={makeDraft} disabled={busy}>{draft ? "다시 만들기" : "요약 초안 만들기"}</button>
          {draft && draftText.trim() && <button className="btn sm" onClick={saveDraft}>저장</button>}
        </div>
      </div>

      <h2 className="sec">디지털 책</h2>
      <div className="card">
        <b style={{ fontSize: 15 }}>완주 기념 책</b>
        <p className="note" style={{ margin: "4px 0 12px" }}>
          {elig?.eligible ? `완주를 축하해요. 저장한 일기 ${elig.saved_entries}편으로 책을 만들어요.`
            : elig?.reasons?.join(" ") ?? "코스를 완주한 마지막 날부터 내려받을 수 있어요."}
        </p>
        <button className="btn dark" type="button" disabled={!elig?.eligible} onClick={() => openSheet("book")}>디지털 책 내려받기</button>
        {elig?.rule && <p className="note">{elig.rule}</p>}
      </div>
    </section>
  );
}
