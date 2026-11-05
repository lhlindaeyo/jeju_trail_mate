import { useEffect, useState, type ReactNode } from "react";
import { api, auth, type Json } from "../api";
import { useApp } from "../App";
import Plan from "./Plan";
import Settings from "./Settings";

export type SheetName = "menu" | "plan" | "settings" | "sources" | "book";
const TITLES: Record<Exclude<SheetName, "menu">, string> = { plan: "여행 일정", settings: "설정·동의", sources: "데이터 출처", book: "완주 기념 책" };

function Frame({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <>
      <div className="sheet-bg" onClick={onClose} />
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <header><h2>{title}</h2><button className="close" onClick={onClose} aria-label="닫기">×</button></header>
        <div className="body">{children}</div>
      </div>
    </>
  );
}

export function MenuSheet({ onClose }: { onClose: () => void }) {
  const { openSheet, me } = useApp();
  return (
    <Frame title="메뉴" onClose={onClose}>
      <div className="menu-list">
        <button onClick={() => openSheet("plan")}><div>여행 일정<span>날짜별 코스 초안·승인, AI 제안 확인</span></div>›</button>
        <button onClick={() => openSheet("settings")}><div>설정·동의<span>걷기 속도·종료 시각, 위치·사진·AI 동의, 알림</span></div>›</button>
        <button onClick={() => openSheet("sources")}><div>데이터 출처<span>제주데이터허브·공공데이터 기준일과 이용 조건</span></div>›</button>
      </div>
      <p className="note">{me.email} · AI {me.ai_provider === "mock" ? "오프라인 모드" : me.ai_provider}</p>
      <button className="go" onClick={async () => { await auth.logout(); location.reload(); }}>로그아웃</button>
      <p className="note">JEJU TRAIL MATE는 사단법인 제주올레·서귀포시가 운영·보증하는 서비스가 아닙니다. 이 앱은 구조·구급 서비스가 아닙니다.</p>
    </Frame>
  );
}

function Sources() {
  const [data, setData] = useState<Json | null>(null);
  useEffect(() => { api("/v1/data-sources").then(setData).catch(() => undefined); }, []);
  if (!data) return <p className="note">불러오는 중…</p>;
  return (
    <div>
      {data.sources.map((s: Json) => (
        <div key={s.id} className="card">
          <div className="row"><b className="grow" style={{ fontSize: 14 }}>{s.name}</b>
            <span className={`badge ${s.status === "imported" ? "" : "gray"}`}>{s.status === "imported" ? "적재됨" : "시드/미적재"}</span></div>
          <p className="note" style={{ margin: "4px 0" }}>{s.provider}</p>
          <div className="row wrap">
            <span className="badge warn">기준일 {s.ref_date ?? "미상"}</span>
            <span className="badge gray">이용 조건: {s.license}</span>
            {s.poi_count > 0 && <span className="badge">장소 {s.poi_count}곳</span>}
          </div>
          {s.note && <p className="note">{s.note}</p>}
          {s.url && <a href={s.url} target="_blank" rel="noopener" style={{ fontSize: 12 }}>원본 페이지</a>}
        </div>
      ))}
      <p className="note">{data.disclaimer}</p>
    </div>
  );
}

function BookSheet() {
  const { trip, toast } = useApp();
  const [book, setBook] = useState<Json | null>(null);
  const [title, setTitle] = useState(trip?.title ?? "");
  const [theme, setTheme] = useState("olle_green");
  const [prologue, setPrologue] = useState("");
  const [epilogue, setEpilogue] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const key = trip ? `jtm.book.${trip.id}` : "";

  useEffect(() => {
    let id: string | null = null;
    try { id = localStorage.getItem(key); } catch { /* ignore */ }
    if (id) api(`/v1/books/${id}`).then((b) => { setBook(b); setTitle(b.title); setTheme(b.theme); setPrologue(b.prologue ?? ""); setEpilogue(b.epilogue ?? ""); }).catch(() => undefined);
  }, [key]);

  const run = async (fn: () => Promise<Json>) => {
    setBusy(true); setErr("");
    try {
      const b = await fn();
      setBook(b);
      try { localStorage.setItem(key, b.id); } catch { /* ignore */ }
      if (b.status === "failed") setErr(b.error);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const body = { title, theme, prologue: prologue || null, epilogue: epilogue || null };
  const generate = () => run(() => book ? api(`/v1/books/${book.id}`, { method: "PATCH", body }) : api(`/v1/trips/${trip!.id}/book`, { body }));
  const approve = () => run(() => api(`/v1/books/${book!.id}/approve`, { method: "POST" }));
  const download = async () => {
    try {
      const res = await fetch(`/v1/books/${book!.id}/export`, { headers: { Authorization: `Bearer ${auth.token}` } });
      if (!res.ok) throw new Error((await res.json()).detail);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      if (blob.type.includes("pdf")) {
        const a = document.createElement("a");
        a.href = url; a.download = `${title}.pdf`; a.click();
      } else {
        const w = window.open(url, "_blank");
        w?.addEventListener("load", () => w.print());
        toast("인쇄 창에서 'PDF로 저장'을 고르세요");
      }
      setBook({ ...book!, status: "exported" });
    } catch (e) { setErr((e as Error).message); }
  };

  return (
    <div>
      <p className="note">저장한 일기와 사진만 들어가요. 기록이 없는 날은 비워 두고, 미리보기를 확인하고 승인한 뒤 내려받아요.</p>
      <label className="field">책 제목<input type="text" value={title} onChange={(e) => setTitle(e.target.value)} /></label>
      <div className="chips">{[["olle_green", "올레 그린"], ["sunset", "노을"], ["ocean", "바다"]].map(([k, l]) => (
        <button key={k} className={`chip ${theme === k ? "on" : ""}`} onClick={() => setTheme(k)}>{l}</button>))}</div>
      <label className="field">프롤로그 (비워 두면 빈 페이지)<textarea className="long" value={prologue} onChange={(e) => setPrologue(e.target.value)} /></label>
      <label className="field">에필로그<textarea className="long" value={epilogue} onChange={(e) => setEpilogue(e.target.value)} /></label>
      {err && <p className="err">{err}</p>}
      <button className="btn" onClick={generate} disabled={busy || !title}>{busy ? "만드는 중…" : book ? "수정 반영해 다시 만들기" : "미리보기 만들기"}</button>
      {book?.preview_url && (
        <div style={{ marginTop: 14 }}>
          <iframe className="preview" src={book.preview_url} title="책 미리보기" sandbox="allow-same-origin allow-modals" />
          <div className="row" style={{ marginTop: 10 }}>
            {book.status === "preview_ready" && <button className="btn sm" onClick={approve}>이대로 승인</button>}
            {(book.status === "user_approved" || book.status === "exported") && <button className="btn sm dark" onClick={download}>PDF 내려받기</button>}
          </div>
        </div>
      )}
    </div>
  );
}

export function Sheet({ name, onClose }: { name: Exclude<SheetName, "menu">; onClose: () => void }) {
  const C = { plan: Plan, settings: Settings, sources: Sources, book: BookSheet }[name];
  return <Frame title={TITLES[name]} onClose={onClose}><C /></Frame>;
}
