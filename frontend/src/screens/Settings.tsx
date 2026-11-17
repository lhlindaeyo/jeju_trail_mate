import { useEffect, useState } from "react";
import { api, type Json } from "../api";
import { useApp } from "../App";
import { setLang, t, useLang, type Lang } from "../i18n";
import { disableNotifications, enableNotifications, notifyState, sendTestNotification, type NotifyState } from "../notify";

const notifyTypes = (): [string, string, string][] => [
  ["route_notice", t("공식 우회·통제 공지 (안전)", "Official detours & closures (safety)"), t("공식 우회·통제 소식이 있을 때 알려줘요.", "Tells you when there's an official detour or closure.")],
  ["end_time_risk", t("종료 시각·일몰 임박 (안전)", "End time & sunset close (safety)"), t("해 지기 전에 못 끝낼 것 같으면 알려줘요.", "Tells you if it looks like you won't finish before sunset.")],
  ["schedule_delay", t("일정 지연", "Running behind"), t("계획보다 늦어지면 알려줘요.", "Tells you when you fall behind your plan.")],
  ["rest_reminder", t("휴식 제안", "Break reminders"), t("쉬어갈 때가 되면 알려줘요.", "Tells you when it's time for a break.")],
  ["weather", t("비 예보", "Rain forecast"), t("비가 올 것 같으면 알려줘요.", "Tells you when rain is likely.")],
  ["notice_unverified", t("공지 최신 확인 필요", "Notices need checking"), t("공지를 최신으로 확인하지 못했을 때 알려줘요.", "Tells you when the latest notices couldn't be checked.")],
];

const notifyLabel = (s: NotifyState) => ({
  on: t("켜짐 · 앱을 닫아도 잠금화면·상단 배너로 알려 드려요", "On · alerts show on your lock screen and banner even with the app closed"),
  on_in_app_only: t("켜짐 · 앱이 열려 있거나 백그라운드에 있을 때 알려 드려요", "On · alerts arrive while the app is open or in the background"),
  off: t("꺼짐", "Off"),
  blocked: t("차단됨 · 휴대폰 설정 → 브라우저(또는 앱) → 알림에서 허용해 주세요", "Blocked · allow it in your phone's Settings → browser (or app) → Notifications"),
  ios_needs_home_screen: t("아이폰은 Safari 공유 → '홈 화면에 추가'로 설치한 뒤 그 앱에서 켤 수 있어요", "On iPhone, install with Safari Share → 'Add to Home Screen', then turn it on in that app"),
  unsupported: t("이 브라우저는 알림을 지원하지 않아요", "This browser doesn't support notifications"),
})[s];

/** 언어 / Language. Applies at once and is kept in this browser; the opening (splash, intro) stays Korean. */
function LanguageCard() {
  const lang = useLang();
  const pick = (l: Lang) => setLang(l);
  return (
    <div className="card">
      <h2>언어 · Language</h2>
      <div className="seg" role="group" aria-label="언어 · Language" style={{ marginBottom: 8 }}>
        <button type="button" lang="ko" aria-pressed={lang === "ko"} onClick={() => pick("ko")}>한국어</button>
        <button type="button" lang="en" aria-pressed={lang === "en"} onClick={() => pick("en")}>English</button>
      </div>
      <p className="muted small" style={{ margin: 0 }}>{t("앱 화면과 알림, AI 답변이 고른 언어로 나와요. 장소 이름은 공식 표기(한국어) 그대로예요.",
        "Screens, alerts and AI answers use the language you pick. Place names stay in their official Korean form.")}</p>
    </div>
  );
}

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
      <h2>{t("폰 알림", "Phone alerts")}</h2>
      <p className="muted small" style={{ margin: "0 0 8px" }}>{t("안전·일정 알림을 휴대폰 잠금화면과 상단 배너로 받아요.", "Get safety and schedule alerts on your phone's lock screen and banner.")}</p>
      <p style={{ margin: "0 0 10px" }}><strong>{state ? notifyLabel(state) : t("확인 중…", "Checking…")}</strong></p>
      <div className="row wrap">
        {(state === "off" || state === "on_in_app_only") && (
          <button className="btn sm" disabled={busy} onClick={() => run(async () => { const r = await enableNotifications(); setState(r.state); setMsg(r.message); })}>
            {state === "off" ? t("폰 알림 켜기", "Turn on phone alerts") : t("앱 닫혀도 받기", "Also when the app is closed")}</button>
        )}
        {on && <button className="btn sm ghost" disabled={busy} onClick={() => run(async () => setMsg(await sendTestNotification()))}>{t("테스트 알림", "Test alert")}</button>}
        {state === "on" && (
          <button className="btn sm ghost" disabled={busy} onClick={() => run(async () => { await disableNotifications(); setState(await notifyState()); setMsg(t("앱이 닫혀 있을 때의 알림을 껐어요.", "Turned off alerts for when the app is closed.")); })}>
            {t("닫혀 있을 때 끄기", "Off when closed")}</button>
        )}
      </div>
      {msg && <p className="muted small" style={{ margin: "8px 0 0" }}>{msg}</p>}
      <p className="muted small" style={{ margin: "8px 0 0" }}>{t("잠금화면에 보이는지는 휴대폰의 알림 설정(잠금화면 표시)도 따라요.", "Whether they show on the lock screen also depends on your phone's notification settings.")}</p>
    </div>
  );
}

