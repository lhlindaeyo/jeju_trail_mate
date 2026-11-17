import type { ReactNode } from "react";
import { useApp } from "../App";
import { t } from "../i18n";

/** Tab page title + the same menu button as Home. */
export default function PageHead({ title, sub }: { title: string; sub: ReactNode }) {
  const { openSheet } = useApp();
  return (
    <header className="pagehead">
      <div>
        <h1 className="title">{title}</h1>
        <p className="sub">{sub}</p>
      </div>
      <button className="menu" aria-label={t("메뉴", "Menu")} onClick={() => openSheet("menu")}><i /></button>
    </header>
  );
}
