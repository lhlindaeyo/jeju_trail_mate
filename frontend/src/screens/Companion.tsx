import { useEffect, useRef, useState } from "react";
import { api, fmtDate, type Json } from "../api";
import { useApp } from "../App";
import { listen, voiceSupported } from "../voice";

type Msg = { role: "me" | "ai"; text: string; meta?: Json };
const QUICK: [string, string][] = [
  ["근처 맛집", "근처 맛집 알려줘"], ["남은 시간", "도착까지 남은 시간은?"], ["쉬어갈 곳", "근처에 쉬어갈 곳 있어?"],
  ["다음 경유지", "다음 경유지까지 얼마나 남았어?"], ["공지 확인", "오늘 코스 우회 공지 있어?"], ["날씨", "지금 날씨 어때?"],
];
const ICON: Record<string, string> = {
  pin: '<path d="M12 21s7-6 7-11a7 7 0 0 0-14 0c0 5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/>',
  warn: '<path d="M12 4 3 20h18z"/><path d="M12 10v4M12 17v.5"/>',
  info: '<circle cx="12" cy="12" r="8"/><path d="M12 11v5M12 8v.5"/>',
};
const iconFor = (t: string) => (t === "waypoint_arrival" || t === "endpoint_near" ? "pin"
  : ["schedule_delay", "end_time_risk", "rest_reminder"].includes(t) ? "clock" : ["route_notice", "off_route"].includes(t) ? "warn" : "info");
const HISTORY_KEY = "jtm.chat";

function ago(iso: string) {
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? "방금" : m < 60 ? `${m}분 전` : m < 1440 ? `${Math.round(m / 60)}시간 전` : `${Math.round(m / 1440)}일 전`;
}

export default function Companion() {
  const { trip, session, me, notifications, go, openSheet } = useApp();
  const [msgs, setMsgs] = useState<Msg[]>(() => {
    try { return JSON.parse(sessionStorage.getItem(HISTORY_KEY) || "[]"); } catch { return []; }
  });
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const stopRef = useRef<() => void>(() => undefined);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { sessionStorage.setItem(HISTORY_KEY, JSON.stringify(msgs.slice(-30))); } catch { /* ignore */ }
    endRef.current?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
  }, [msgs]);

  const send = async (q: string) => {
    if (!q.trim() || busy) return;
    const history = msgs.slice(-6).map((m) => ({ role: m.role === "me" ? "user" : "assistant", text: m.text }));
    setMsgs((m) => [...m, { role: "me", text: q }]);
    setText("");
    setBusy(true);
    try {
      const r = await api("/v1/agent/chat", { body: { message: q, trip_id: trip?.id ?? null,
        session_id: session && session.status !== "ended" ? session.session_id : null, history } });
      setMsgs((m) => [...m, { role: "ai", text: r.reply, meta: r }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message }]); } finally { setBusy(false); }
  };

  const decide = async (pid: string, approve: boolean) => {
    try {
      await api(`/v1/trips/${trip!.id}/proposals/${pid}/decision`, { body: { approve } });
      setMsgs((m) => [...m, { role: "ai", text: approve ? "일정에 반영했어요. 메뉴 → 여행 일정에서 확인할 수 있어요." : "제안을 거절했어요. 일정은 그대로예요." }]);
    } catch (e) { setMsgs((m) => [...m, { role: "ai", text: (e as Error).message }]); }
  };

  const voice = () => {
    if (listening) { stopRef.current(); return; }
    if (!me.consents.microphone) { openSheet("settings"); return; }
    setListening(true);
    stopRef.current = listen((t, final) => { setText(t); if (final) send(t); }, () => setListening(false));
  };

  const alerts = notifications.slice(0, 5);
  return (
    <section aria-label="AI동행자">
      <h1 className="title">AI 동행자</h1>
      <p className="sub">걷는 중 알림과 쉬어갈 곳 추천을 받아요. 확인된 데이터만 출처와 함께 답해요.</p>
      {me.ai_provider === "mock" && <p className="note">오프라인 모드: 코스·경유지·공지·날씨·장소 조회만 답해요. 자유 대화는 AI 키 설정 후 가능해요.</p>}
      {me.ai_external && !me.consents.ai_processing && <p className="note">AI 처리 동의가 꺼져 있어요. <button className="go" onClick={() => openSheet("settings")}>설정</button></p>}

      <h2 className="sec" style={{ marginTop: 0 }}>알림</h2>
      {alerts.length === 0 && <p className="note">아직 알림이 없어요. 걷기를 시작하면 경유지 도착·일정·공지 알림이 여기에 모여요.</p>}
      {alerts.map((n) => (
        <div key={n.id} className="card alert">
          <div className={`ic ${n.severity === "safety" ? "safety" : ""}`}><svg viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: ICON[iconFor(n.event_type)] }} /></div>
          <div className="grow">
            <b>{n.title}</b>
            <p>{n.message}</p>
            <div className="row wrap">
              <span className={`badge ${n.severity === "safety" ? "safety" : "gray"}`}>{ago(n.created_at)}{n.severity === "safety" ? " · 안전" : ""}</span>
              {n.event_type === "waypoint_arrival" && <button className="go" onClick={() => go("diary")}>사진 남기기</button>}
            </div>
          </div>
        </div>
      ))}

      <h2 className="sec">물어보기</h2>
      <div className="quick">{QUICK.map(([label, q]) => <button key={label} type="button" onClick={() => send(q)} disabled={busy}>{label}</button>)}</div>
      <div aria-live="polite">
        {msgs.map((m, i) => (
          <div key={i}>
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
        ))}
        {busy && <div className="bubble ai" style={{ color: "var(--muted)" }}>확인하는 중…</div>}
        <div ref={endRef} />
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <input type="text" value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send(text)} placeholder="무엇이든 물어보세요" aria-label="질문 입력" />
        {voiceSupported() && <button className={`btn sm ${listening ? "danger" : "ghost"}`} onClick={voice} aria-label="음성으로 질문">{listening ? "■" : "🎤"}</button>}
        <button className="btn sm" onClick={() => send(text)} disabled={busy || !text.trim()}>보내기</button>
      </div>
      <p className="note">위급할 때는 바로 <a href="tel:119">119</a>(경찰 <a href="tel:112">112</a>)에 연락하세요. 이 앱은 구조 서비스가 아니에요.</p>
    </section>
  );
}
