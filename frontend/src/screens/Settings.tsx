import { useEffect, useState } from "react";
import { api, auth, type Json } from "../api";
import { useApp } from "../App";

const INTERESTS = ["바다", "오름", "숲", "카페", "문화", "마을", "일출", "풍경"];
const NOTIFY_TYPES: [string, string][] = [
  ["route_notice", "공식 우회·통제 공지 (안전)"],
  ["end_time_risk", "종료 시각·일몰 임박 (안전)"],
  ["schedule_delay", "일정 지연"],
  ["rest_reminder", "휴식 제안"],
  ["weather", "비 예보"],
  ["notice_unverified", "공지 최신 확인 필요"],
  ["journal_prompt", "기록 제안"],
];

export function PrefsForm({ onSaved, submitLabel = "저장" }: { onSaved?: () => void | Promise<void>; submitLabel?: string }) {
  const [p, setP] = useState<Json | null>(null);
  const [msg, setMsg] = useState("");

  useEffect(() => { api("/v1/me").then(setP).catch((e) => setMsg(e.message)); }, []);
  if (!p) return <p className="muted">{msg || "불러오는 중…"}</p>;

  const set = (k: string, v: unknown) => setP({ ...p, [k]: v });
  const setConsent = (k: string, v: boolean) => setP({ ...p, consents: { ...p.consents, [k]: v } });
  const toggleInterest = (i: string) => set("interests", p.interests.includes(i) ? p.interests.filter((x: string) => x !== i) : [...p.interests, i]);
  const ns = p.notification_settings ?? {};
  const setType = (k: string, v: boolean) => set("notification_settings", { ...ns, types: { ...(ns.types ?? {}), [k]: v } });

  const save = async () => {
    setMsg("");
    try {
      await api("/v1/me/preferences", {
        method: "PATCH",
        body: {
          daily_distance_target_km: Number(p.daily_distance_target_km), pace_kmh: Number(p.pace_kmh),
          walk_start_time: p.walk_start_time, walk_end_time: p.walk_end_time, rest_every_minutes: Number(p.rest_every_minutes),
          interests: p.interests, journal_style: p.journal_style || null, notification_settings: p.notification_settings,
          consent_ai_processing: p.consents.ai_processing, consent_memory: p.consents.memory,
          consent_location: p.consents.location, consent_microphone: p.consents.microphone, consent_photos: p.consents.photos,
        },
      });
      setMsg("저장했어요.");
      await onSaved?.();
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div>
      <div className="card">
        <h2>걷기 설정</h2>
        <div className="grid2">
          <label className="field">하루 최대 거리(km)<input type="number" min={3} max={40} value={p.daily_distance_target_km} onChange={(e) => set("daily_distance_target_km", e.target.value)} /></label>
          <label className="field">평균 걷기 속도(km/h)<input type="number" step={0.1} min={1.5} max={7} value={p.pace_kmh} onChange={(e) => set("pace_kmh", e.target.value)} /></label>
          <label className="field">출발 시각<input type="time" value={p.walk_start_time} onChange={(e) => set("walk_start_time", e.target.value)} /></label>
          <label className="field">걷기 종료 목표<input type="time" value={p.walk_end_time} onChange={(e) => set("walk_end_time", e.target.value)} /></label>
        </div>
        <label className="field">휴식 알림 간격(분, 0이면 끔)<input type="number" min={0} max={300} value={p.rest_every_minutes} onChange={(e) => set("rest_every_minutes", e.target.value)} /></label>
        <div className="muted small" style={{ margin: "8px 0 4px" }}>관심사</div>
        <div className="chips">
          {INTERESTS.map((i) => <button key={i} className={`chip ${p.interests.includes(i) ? "on" : ""}`} onClick={() => toggleInterest(i)} aria-pressed={p.interests.includes(i)}>{i}</button>)}
        </div>
        <label className="field">일기 문체(선택)<input type="text" maxLength={200} value={p.journal_style ?? ""} onChange={(e) => set("journal_style", e.target.value)} placeholder="예: 담백하고 짧게" /></label>
      </div>

      <div className="card">
        <h2>동의 (각각 철회 가능)</h2>
        <label className="toggle"><input type="checkbox" checked={p.consents.ai_processing} onChange={(e) => setConsent("ai_processing", e.target.checked)} />
          <span><strong>AI 처리</strong><br /><span className="muted small">질문·일기 기록을 외부 AI(Anthropic Claude)로 보내 답변과 초안을 만듭니다. 원시 GPS 기록은 보내지 않습니다.</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.memory} onChange={(e) => setConsent("memory", e.target.checked)} />
          <span><strong>개인화 기억</strong><br /><span className="muted small">관심사·문체를 AI 맥락에 사용합니다. 끄면 저장된 관심사·문체가 삭제됩니다.</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.location} onChange={(e) => setConsent("location", e.target.checked)} />
          <span><strong>위치 기록</strong><br /><span className="muted small">트레킹 세션을 시작했을 때만 위치를 기록합니다. 원시 위치는 세션 종료 30일 후 삭제됩니다.</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.microphone} onChange={(e) => setConsent("microphone", e.target.checked)} />
          <span><strong>음성 기록</strong><br /><span className="muted small">음성 메모를 글로 바꿉니다. 브라우저 음성 인식은 기기 제조사(구글·애플) 서버에서 처리될 수 있습니다.</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.photos} onChange={(e) => setConsent("photos", e.target.checked)} />
          <span><strong>사진 저장</strong><br /><span className="muted small">일기에 첨부한 사진을 비공개 저장소에 보관합니다.</span></span></label>
      </div>

      <div className="card">
        <h2>알림</h2>
        <label className="toggle"><input type="checkbox" checked={ns.enabled !== false} onChange={(e) => set("notification_settings", { ...ns, enabled: e.target.checked })} />
          <span><strong>알림 전체</strong></span></label>
        {NOTIFY_TYPES.map(([k, label]) => (
          <label key={k} className="toggle"><input type="checkbox" disabled={ns.enabled === false} checked={(ns.types ?? {})[k] !== false} onChange={(e) => setType(k, e.target.checked)} /><span>{label}</span></label>
        ))}
        <p className="muted small">안전 알림을 끄면 우회 공지·일몰 경고를 받지 못합니다.</p>
      </div>
      {msg && <p className="muted">{msg}</p>}
      <button className="btn big" onClick={save}>{submitLabel}</button>
    </div>
  );
}

