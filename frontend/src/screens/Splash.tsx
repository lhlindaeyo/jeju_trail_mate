import { useEffect } from "react";

const SPLASH_THEME = "#C3E6FB"; // sky colour of the artwork, so the phone's status bar blends in

/**
 * Full-screen launch artwork. It sits on top of the app (which keeps loading underneath) and, when
 * `leaving` flips on, zooms gently toward the island while fading out to reveal the first screen.
 */
export default function Splash({ leaving, fadeMs }: { leaving: boolean; fadeMs: number }) {
  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    const prev = meta?.content;
    if (meta) meta.content = SPLASH_THEME;
    return () => { if (meta && prev) meta.content = prev; };
  }, []);

  return (
    <div className={`splash ${leaving ? "leaving" : ""}`} style={{ ["--fade" as string]: `${fadeMs}ms` }} role="img" aria-label="올레가멍" aria-hidden={leaving}>
      <img className="splash-art" src="/splash.webp" alt="" draggable={false} />
    </div>
  );
}
