import { useState } from "react";
import { markOnboarded } from "../api";
import { NAV_ICONS } from "../App";
import { PrefsForm } from "./Settings";

const FEATURES: { icon: keyof typeof NAV_ICONS; text: string }[] = [
  { icon: "explore", text: "걷고 싶은 길과 날짜를 고르면 하루 일정을 짜 드려요." },
  { icon: "brief", text: "걷는 동안에는 해 지기 전에 도착할 수 있는지, 앞에 쉴 곳이 있는지, 날씨는 어떤지 한눈에 알려 드려요." },
  { icon: "ai", text: "궁금한 건 언제든 꼬마하르방 제돌이에게 물어보세요." },
];

export default function Onboarding({ onDone }: { onDone: () => Promise<void> }) {
  const [step, setStep] = useState<"intro" | "prefs">("intro");

  return (
    <div className="shell scroll" style={{ paddingTop: 32, paddingBottom: 40, overflowY: "auto" }}>
      {step === "intro" && (
        <div className="stack">
          <div className="brand">JEJU TRAIL MATE</div>
          <h1 className="title">제주를 걷는 동안엔<br />믿음직한 길동무.</h1>
          <div className="card stack">
            {FEATURES.map((f) => (
              <div key={f.icon} className="feat">
                <span className="ic" aria-hidden="true">{NAV_ICONS[f.icon]}</span>
                <p>{f.text}</p>
              </div>
            ))}
          </div>
          <p className="muted small" style={{ margin: "0 4px" }}>이 앱은 구조·구급 서비스가 아니에요. 위급할 때는 119에 연락하세요.</p>
          <button className="btn big" onClick={() => setStep("prefs")}>시작하기</button>
        </div>
      )}
      {step === "prefs" && (
        <div>
          <h1 className="title">걷기 스타일 알려주기</h1>
          <p className="muted">일정 추천과 알림에만 쓰이며 언제든 설정에서 바꿀 수 있어요.</p>
          <PrefsForm onSaved={async () => { markOnboarded(); await onDone(); }} submitLabel="저장하고 시작" />
        </div>
      )}
    </div>
  );
}
