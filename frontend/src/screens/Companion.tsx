import { useEffect, useRef, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { listen, voiceSupported } from "../voice";

type Msg = { role: "user" | "ai"; text: string; meta?: Json };
const QUICK = ["도착 예상 시간은?", "오늘 코스 우회 공지 있어?", "지금 날씨 어때?", "근처 카페 알려줘", "오늘 일기 초안 만들어줘"];
const HISTORY_KEY = "jtm.chat";

function loadHistory(): Msg[] {
  try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
}

export default function Companion() {
  const { trip, session, me, go } = useApp();
  const [msgs, setMsgs] = useState<Msg[]>(loadHistory);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const stopRef = useRef<() => void>(() => undefined);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(msgs.slice(-30))); } catch { /* ignore */ }
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    const history = msgs.slice(-6).map((m) => ({ role: m.role === "user" ? "user" : "assistant", text: m.text }));
    setMsgs((m) => [...m, { role: "user", text: q }]);
    setText("");
    setBusy(true);
    try {
      const r = await api("/v1/agent/chat", { body: { message: q, trip_id: trip?.id ?? null, session_id: session?.status !== "ended" ? session?.session_id ?? null : null, history } });
      setMsgs((m) => [...m, { role: "ai", text: r.reply, meta: r }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "ai", text: (e as Error).message }]);
    } finally { setBusy(false); }
  };

  const decide = async (pid: string, approve: boolean) => {
    try {
      await api(`/v1/trips/${trip!.id}/proposals/${pid}/decision`, { body: { approve } });
      setMsgs((m) => [...m, { role: "ai", text: approve ? "일정에 반영했어요. 일정 화면에서 확인할 수 있어요." : "제안을 거절했어요. 일정은 그대로예요." }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message }]); }
  };

  const toggleVoice = () => {
    if (listening) { stopRef.current(); return; }
    if (!me.consents.microphone) { alert("설정에서 '음성 기록' 동의를 켜면 음성으로 질문할 수 있어요."); return; }
    setListening(true);
    stopRef.current = listen((t, final) => { setText(t); if (final) send(t); }, () => setListening(false));
  };

  return (
    <div>
      <h1>AI 동행자</h1>
      {me.ai_provider === "mock" && <div className="banner warn small">오프라인 모드: 코스·공지·날씨·도착 예상 등 도구 조회만 답합니다. 자유 대화는 AI 키 설정 후 가능합니다.</div>}
      {!me.consents.ai_processing && me.ai_external && <div className="banner warn small">AI 처리 동의가 꺼져 있어요. <button className="btn ghost" onClick={() => go("settings")}>설정</button></div>}

      <div aria-live="polite">
        {msgs.length === 0 && <p className="muted">코스, 일정, 공지, 날씨, 도착 예상, 주변 장소, 일기에 대해 물어보세요. 확인된 정보만 출처와 함께 답해요.</p>}
        {msgs.map((m, i) => (
          <div key={i}>
            <div className={`bubble ${m.role}`}>{m.text}</div>
            {(m.meta?.sources?.length ?? 0) > 0 && (
              <div className="muted small" style={{ margin: "-4px 0 6px 4px" }}>
                출처: {m.meta!.sources.map((s: Json, j: number) => (
                  <span key={j}>{j > 0 && " · "}{s.url ? <a href={s.url} target="_blank" rel="noopener">{s.name}</a> : s.name}{s.checked_at ? ` (${String(s.checked_at).slice(0, 16).replace("T", " ")})` : ""}</span>
                ))}
              </div>
            )}
            {m.meta?.proposals?.map((p: Json) => (
              <div key={p.id} className="banner warn">
                <strong>일정 변경 제안</strong> — {p.summary}
                <ul>{p.payload.changes.map((c: Json) => <li key={c.date}>{fmtDate(c.date)}: {c.route_id ? "코스 변경" : "휴식"}{c.planned_start ? ` ${c.planned_start} 출발` : ""}</li>)}</ul>
                <div className="row"><button className="btn" onClick={() => decide(p.id, true)}>승인</button><button className="btn ghost" onClick={() => decide(p.id, false)}>나중에</button></div>
              </div>
            ))}
          </div>
        ))}
        {busy && <div className="bubble ai muted">확인하는 중…</div>}
        <div ref={endRef} />
      </div>

      <div className="chips" style={{ margin: "10px 0" }}>
        {QUICK.map((q) => <button key={q} className="chip" onClick={() => send(q)} disabled={busy}>{q}</button>)}
      </div>
      <div className="row">
        <input type="text" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send(text)} placeholder="무엇이든 물어보세요" style={{ flex: 1 }} aria-label="질문 입력" />
        {voiceSupported() && <button className={`btn ${listening ? "danger" : "secondary"}`} onClick={toggleVoice} aria-label="음성으로 질문">{listening ? "■" : "🎤"}</button>}
        <button className="btn" onClick={() => send(text)} disabled={busy || !text.trim()}>보내기</button>
      </div>
      {msgs.length > 0 && <button className="btn ghost small" style={{ marginTop: 8 }} onClick={() => setMsgs([])}>대화 지우기</button>}
    </div>
  );
}
