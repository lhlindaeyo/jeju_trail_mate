import { useEffect, useState, type ReactNode } from "react";
import { resetAppData } from "../api";
import { useApp } from "../App";
import { t } from "../i18n";
import { disableNotifications } from "../notify";
import Plan from "./Plan";
import Settings from "./Settings";

export type SheetName = "menu" | "plan" | "settings";
const title = (s: Exclude<SheetName, "menu">) => (s === "plan" ? t("여행 일정", "Trip plan") : t("설정·동의", "Settings & consent"));

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
        <header><h2>{title}</h2><button className="close" onClick={onClose} aria-label={t("닫기", "Close")}>×</button></header>
        <div className="body">{children}</div>
      </div>
    </>
  );
}

export function MenuSheet({ onClose }: { onClose: () => void }) {
  const { openSheet, simOn, setSimOn, go, toast } = useApp();
  const [resetting, setResetting] = useState(false);
  const reset = async () => {
    if (!confirm(t("여행·걷기 기록·설정·동의를 모두 지우고 처음 화면부터 다시 시작할까요? 되돌릴 수 없어요.",
      "Erase all trips, walk records, settings and consents and start again from the first screen? This can't be undone."))) return;
    setResetting(true);
    try {
      await disableNotifications();
      await resetAppData();
      history.replaceState(null, "", location.pathname + location.search); // back to the home tab
      location.reload();
    } catch (e) { setResetting(false); toast((e as Error).message); }
  };
  return (
    <Frame title={t("메뉴", "Menu")} onClose={onClose}>
      <div className="menu-list">
        <button onClick={() => openSheet("plan")}><div>{t("여행 일정", "Trip plan")}<span>{t("날짜별 코스 초안·등록, AI 제안 확인", "Draft and register courses by date, review AI suggestions")}</span></div>›</button>
        <button onClick={() => openSheet("settings")}><div>{t("설정·동의", "Settings & consent")}<span>{t("언어, 걷기 속도·종료 시각, 위치·AI 동의, 알림", "Language, pace & end time, location & AI consent, alerts")}</span></div>›</button>
        <button aria-pressed={simOn} onClick={() => { setSimOn(!simOn); if (!simOn) { go("home"); onClose(); } }}>
          <div>{t("가상 위치 (시연 모드)", "Demo location (demo mode)")}<span>{t("제주 밖에서도 코스를 따라 걷는 것처럼 시험해요", "Try walking a course even when you're not in Jeju")}</span></div>
          <span className={`badge ${simOn ? "yellow" : "gray"}`}>{simOn ? t("켜짐", "On") : t("꺼짐", "Off")}</span></button>
        <button onClick={reset} disabled={resetting}>
          <div>{t("처음부터 다시 체험하기", "Start over from the beginning")}<span>{t("모든 기록과 설정을 지우고 소개 화면부터 다시 시작해요", "Erase all records and settings and restart from the intro (in Korean)")}</span></div>›</button>
      </div>
    </Frame>
  );
}

export function Sheet({ name, onClose }: { name: Exclude<SheetName, "menu">; onClose: () => void }) {
  const C = { plan: Plan, settings: Settings }[name];
  return <Frame title={title(name)} onClose={onClose}><C /></Frame>;
}
