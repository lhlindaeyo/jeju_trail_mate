/* Display language: Korean (default) or English, picked in Settings → 언어 / Language.
 * Text is written in place as t("한국어", "English"). The choice is kept in this browser ("jtm.lang", cleared by
 * "처음부터 다시 체험하기", so the opening is always Korean) and sent to the API as ?lang=en, so the server's own
 * messages (brief, alerts, AI replies, course names) come back in the same language. The splash and onboarding
 * (the opening) stay Korean only. */
import { useSyncExternalStore } from "react";

export type Lang = "ko" | "en";
export const LANG_KEY = "jtm.lang";

function read(): Lang {
  try { return localStorage.getItem(LANG_KEY) === "en" ? "en" : "ko"; } catch { return "ko"; }
}

let current: Lang = read();
const listeners = new Set<() => void>();
if (typeof document !== "undefined") document.documentElement.lang = current;

export const getLang = () => current;
export const isEn = () => current === "en";
export const t = (ko: string, en: string) => (current === "en" ? en : ko);

export function setLang(l: Lang) {
  if (l === current) return;
  current = l;
  try { if (l === "en") localStorage.setItem(LANG_KEY, l); else localStorage.removeItem(LANG_KEY); } catch { /* storage unavailable */ }
  document.documentElement.lang = l;
  listeners.forEach((f) => f());
}

export function useLang(): Lang {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; }, getLang);
}

/** BCP 47 locale for dates and times. */
export const locale = () => (current === "en" ? "en-US" : "ko-KR");

const WEEKDAY_KO = "일월화수목금토";
const WEEKDAY_EN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const weekday = (d: number) => (current === "en" ? WEEKDAY_EN[d] : WEEKDAY_KO[d]);
/** "11월 16일 (월)" / "Mon, Nov 16" */
export const longDate = (m: number, d: number, wd: number) =>
  current === "en" ? `${WEEKDAY_EN[wd]}, ${MONTH_EN[m - 1]} ${d}` : `${m}월 ${d}일 (${WEEKDAY_KO[wd]})`;
