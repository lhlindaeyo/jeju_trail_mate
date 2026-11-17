import { useEffect, useRef, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { locale, t } from "../i18n";

type Msg = { role: "me" | "ai"; text: string; meta?: Json; at?: string };
const examples = () => [t("근처 맛집 알려줘", "Good places to eat nearby?"), t("근처 관광지 알려줘", "Sights to see nearby?"),
  t("관광지와 관련된 이야기 알려줘", "Tell me a story about a sight here")];
const HISTORY_KEY = "jtm.chat";
const AVATAR = "/hareubang.png";

const hhmm = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString(locale(), { hour: "numeric", minute: "2-digit", hour12: true }) : "");
const now = () => new Date().toISOString();

function Avatar() {
  return <span className="chat-avatar" style={{ backgroundImage: `url(${AVATAR})` }} aria-hidden="true" />;
}

export default function Companion() {
  const { trip, session, me, openSheet, pos } = useApp();
  const NAME = t("꼬마하르방 제돌이", "Jedori, the little Hareubang");
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const [heroOut, setHeroOut] = useState(false); // big character shrinking away right after the first question
  const chatting = msgs.length > 0 && !heroOut;

  useEffect(() => {
    try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(msgs.slice(-30))); } catch { /* ignore */ }
    endRef.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "end" });
  }, [msgs, busy]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    if (msgs.length === 0) { setHeroOut(true); window.setTimeout(() => setHeroOut(false), 420); }
    const history = msgs.slice(-6).map((m) => ({ role: m.role === "me" ? "user" : "assistant", text: m.text }));
    setMsgs((m) => [...m, { role: "me", text: q, at: now() }]);
    setText("");
    setBusy(true);
    try {
      const r = await api("/v1/agent/chat", { body: { message: q, trip_id: trip?.id ?? null,
        session_id: session && session.status !== "ended" ? session.session_id : null, history,
        // current position for 'a story about here' and similar; the server uses it only with location consent
        location: pos && pos.onIsland ? { lat: pos.lat, lon: pos.lon, accuracy_m: pos.acc } : null } });
      setMsgs((m) => [...m, { role: "ai", text: r.reply, meta: r, at: now() }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message, at: now() }]); } finally { setBusy(false); }
  };

  const decide = async (pid: string, approve: boolean) => {
    try {
      await api(`/v1/trips/${trip!.id}/proposals/${pid}/decision`, { body: { approve } });
      setMsgs((m) => [...m, { role: "ai", text: approve ? t("일정에 반영했어요. 메뉴 → 여행 일정에서 확인할 수 있어요.", "Added to your plan. See it in Menu → Trip plan.")
        : t("제안을 거절했어요. 일정은 그대로예요.", "Suggestion declined. Your plan stays as it was."), at: now() }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message, at: now() }]); }
  };

  const newChat = () => { setMsgs([]); setText(""); };

  const notes = (
    <>
      {me.ai_provider === "mock" && <p className="chat-sys">{t("오프라인 모드: 코스·경유지·공지·날씨·장소 조회만 답해요.", "Offline mode: only answers about courses, waypoints, notices, weather and places.")}</p>}
      {me.ai_external && !me.consents.ai_processing && <p className="chat-sys">{t("AI 처리 동의가 꺼져 있어요.", "AI processing consent is off.")} <button className="go" onClick={() => openSheet("settings")}>{t("설정", "Settings")}</button></p>}
    </>
  );

  return (
    <section className={`chat ${chatting ? "talking" : "intro"}`} aria-label={t("AI동행자", "AI buddy")}>
      {chatting && (
        <header className="chat-bar">
          <Avatar /><div className="grow"><b>{NAME}</b><span>{t("AI 동행자", "AI trail buddy")}</span></div>
          <button type="button" className="chat-new" onClick={newChat} disabled={busy}>{t("새 대화", "New chat")}</button>
        </header>
      )}

      {!chatting ? (
        <div className={`chat-hero ${heroOut ? "out" : ""}`}>
          <div className="hero-bubble">{t(`안녕! 나는 ${NAME}야.`, `Hi! I'm ${NAME}.`)}<br />{t("걷다가 궁금한 건 뭐든 물어봐!", "Ask me anything while you walk!")}</div>
          <img className="hero-img" src={AVATAR} alt={t(`${NAME} 캐릭터`, `${NAME} character`)} />
          {notes}
        </div>
      ) : (
        <div className="chat-list" aria-live="polite">
          <p className="chat-sys">{t("위급할 때는 바로 ", "In an emergency, call ")}<a href="tel:119">119</a>{t("(경찰 ", " (police ")}<a href="tel:112">112</a>{t(")에 연락하세요. 이 앱은 구조 서비스가 아니에요.", ") right away. This app is not a rescue service.")}</p>
          {notes}
          {msgs.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              {m.role === "ai" && <Avatar />}
              <div className="msg-col">
                {m.role === "ai" && <span className="msg-name">{NAME}</span>}
                <div className="msg-line">
                  <div className="bubble-wrap">
                    <div className={`bubble ${m.role}`}>{m.text}</div>
                    {(m.meta?.sources?.length ?? 0) > 0 && (
                      <div className="src">{t("출처", "Source")}: {m.meta!.sources.map((s: Json, j: number) => (
                        <span key={j}>{j > 0 && " · "}{s.url ? <a href={s.url} target="_blank" rel="noopener">{s.name}</a> : s.name}{s.checked_at ? ` (${String(s.checked_at).slice(0, 16).replace("T", " ")})` : ""}</span>))}</div>
                    )}
                    {m.meta?.proposals?.map((p: Json) => (
                      <div key={p.id} className="card">
                        <b>{t("일정 변경 제안", "Plan change suggestion")}</b> <span className="badge warn">{t("승인 필요", "Needs approval")}</span>
                        <p className="note" style={{ margin: "4px 0" }}>{p.summary}</p>
                        <ul style={{ margin: "0 0 8px", paddingLeft: 18, fontSize: 13 }}>{p.payload.changes.map((c: Json) => <li key={c.date}>{fmtDate(c.date)}: {c.route_id ? t("코스 변경", "change course") : t("휴식", "rest")}</li>)}</ul>
                        <div className="row"><button className="btn sm" onClick={() => decide(p.id, true)}>{t("승인", "Approve")}</button><button className="btn sm ghost" onClick={() => decide(p.id, false)}>{t("나중에", "Later")}</button></div>
                      </div>
                    ))}
                  </div>
                  <time className="msg-time">{hhmm(m.at)}</time>
                </div>
              </div>
            </div>
          ))}
          {busy && (
            <div className="msg ai"><Avatar /><div className="msg-col"><span className="msg-name">{NAME}</span>
              <div className="bubble ai typing" aria-label={t("답변 확인 중", "Writing an answer")}><i /><i /><i /></div></div></div>
          )}
          <div ref={endRef} />
        </div>
      )}

      <div className="chat-foot">
        {!chatting && !heroOut && (
          <div className="examples" aria-label={t("예시 질문", "Example questions")}>
            {examples().map((q) => <button key={q} type="button" onClick={() => send(q)} disabled={busy}>{q}</button>)}
          </div>
        )}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input type="text" value={text} onChange={(e) => setText(e.target.value)} placeholder={t("무엇이든 물어보세요", "Ask anything")} aria-label={t("질문 입력", "Your question")} />
          <button type="submit" className="ic-btn send" disabled={busy || !text.trim()} aria-label={t("보내기", "Send")}>
            <svg viewBox="0 0 24 24"><path d="M4 12 20 4l-4 16-4.5-6.5z" /><path d="M11.5 13.5 20 4" /></svg>
          </button>
        </form>
      </div>
    </section>
  );
}