export function PrefsForm({ onSaved, submitLabel }: { onSaved?: () => void | Promise<void>; submitLabel?: string }) {
  const [p, setP] = useState<Json | null>(null);
  const [msg, setMsg] = useState("");
  const [helpOpen, setHelpOpen] = useState<string | null>(null);

  useEffect(() => { api("/v1/me").then(setP).catch((e) => setMsg(e.message)); }, []);
  if (!p) return <p className="muted">{msg || t("불러오는 중…", "Loading…")}</p>;

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
      setMsg(t("저장했어요.", "Saved."));
      await onSaved?.();
    } catch (e) { setMsg((e as Error).message); }
  };

  return (
    <div>
      <div className="card">
        <h2>{t("걷기 설정", "Walking")}</h2>
        <div className="grid2">
          <label className="field">{t("하루 최대 거리(km)", "Max distance per day (km)")}<input type="number" min={3} max={40} value={p.daily_distance_target_km} onChange={(e) => set("daily_distance_target_km", e.target.value)} /></label>
          <label className="field">{t("평균 걷기 속도(km/h)", "Average pace (km/h)")}<input type="number" step={0.1} min={1.5} max={7} value={p.pace_kmh} onChange={(e) => set("pace_kmh", e.target.value)} /></label>
          <label className="field">{t("출발 시각", "Start time")}<input type="time" value={p.walk_start_time} onChange={(e) => set("walk_start_time", e.target.value)} /></label>
          <label className="field">{t("걷기 종료 목표", "Finish by")}<input type="time" value={p.walk_end_time} onChange={(e) => set("walk_end_time", e.target.value)} /></label>
        </div>
        <label className="field">{t("휴식 알림 간격(분, 0이면 끔)", "Break reminder every (min, 0 = off)")}<input type="number" min={0} max={300} value={p.rest_every_minutes} onChange={(e) => set("rest_every_minutes", e.target.value)} /></label>
      </div>

      <div className="card">
        <h2>{t("동의", "Consent")}</h2>
        <label className="toggle"><input type="checkbox" checked={p.consents.ai_processing} onChange={(e) => setConsent("ai_processing", e.target.checked)} />
          <span><strong>{t("AI 처리", "AI processing")}</strong><br /><span className="muted small">{t("질문과 현재 코스 상황을 외부 AI(Anthropic Claude)로 보내 답변을 만듭니다. 원시 GPS 기록은 보내지 않습니다.",
            "Your questions and current course status are sent to an external AI (Anthropic Claude) to write answers. Raw GPS tracks are never sent.")}</span></span></label>
        <label className="toggle"><input type="checkbox" checked={p.consents.location} onChange={(e) => setConsent("location", e.target.checked)} />
          <span><strong>{t("위치 기록", "Location recording")}</strong><br /><span className="muted small">{t("트레킹 세션을 시작했을 때만 위치를 기록합니다. 원시 위치는 세션 종료 30일 후 삭제됩니다.",
            "Your location is recorded only while a walk is running. Raw positions are deleted 30 days after the walk ends.")}</span></span></label>
      </div>

      <PhoneNotifications />

      <div className="card">
        <h2>{t("알림", "Alerts")}</h2>
        {notifyTypes().map(([k, label, help]) => (
          <div key={k} className="toggle-row">
            <div className="toggle-row-main">
              <label className="toggle"><input type="checkbox" checked={(ns.types ?? {})[k] !== false} onChange={(e) => setType(k, e.target.checked)} /><span>{label}</span></label>
              <button type="button" className="help-btn" aria-label={t(`${label} 설명`, `About: ${label}`)} aria-expanded={helpOpen === k} onClick={() => setHelpOpen(helpOpen === k ? null : k)}>?</button>
            </div>
            {helpOpen === k && <p className="help-text">{help}</p>}
          </div>
        ))}
        <p className="muted small">{t("안전 알림을 끄면 우회 공지·일몰 경고를 받지 못합니다.", "If you turn off safety alerts, you won't get detour notices or sunset warnings.")}</p>
      </div>
      {msg && <p className="muted">{msg}</p>}
      <button className="btn big" onClick={save}>{submitLabel ?? t("저장", "Save")}</button>
    </div>
  );
}

export default function Settings() {
  const { refreshMe } = useApp();

  return (
    <div>
      <LanguageCard />
      <PrefsForm onSaved={refreshMe} />
    </div>
  );
}