export default function Settings() {
  const { refreshMe, me } = useApp();
  const [msg, setMsg] = useState("");

  const exportData = async () => {
    const data = await api("/v1/account/export");
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url; a.download = "jeju-trail-mate-export.json"; a.click();
    URL.revokeObjectURL(url);
  };
  const deleteAll = async () => {
    if (!confirm("계정과 모든 여행·일기·사진·책을 삭제합니다. 되돌릴 수 없어요. 계속할까요?")) return;
    const r = await api("/v1/account/data", { method: "DELETE" });
    setMsg(r.note);
    await auth.logout();
    location.reload();
  };
  const enableBrowserNotifications = async () => {
    if (!("Notification" in window)) return setMsg("이 브라우저는 알림을 지원하지 않습니다.");
    const p = await Notification.requestPermission();
    setMsg(p === "granted" ? "브라우저 알림을 켰어요." : "알림 권한이 허용되지 않았어요.");
  };

  return (
    <div>
      <h1>설정</h1>
      <p className="muted small">AI: {me.ai_provider === "mock" ? "오프라인 모드(도구 조회만)" : me.ai_provider} · {me.email}</p>
      <PrefsForm onSaved={refreshMe} />
      <div className="card stack">
        <h2>내 데이터</h2>
        <button className="btn secondary" onClick={enableBrowserNotifications}>브라우저 알림 허용</button>
        <button className="btn secondary" onClick={exportData}>데이터 내보내기 (JSON)</button>
        <button className="btn ghost" onClick={async () => { await auth.logout(); location.reload(); }}>로그아웃</button>
        <button className="btn danger" onClick={deleteAll}>계정 및 모든 데이터 삭제</button>
        {msg && <p className="muted">{msg}</p>}
      </div>
    </div>
  );
}
