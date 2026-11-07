import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

export type GuideStep = { target: string; title: string; desc: string };

const BUBBLE_W = 224;
const EDGE = 12;
const GAP = 14;

type Placed = { cx: number; cy: number; w: number; h: number; box: DOMRect; W: number; H: number };

// First-visit tour: a small bubble next to one icon at a time (settings first, then the bottom tabs left → right).
export default function Guide({ steps, onClose }: { steps: GuideStep[]; onClose: () => void }) {
  const [idx, setIdx] = useState(0);
  const [placed, setPlaced] = useState<Placed | null>(null);
  const root = useRef<HTMLDivElement>(null);
  const step = steps[idx];
  const last = idx === steps.length - 1;
  const next = useCallback(() => (last ? onClose() : setIdx((i) => i + 1)), [last, onClose]);

  const measure = useCallback(() => {
    const el = document.querySelector(step.target);
    const r = root.current?.getBoundingClientRect();
    if (!el || !r) { setPlaced(null); return false; }
    const b = el.getBoundingClientRect();
    setPlaced({ cx: b.left - r.left + b.width / 2, cy: b.top - r.top + b.height / 2, w: b.width, h: b.height, box: new DOMRect(b.left - r.left, b.top - r.top, b.width, b.height), W: r.width, H: r.height });
    return true;
  }, [step.target]);

  useLayoutEffect(() => {
    if (!measure()) next(); // target not on this screen → skip to the next step
  }, [measure, next]);

  useEffect(() => {
    window.addEventListener("resize", measure);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("resize", measure); window.removeEventListener("keydown", onKey); };
  }, [measure, onClose]);

  let bubble: React.CSSProperties = { visibility: "hidden" };
  let arrowLeft = 0;
  let above = true;
  if (placed) {
    above = placed.cy > placed.H / 2;
    const left = Math.min(Math.max(placed.cx - BUBBLE_W / 2, EDGE), placed.W - EDGE - BUBBLE_W);
    arrowLeft = Math.min(Math.max(placed.cx - left - 6, 16), BUBBLE_W - 28);
    bubble = above
      ? { left, bottom: placed.H - (placed.cy - placed.h / 2) + GAP }
      : { left, top: placed.cy + placed.h / 2 + GAP };
  }

  return (
    <div className="guide-root" ref={root} role="dialog" aria-modal="true" aria-label="처음 사용 안내">
      {placed && <div className="guide-spot" style={{ left: placed.box.x - 4, top: placed.box.y - 4, width: placed.w + 8, height: placed.h + 8 }} />}
      <div className={`guide-bubble ${above ? "above" : "below"}`} style={{ width: BUBBLE_W, ...bubble }}>
        <span className="guide-arrow" style={{ left: arrowLeft }} />
        <div className="guide-head">
          <b>{step.title}</b>
          <button type="button" className="guide-x" onClick={onClose} aria-label="안내 닫기">×</button>
        </div>
        <p>{step.desc}</p>
        <div className="guide-foot">
          <span>{idx + 1} / {steps.length}</span>
          <button type="button" className="guide-next" onClick={next}>{last ? "완료" : "다음"}</button>
        </div>
      </div>
    </div>
  );
}
