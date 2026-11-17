import { useEffect, useState, type ReactNode } from "react";
import { resetAppData } from "../api";
import { useApp } from "../App";
import { disableNotifications } from "../notify";
import Plan from "./Plan";
import Settings from "./Settings";

export type SheetName = "menu" | "plan" | "settings";
const TITLES: Record<Exclude<SheetName, "menu">, string> = { plan: "여행 일정", settings: "설정·동의" };

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
  const { openSheet, simOn, setSimOn, go, toast } = useApp();
  const [resetting, setResetting] = useState(false);
  const reset = async () => {
    if (!confirm("여행·걷기 기록·설정·동의를 모두 지우고 처음 화면부터 다시 시작할까요? 되돌릴 수 없어요.")) return;
    setResetting(true);
    try {
      await disableNotifications();
      await resetAppData();
      history.replaceState(null, "", location.pathname + location.search); // back to the home tab
      location.reload();
    } catch (e) { setResetting(false); toast((e as Error).message); }
  };
  return (
    <Frame title="메뉴" onClose={onClose}>
      <div className="menu-list">
        <button onClick={() => openSheet("plan")}><div>여행 일정<span>날짜별 코스 초안·등록, AI 제안 확인</span></div>›</button>
        <button onClick={() => openSheet("settings")}><div>설정·동의<span>걷기 속도·종료 시각, 위치·AI 동의, 알림</span></div>›</button>
        <button aria-pressed={simOn} onClick={() => { setSimOn(!simOn); if (!simOn) { go("home"); onClose(); } }}>
          <div>가상 위치 (시연 모드)<span>제주 밖에서도 코스를 따라 걷는 것처럼 시험해요</span></div>
          <span className={`badge ${simOn ? "yellow" : "gray"}`}>{simOn ? "켜짐" : "꺼짐"}</span></button>
        <button onClick={reset} disabled={resetting}>
          <div>처음부터 다시 체험하기<span>모든 기록과 설정을 지우고 소개 화면부터 다시 시작해요</span></div>›</button>
      </div>
    </Frame>
  );
}

export function Sheet({ name, onClose }: { name: Exclude<SheetName, "menu">; onClose: () => void }) {
  const C = { plan: Plan, settings: Settings }[name];
  return <Frame title={TITLES[name]} onClose={onClose}><C /></Frame>;
}
