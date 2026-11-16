import { useEffect, useState } from "react";
import { api, type Json } from "../api";
import { useApp } from "../App";
import { disableNotifications, enableNotifications, notifyState, sendTestNotification, type NotifyState } from "../notify";

const NOTIFY_TYPES: [string, string, string][] = [
  ["route_notice", "공식 우회·통제 공지 (안전)", "공식 우회·통제 소식이 있을 때 알려줘요."],
  ["end_time_risk", "종료 시각·일몰 임박 (안전)", "해 지기 전에 못 끝낼 것 같으면 알려줘요."],
  ["schedule_delay", "일정 지연", "계획보다 늦어지면 알려줘요."],
  ["rest_reminder", "휴식 제안", "쉬어갈 때가 되면 알려줘요."],
  ["weather", "비 예보", "비가 올 것 같으면 알려줘요."],
  ["notice_unverified", "공지 최신 확인 필요", "공지를 최신으로 확인하지 못했을 때 알려줘요."],
];

const NOTIFY_LABEL: Record<NotifyState, string> = {
  on: "켜짐 · 앱을 닫아도 잠금화면·상단 배너로 알려 드려요",
  on_in_app_only: "켜짐 · 앱이 열려 있거나 백그라운드에 있을 때 알려 드려요",
  off: "꺼짐",
  blocked: "차단됨 · 휴대폰 설정 → 브라우저(또는 앱) → 알림에서 허용해 주세요",
  ios_needs_home_screen: "아이폰은 Safari 공유 → '홈 화면에 추가'로 설치한 뒤 그 앱에서 켤 수 있어요",
  unsupported: "이 브라우저는 알림을 지원하지 않아요",
};

/** Phone banner / lock-screen alerts. Permission is requested only when this button is tapped. */
function PhoneNotifications() {
  const [state, setState] = useState<NotifyState | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { notifyState().then(setState); }, []);
  const run = async (f: () => Promise<void>) => { setBusy(true); try { await f(); } finally { setBusy(false); } };
  const on = state === "on" || state === "on_in_app_only";
  return (
    <div className="card">
      <h2>폰 알림</h2>
      <p className="muted small" style={{ margin: "0 0 8px" }}>안전·일정 알림을 휴대폰 잠금화면과 상단 배너로 받아요.</p>
      <p style={{ margin: "0 0 10px" }}><strong>{state ? NOTIFY_LABEL[state] : "확인 중…"}</strong></p>
      <div className="row wrap">
        {(state === "off" || state === "on_in_app_only") && (
          <button className="btn sm" disabled={busy} onClick={() => run(async () => { const r = await enableNotifications(); setState(r.state); setMsg(r.message); })}>
            {state === "off" ? "폰 알림 켜기" : "앱 닫혀도 받기"}</button>
        )}
        {on && <button className="btn sm ghost" disabled={busy} onClick={() => run(async () => setMsg(await sendTestNotification()))}>테스트 알림</button>}
        {state === "on" && (
          <button className="btn sm ghost" disabled={busy} onClick={() => run(async () => { await disableNotifications(); setState(await notifyState()); setMsg("앱이 닫혀 있을 때의 알림을 껐어요."); })}>
            닫혀 있을 때 끄기</button>
        )}
      </div>
      {msg && <p className="muted small" style={{ margin: "8px 0 0" }}>{msg}</p>}
      <p className="muted small" style={{ margin: "8px 0 0" }}>잠금화면에 보이는지는 휴대폰의 알림 설정(잠금화면 표시)도 따라요.</p>
    </div>
  );
}

export function PrefsForm({ onSaved, submitLabel = "저장" }: { onSaved?: () => void | Promise<void>; submitLabel?: string }) {
  const [p, setP] = useState<Json | null>(null);
  const [msg, setMsg] = useState("");
  const [helpOpen, setHelpOpen] = useState<string | null>(null);

  useEffect(() => { api("/v1/me").then(setP).catch((e) => setMsg(e.message)); }, []);
  if (!p) return <p className="muted">{msg || "불러오는 중…"}</p>;

  const set = (k: string, v: unknown) => setP({ ...p, [k]: v });
  const setConsent = (k: string, v: boolean) => setP({ ...p, consents: { ...p.consents, [k]: v } });
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
          notification_settings: p.notification_settings,
          consent_ai_processing: p.consents.ai_processing,
          consent_location: p.consents.location,
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
      </div>

      <div className="card">
        <h2>동의</h2>
        <label className="toggle"><input type="checkbox" checked={p.consents.ai_processing} onChange={(e) => setConsent("ai_processing", e.target.checked)} />
          <span><strong>AI 처리</strong><br /><span className="muted small">질문과 현재 코스 상황을 외부 AI(Anthropic Claude)로 보내 답변을 만듭니다. 원시 GPS 기록은 보내지 않습니다.</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.location} onChange={(e) => setConsent("location", e.target.checked)} />
          <span><strong>위치 기록</strong><br /><span className="muted small">트레킹 세션을 시작했을 때만 위치를 기록합니다. 원시 위치는 세션 종료 30일 후 삭제됩니다.</span></span></label>
      </div>

      <PhoneNotifications />

      <div className="card">
        <h2>알림</h2>
        {NOTIFY_TYPES.map(([k, label, help]) => (
          <div key={k} className="toggle-row">
            <div className="toggle-row-main">
              <label className="toggle"><input type="checkbox" checked={(ns.types ?? {})[k] !== false} onChange={(e) => setType(k, e.target.checked)} /><span>{label}</span></label>
              <button type="button" className="help-btn" aria-label={`${label} 설명`} aria-expanded={helpOpen === k} onClick={() => setHelpOpen(helpOpen === k ? null : k)}>?</button>
            </div>
            {helpOpen === k && <p className="help-text">{help}</p>}
          </div>
        ))}
        <p className="muted small">안전 알림을 끄면 우회 공지·일몰 경고를 받지 못합니다.</p>
      </div>
      {msg && <p className="muted">{msg}</p>}
      <button className="btn big" onClick={save}>{submitLabel}</button>
    </div>
  );
}

export default function Settings() {
  const { refreshMe } = useApp();

  return (
    <div>
      <PrefsForm onSaved={refreshMe} />
    </div>
  );
}
