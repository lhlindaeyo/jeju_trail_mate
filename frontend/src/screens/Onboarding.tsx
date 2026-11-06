import { useState } from "react";
import { markOnboarded } from "../api";
import { PrefsForm } from "./Settings";

export default function Onboarding({ onDone }: { onDone: () => Promise<void> }) {
  const [step, setStep] = useState<"intro" | "prefs">("intro");

  return (
    <div className="shell scroll" style={{ paddingTop: 32, paddingBottom: 40, overflowY: "auto" }}>
      {step === "intro" && (
        <div className="stack">
          <div className="brand">JEJU TRAIL MATE</div>
          <h1 className="title">제주를 걷는 동안엔 믿음직한 동행자,<br />여행이 끝나면 나만의 이야기책.</h1>
          <div className="card stack">
            <div><strong>PLAN</strong> · 기간·체력·귀환 시각에 맞춘 코스 일정 초안 (승인해야 확정)</div>
            <div><strong>WALK</strong> · 하영올레는 경유지 도착·경로 이탈까지, 제주올레는 거리·공지·일몰 알림</div>
            <div><strong>REMEMBER</strong> · 사진 한 장과 세 줄 일기, 완주한 마지막 날 디지털 책</div>
          </div>
          <div className="card muted small stack">
            <div>위치·마이크·사진 권한은 처음부터 요구하지 않고, 해당 기능을 쓰는 순간에 목적을 설명하고 요청합니다.</div>
            <div>이 앱은 구조·구급 서비스가 아닙니다. 위급할 때는 119에 연락하세요.</div>
            <div>JEJU TRAIL MATE는 사단법인 제주올레가 운영·보증하는 서비스가 아닙니다.</div>
          </div>
          <button className="btn big" onClick={() => setStep("prefs")}>시작하기</button>
        </div>
      )}
      {step === "prefs" && (
        <div>
          <h1 className="title">걷기 스타일 알려주기</h1>
          <p className="muted">일정 추천과 알림에만 쓰이며 언제든 설정에서 바꾸거나 지울 수 있어요.</p>
          <PrefsForm onSaved={async () => { markOnboarded(); await onDone(); }} submitLabel="저장하고 시작" />
        </div>
      )}
    </div>
  );
}
