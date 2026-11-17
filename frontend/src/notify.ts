/* Phone notifications: system banners / lock-screen alerts through the service worker, and Web Push so they
 * also arrive with the app closed. Permission is asked only from an explicit tap (Settings → 폰 알림 켜기). */
import { api, type Json } from "./api";
import { t } from "./i18n";

export type NotifyState = "unsupported" | "ios_needs_home_screen" | "blocked" | "off" | "on" | "on_in_app_only";

const isIOS = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
const standalone = () => matchMedia("(display-mode: standalone)").matches || (navigator as Json).standalone === true;

export const notifySupported = () => "Notification" in window && "serviceWorker" in navigator;

function b64ToBytes(b64: string): Uint8Array<ArrayBuffer> {
  const s = atob((b64 + "=".repeat((4 - (b64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(s.length));
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

async function registration(): Promise<ServiceWorkerRegistration | null> {
  if (!("serviceWorker" in navigator)) return null;
  try {
    return (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
  } catch { return null; }
}

export async function notifyState(): Promise<NotifyState> {
  if (isIOS() && !standalone()) return "ios_needs_home_screen"; // iPhone: web notifications only for home-screen apps
  if (!notifySupported()) return "unsupported";
  if (Notification.permission === "denied") return "blocked";
  if (Notification.permission !== "granted") return "off";
  const reg = await registration();
  const sub = reg && "pushManager" in reg ? await reg.pushManager.getSubscription() : null;
  return sub ? "on" : "on_in_app_only";
}

/** Ask permission, then subscribe to Web Push when the server has it set up. */
export async function enableNotifications(): Promise<{ state: NotifyState; message: string }> {
  if (isIOS() && !standalone()) return { state: "ios_needs_home_screen", message: t("아이폰은 공유 → '홈 화면에 추가'로 설치한 앱에서만 알림을 받을 수 있어요.", "On iPhone, alerts only work in the app installed with Share → 'Add to Home Screen'.") };
  if (!notifySupported()) return { state: "unsupported", message: t("이 브라우저는 알림을 지원하지 않아요.", "This browser doesn't support notifications.") };
  const perm = await Notification.requestPermission();
  if (perm !== "granted") return { state: perm === "denied" ? "blocked" : "off", message: t("알림 권한이 허용되지 않았어요.", "Notification permission wasn't granted.") };
  const reg = await registration();
  if (!reg) return { state: "on_in_app_only", message: t("알림을 켰어요. 앱이 열려 있을 때 알려 드려요.", "Alerts are on. You'll get them while the app is open.") };
  try {
    const cfg = await api("/v1/push/config");
    if (!cfg.enabled || !("pushManager" in reg)) {
      return { state: "on_in_app_only", message: t("알림을 켰어요. 지금은 앱이 열려 있거나 백그라운드에 있을 때 알려 드려요.", "Alerts are on. For now you'll get them while the app is open or in the background.") };
    }
    await navigator.serviceWorker.ready;
    const sub = (await reg.pushManager.getSubscription())
      ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg.public_key) }));
    await api("/v1/push/subscribe", { body: sub.toJSON() });
    return { state: "on", message: t("폰 알림을 켰어요. 앱을 닫아도 잠금화면과 상단 배너로 알려 드려요.", "Phone alerts are on. Even with the app closed, they'll show on your lock screen and banner.") };
  } catch {
    return { state: "on_in_app_only", message: t("알림을 켰지만 푸시 등록에 실패했어요. 앱이 열려 있을 때는 알려 드려요.", "Alerts are on, but push sign-up failed. You'll still get them while the app is open.") };
  }
}

export async function disableNotifications(): Promise<void> {
  const reg = await registration();
  const sub = reg && "pushManager" in reg ? await reg.pushManager.getSubscription() : null;
  if (sub) {
    try { await api("/v1/push/unsubscribe", { body: { endpoint: sub.endpoint } }); } catch { /* offline */ }
    await sub.unsubscribe().catch(() => undefined);
  }
}

/** Show an alert as a system notification (banner / lock screen). Same id → same tag → shown once. */
export async function showSystemNotification(n: Json): Promise<void> {
  if (!notifySupported() || Notification.permission !== "granted") return;
  const reg = await registration();
  const data = { id: n.id, title: n.title, body: n.message ?? n.body, severity: n.severity, url: "/#brief" };
  if (reg?.active) { reg.active.postMessage({ type: "notify", notification: data }); return; }
  try { new Notification(data.title, { body: data.body, tag: data.id }); } catch { /* Android needs the service worker */ }
}

export async function sendTestNotification(): Promise<string> {
  try {
    const r = await api("/v1/push/test", { method: "POST" });
    if (r.enabled && r.sent > 0) return t("테스트 알림을 보냈어요. 잠금화면이나 상단 배너를 확인해 보세요.", "Test alert sent. Check your lock screen or the banner.");
  } catch { /* fall back to a local one */ }
  await showSystemNotification({ id: "test-local", title: t("JEJU TRAIL MATE 알림 확인", "JEJU TRAIL MATE test alert"),
    message: t("이렇게 상단 배너와 잠금화면에 알림이 와요.", "Alerts will show up like this on your banner and lock screen."), severity: "info" });
  return t("테스트 알림을 띄웠어요. 상단 배너를 확인해 보세요.", "Test alert shown. Check the banner.");
}
