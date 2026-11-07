import { useEffect, useRef, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { listen, voiceSupported } from "../voice";

type Msg = { role: "me" | "ai"; text: string; meta?: Json; at?: string };
const EXAMPLES = ["근처 맛집 알려줘", "근처 관광지 알려줘", "관광지와 관련된 이야기 알려줘"];
const HISTORY_KEY = "jtm.chat";
const NAME = "돌하르방";
const AVATAR = "/hareubang.png";

const hhmm = (iso?: string) => (iso ? new Date(iso).toLocaleTimeString("ko-KR", { hour: "numeric", minute: "2-digit", hour12: true }) : "");
const now = () => new Date().toISOString();

function Avatar() {
  return <span className="chat-avatar" style={{ backgroundImage: `url(${AVATAR})` }} aria-hidden="true" />;
}

export default function Companion() {
  const { trip, session, me, openSheet, toast } = useApp();
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const stopRef = useRef<() => void>(() => undefined);
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
        session_id: session && session.status !== "ended" ? session.session_id : null, history } });
      setMsgs((m) => [...m, { role: "ai", text: r.reply, meta: r, at: now() }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message, at: now() }]); } finally { setBusy(false); }
  };

  const decide = async (pid: string, approve: boolean) => {
    try {
      await api(`/v1/trips/${trip!.id}/proposals/${pid}/decision`, { body: { approve } });
      setMsgs((m) => [...m, { role: "ai", text: approve ? "일정에 반영했어요. 메뉴 → 여행 일정에서 확인할 수 있어요." : "제안을 거절했어요. 일정은 그대로예요.", at: now() }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message, at: now() }]); }
  };

  const voice = () => {
    if (listening) { stopRef.current(); return; }
    if (!voiceSupported()) { toast("이 브라우저는 음성 입력을 지원하지 않아요"); return; }
    if (!me.consents.microphone) { openSheet("settings"); return; }
    setListening(true);
    stopRef.current = listen((t, final) => { setText(t); if (final) send(t); }, (err) => { setListening(false); if (err) toast(err); });
  };

  const newChat = () => { setMsgs([]); setText(""); };

  const notes = (
    <>
      {me.ai_provider === "mock" && <p className="chat-sys">오프라인 모드: 코스·경유지·공지·날씨·장소 조회만 답해요.</p>}
      {me.ai_external && !me.consents.ai_processing && <p className="chat-sys">AI 처리 동의가 꺼져 있어요. <button className="go" onClick={() => openSheet("settings")}>설정</button></p>}
    </>
  );

  return (
    <section className={`chat ${chatting ? "talking" : "intro"}`} aria-label="AI동행자">
      {chatting && (
        <header className="chat-bar">
          <Avatar /><div className="grow"><b>{NAME}</b><span>AI 동행자</span></div>
          <button type="button" className="chat-new" onClick={newChat} disabled={busy}>새 대화</button>
        </header>
      )}

      {!chatting ? (
        <div className={`chat-hero ${heroOut ? "out" : ""}`}>
          <div className="hero-bubble">안녕! 나는 {NAME}이야.<br />걷다가 궁금한 건 뭐든 물어봐!</div>
          <img className="hero-img" src={AVATAR} alt={`${NAME} 캐릭터`} />
          {notes}
        </div>
      ) : (
        <div className="chat-list" aria-live="polite">
          <p className="chat-sys">위급할 때는 바로 <a href="tel:119">119</a>(경찰 <a href="tel:112">112</a>)에 연락하세요. 이 앱은 구조 서비스가 아니에요.</p>
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
                      <div className="src">출처: {m.meta!.sources.map((s: Json, j: number) => (
                        <span key={j}>{j > 0 && " · "}{s.url ? <a href={s.url} target="_blank" rel="noopener">{s.name}</a> : s.name}{s.checked_at ? ` (${String(s.checked_at).slice(0, 16).replace("T", " ")})` : ""}</span>))}</div>
                    )}
                    {m.meta?.proposals?.map((p: Json) => (
                      <div key={p.id} className="card">
                        <b>일정 변경 제안</b> <span className="badge warn">승인 필요</span>
                        <p className="note" style={{ margin: "4px 0" }}>{p.summary}</p>
                        <ul style={{ margin: "0 0 8px", paddingLeft: 18, fontSize: 13 }}>{p.payload.changes.map((c: Json) => <li key={c.date}>{fmtDate(c.date)}: {c.route_id ? "코스 변경" : "휴식"}</li>)}</ul>
                        <div className="row"><button className="btn sm" onClick={() => decide(p.id, true)}>승인</button><button className="btn sm ghost" onClick={() => decide(p.id, false)}>나중에</button></div>
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
              <div className="bubble ai typing" aria-label="답변 확인 중"><i /><i /><i /></div></div></div>
          )}
          <div ref={endRef} />
        </div>
      )}

      <div className="chat-foot">
        {!chatting && !heroOut && (
          <div className="examples" aria-label="예시 질문">
            {EXAMPLES.map((q) => <button key={q} type="button" onClick={() => send(q)} disabled={busy}>{q}</button>)}
          </div>
        )}
        <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text); }}>
          <input type="text" value={text} onChange={(e) => setText(e.target.value)} placeholder={listening ? "듣고 있어요…" : "무엇이든 물어보세요"} aria-label="질문 입력" />
          <button type="button" className={`ic-btn ${listening ? "rec" : ""}`} onClick={voice} aria-label={listening ? "음성 입력 멈추기" : "음성으로 질문"} aria-pressed={listening}>
            {listening
              ? <svg viewBox="0 0 24 24"><rect x="7" y="7" width="10" height="10" rx="2" /></svg>
              : <svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" /></svg>}
          </button>
          <button type="submit" className="ic-btn send" disabled={busy || !text.trim()} aria-label="보내기">
            <svg viewBox="0 0 24 24"><path d="M4 12 20 4l-4 16-4.5-6.5z" /><path d="M11.5 13.5 20 4" /></svg>
          </button>
        </form>
      </div>
    </section>
  );
}
