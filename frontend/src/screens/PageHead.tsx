import type { ReactNode } from "react";
import { useApp } from "../App";

/** 탭 화면 제목 + 홈과 같은 메뉴 버튼. */
export default function PageHead({ title, sub }: { title: string; sub: ReactNode }) {
  const { openSheet } = useApp();
  return (
    <header className="pagehead">
      <div>
        <h1 className="title">{title}</h1>
        <p className="sub">{sub}</p>
      </div>
      <button className="menu" aria-label="메뉴" onClick={() => openSheet("menu")}><i /></button>
    </header>
  );
}
