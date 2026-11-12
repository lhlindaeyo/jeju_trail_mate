import { useEffect, useState, type ReactNode } from "react";
import { api, type Json } from "../api";
import { useApp } from "../App";
import Plan from "./Plan";
import Settings from "./Settings";

export type SheetName = "menu" | "plan" | "settings" | "sources";
const TITLES: Record<Exclude<SheetName, "menu">, string> = { plan: "여행 일정", settings: "설정·동의", sources: "데이터 출처" };

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
  const { openSheet, me, simOn, setSimOn, go } = useApp();
  return (
    <Frame title="메뉴" onClose={onClose}>
      <div className="menu-list">
        <button onClick={() => openSheet("plan")}><div>여행 일정<span>날짜별 코스 초안·승인, AI 제안 확인</span></div>›</button>
        <button onClick={() => openSheet("settings")}><div>설정·동의<span>걷기 속도·종료 시각, 위치·AI 동의, 알림</span></div>›</button>
        <button onClick={() => openSheet("sources")}><div>데이터 출처<span>제주데이터허브·공공데이터 기준일과 이용 조건</span></div>›</button>
        <button aria-pressed={simOn} onClick={() => { setSimOn(!simOn); if (!simOn) { go("home"); onClose(); } }}>
          <div>가상 위치 (시연 모드)<span>제주 밖에서도 코스를 따라 걷는 것처럼 시험해요</span></div>
          <span className={`badge ${simOn ? "yellow" : "gray"}`}>{simOn ? "켜짐" : "꺼짐"}</span></button>
      </div>
      <p className="note">AI {me.ai_provider === "mock" ? "오프라인 모드" : me.ai_provider}</p>
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

export function Sheet({ name, onClose }: { name: Exclude<SheetName, "menu">; onClose: () => void }) {
  const C = { plan: Plan, settings: Settings, sources: Sources }[name];
  return <Frame title={TITLES[name]} onClose={onClose}><C /></Frame>;
}
