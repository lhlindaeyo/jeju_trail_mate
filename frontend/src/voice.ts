/* Browser speech-to-text (Web Speech API). Started only by an explicit user tap. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Recognition = any;

export const voiceSupported = () => typeof window !== "undefined" && ("SpeechRecognition" in window || "webkitSpeechRecognition" in window);

export function listen(onText: (text: string, final: boolean) => void, onEnd: (err?: string) => void): () => void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const w = window as any;
  const Ctor = w.SpeechRecognition || w.webkitSpeechRecognition;
  if (!Ctor) { onEnd("이 브라우저는 음성 인식을 지원하지 않습니다."); return () => undefined; }
  const rec: Recognition = new Ctor();
  rec.lang = "ko-KR";
  rec.interimResults = true;
  rec.continuous = false;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rec.onresult = (e: any) => {
    let text = "";
    let final = false;
    for (let i = 0; i < e.results.length; i++) {
      text += e.results[i][0].transcript;
      final = final || e.results[i].isFinal;
    }
    onText(text, final);
  };
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  rec.onerror = (e: any) => onEnd(e.error === "not-allowed" ? "마이크 권한이 거부되었습니다." : "음성을 인식하지 못했어요.");
  rec.onend = () => onEnd();
  rec.start();
  return () => rec.stop();
}
