import { useEffect, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { NewTripForm } from "./Home";

const THEME_LABEL: Record<string, string> = { olle_green: "올레 그린", sunset: "노을", ocean: "바다" };
const BOOK_KEY = (tripId: string) => `jtm.book.${tripId}`;

export default function Book() {
  const { trip } = useApp();
  const [book, setBook] = useState<Json | null>(null);
  const [entries, setEntries] = useState<Json[]>([]);
  const [form, setForm] = useState<Json>({ title: "", theme: "olle_green", prologue: "", epilogue: "", cover_media_id: null, excluded_entry_ids: [] });
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!trip) return;
    setForm((f) => ({ ...f, title: f.title || trip.title }));
    api(`/v1/trips/${trip.id}/journals`).then((r) => setEntries(r.entries.filter((e: Json) => e.status === "saved"))).catch(() => undefined);
    let id: string | null = null;
    try { id = localStorage.getItem(BOOK_KEY(trip.id)); } catch { /* ignore */ }
    if (id) api(`/v1/books/${id}`).then((b) => { setBook(b); setForm({ title: b.title, theme: b.theme, prologue: b.prologue ?? "", epilogue: b.epilogue ?? "", cover_media_id: b.cover_media_id, excluded_entry_ids: b.excluded_entry_ids }); }).catch(() => undefined);
  }, [trip]);

  if (!trip) return <NewTripForm />;
  const photos = entries.flatMap((e) => e.media.filter((m: Json) => m.media_type === "photo").map((m: Json) => ({ ...m, date: e.date })));
  const set = (k: string, v: unknown) => setForm({ ...form, [k]: v });
  const toggleExclude = (id: string) => set("excluded_entry_ids", form.excluded_entry_ids.includes(id) ? form.excluded_entry_ids.filter((x: string) => x !== id) : [...form.excluded_entry_ids, id]);

  const run = async (fn: () => Promise<Json>) => {
    setBusy(true); setMsg("");
    try {
      const b = await fn();
      setBook(b);
      try { localStorage.setItem(BOOK_KEY(trip.id), b.id); } catch { /* ignore */ }
      if (b.status === "failed") setMsg(b.error ?? "생성에 실패했어요. 다시 시도해 주세요.");
    } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); }
  };
  const body = { ...form, prologue: form.prologue || null, epilogue: form.epilogue || null };
  const generate = () => run(() => book ? api(`/v1/books/${book.id}`, { method: "PATCH", body }) : api(`/v1/trips/${trip.id}/book`, { body }));
  const approve = () => run(() => api(`/v1/books/${book!.id}/approve`, { method: "POST" }));

  const exportPdf = async () => {
    setMsg("");
    try {
      const res = await fetch(`/v1/books/${book!.id}/export`, { headers: { Authorization: `Bearer ${localStorage.getItem("jtm.token")}` } });
      if (!res.ok) throw new Error((await res.json()).detail);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (blob.type.includes("pdf")) {
        const a = document.createElement("a");
        a.href = url; a.download = `${form.title}.pdf`; a.click();
      } else {
        // No server PDF engine: open the print-ready page and use the browser's "PDF로 저장".
        const w = window.open(url, "_blank");
        w?.addEventListener("load", () => w.print());
        setMsg("인쇄 대화상자에서 'PDF로 저장'을 선택하세요.");
      }
      setBook({ ...book!, status: "exported" });
    } catch (e) { setMsg((e as Error).message); }
  };

  const remove = async () => {
    if (!book || !confirm("생성된 책을 삭제할까요? 일기와 사진은 그대로 남아요.")) return;
    await api(`/v1/books/${book.id}`, { method: "DELETE" });
    setBook(null);
    try { localStorage.removeItem(BOOK_KEY(trip.id)); } catch { /* ignore */ }
  };

  return (
    <div>
      <h1>완주 디지털 책</h1>
      <p className="muted small">저장한 일기와 사진만 들어가요. 기록이 없는 날은 비워 둡니다. 미리보기 확인 → 승인 → 내보내기 순서예요.</p>
      {msg && <div className="banner warn">{msg}</div>}

      <div className="card">
        <label className="field">책 제목<input type="text" value={form.title} onChange={(e) => set("title", e.target.value)} /></label>
        <div className="muted small">스타일</div>
        <div className="chips">{Object.keys(THEME_LABEL).map((t) => <button key={t} className={`chip ${form.theme === t ? "on" : ""}`} onClick={() => set("theme", t)}>{THEME_LABEL[t]}</button>)}</div>
        {photos.length > 0 && (
          <>
            <div className="muted small" style={{ marginTop: 10 }}>표지 사진</div>
            <div className="row">
              {photos.map((p) => (
                <button key={p.id} onClick={() => set("cover_media_id", form.cover_media_id === p.id ? null : p.id)} style={{ padding: 0, border: form.cover_media_id === p.id ? "3px solid var(--green)" : "3px solid transparent", borderRadius: 12, background: "none" }} aria-pressed={form.cover_media_id === p.id}>
                  {p.url && <img className="thumb" src={p.url} alt={p.caption ?? "사진"} />}
                </button>
              ))}
            </div>
          </>
        )}
        <label className="field">프롤로그 — 이 여행을 시작한 이유 (비워 두면 빈 페이지)<textarea value={form.prologue} onChange={(e) => set("prologue", e.target.value)} /></label>
        <label className="field">에필로그 — 여행을 마친 소감<textarea value={form.epilogue} onChange={(e) => set("epilogue", e.target.value)} /></label>
      </div>

      {entries.length > 0 && (
        <div className="card">
          <h2>포함할 기록</h2>
          {entries.map((e) => (
            <label key={e.id} className="toggle">
              <input type="checkbox" checked={!form.excluded_entry_ids.includes(e.id)} onChange={() => toggleExclude(e.id)} />
              <span><span className="muted small">{fmtDate(e.date)} · {e.kind === "daily" ? "하루 일기" : "메모"}</span><br />{(e.user_edited_text ?? e.text ?? "(사진)").slice(0, 80)}</span>
            </label>
          ))}
        </div>
      )}

      <button className="btn big" onClick={generate} disabled={busy || !form.title}>{busy ? "만드는 중…" : book ? "수정 반영해 다시 만들기" : "책 미리보기 만들기"}</button>

      {book?.preview_url && (
        <div className="card">
          <div className="spread"><h2>미리보기</h2><span className="tag">{{ preview_ready: "승인 대기", user_approved: "승인됨", exported: "내보냄" }[book.status as string] ?? book.status}</span></div>
          <iframe className="preview" src={book.preview_url} title="책 미리보기" sandbox="allow-same-origin allow-modals" />
          <div className="btn-row" style={{ marginTop: 10 }}>
            {book.status === "preview_ready" && <button className="btn" onClick={approve}>이대로 승인</button>}
            {(book.status === "user_approved" || book.status === "exported") && <button className="btn" onClick={exportPdf}>PDF 내보내기</button>}
            <button className="btn ghost" onClick={remove}>책 삭제</button>
          </div>
          <p className="muted small">미리보기 링크는 몇 분 후 만료돼요. 책은 기본 비공개이며 공유 기능은 아직 꺼져 있습니다.</p>
        </div>
      )}
    </div>
  );
}
