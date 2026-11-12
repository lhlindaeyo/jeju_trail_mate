// 백엔드(FastAPI)와 프런트(Vite)를 한 번에 띄우는 로컬 개발용 런처. 추가 의존성 없음.
//   npm run dev     개발: 백엔드 --reload + Vite 개발 서버
//   npm run share   외부 공개(심사위원용): 프런트 빌드 → vite preview + Cloudflare 임시 터널 → 공개 https 주소 출력
import { spawn, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const isWin = process.platform === "win32";
const backendDir = join(root, "backend");
const frontendDir = join(root, "frontend");

const venvPython = join(backendDir, ".venv", isWin ? "Scripts/python.exe" : "bin/python");
if (!existsSync(venvPython)) {
  console.error(
    "backend/.venv 가 없습니다. 먼저 한 번만 실행하세요:\n" +
      "  cd backend && python -m venv .venv\n" +
      `  ${isWin ? ".venv/Scripts/pip" : ".venv/bin/pip"} install -r requirements.txt`,
  );
  process.exit(1);
}

if (!existsSync(join(frontendDir, "node_modules"))) {
  console.log("[setup] frontend/node_modules 가 없어 npm install 을 실행합니다...");
  const r = spawnSync("npm install", { cwd: frontendDir, stdio: "inherit", shell: true });
  if (r.status !== 0) process.exit(r.status ?? 1);
}

const share = process.argv.includes("--share");

function findCloudflared() {
  if (spawnSync("cloudflared", ["--version"], { stdio: "ignore", shell: isWin }).status === 0) return "cloudflared";
  const known = isWin
    ? ["C:\\Program Files (x86)\\cloudflared\\cloudflared.exe", "C:\\Program Files\\cloudflared\\cloudflared.exe"]
    : ["/usr/local/bin/cloudflared", "/opt/homebrew/bin/cloudflared"];
  return known.find(existsSync) ?? null;
}

let cloudflared = null;
if (share) {
  cloudflared = findCloudflared();
  if (!cloudflared) {
    console.error("cloudflared 가 없습니다. 설치 후 다시 실행하세요:\n" +
      (isWin ? "  winget install --id Cloudflare.cloudflared -e" : "  brew install cloudflared"));
    process.exit(1);
  }
  console.log("[share] 프런트를 빌드합니다...");
  const b = spawnSync("npm run build", { cwd: frontendDir, stdio: "inherit", shell: true });
  if (b.status !== 0) process.exit(b.status ?? 1);
}

const COLORS = { api: "\x1b[36m", web: "\x1b[35m", tunnel: "\x1b[33m" };
const RESET = "\x1b[0m";
const children = [];
let stopping = false;

function run(name, cmd, args, cwd, opts = {}) {
  const child = spawn(cmd, args, { cwd, shell: opts.shell ?? false, env: { ...process.env, FORCE_COLOR: "1", ...opts.env } });
  const prefix = `${COLORS[name]}[${name}]${RESET} `;
  const pipe = (stream, out) => {
    let buf = "";
    stream.on("data", (chunk) => {
      buf += chunk.toString();
      const lines = buf.split(/\r?\n/);
      buf = lines.pop();
      for (const line of lines) { out.write(prefix + line + "\n"); opts.onLine?.(line); }
    });
    stream.on("end", () => buf && out.write(prefix + buf + "\n"));
  };
  pipe(child.stdout, process.stdout);
  pipe(child.stderr, process.stderr);
  child.on("exit", (code) => {
    if (!stopping) {
      console.error(`${prefix}종료됨 (code ${code}). 나머지도 종료합니다.`);
      shutdown(code ?? 1);
    }
  });
  children.push(child);
}

function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const c of children) {
    if (c.exitCode !== null || !c.pid) continue;
    if (isWin) spawnSync("taskkill", ["/pid", String(c.pid), "/T", "/F"], { stdio: "ignore" });
    else c.kill("SIGTERM");
  }
  process.exit(code);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));

console.log("API  → http://localhost:8000   (docs: /docs)");
console.log("Web  → http://localhost:5173\n");

if (!share) {
  run("api", venvPython, ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"], backendDir);
  run("web", "npm run dev", [], frontendDir, { shell: true });
} else {
  // public demo: every browser (judge) gets its own user instead of sharing the one local user
  run("api", venvPython, ["-m", "uvicorn", "app.main:app", "--port", "8000"], backendDir, { env: { PER_DEVICE_USERS: "1" } });
  run("web", "npx vite preview", [], frontendDir, { shell: true });
  let announced = false;
  run("tunnel", cloudflared, ["tunnel", "--no-autoupdate", "--url", "http://localhost:5173"], root, {
    onLine(line) {
      const m = !announced && line.match(/https:\/\/[a-z0-9-]+\.trycloudflare\.com/);
      if (!m) return;
      announced = true;
      const bar = "=".repeat(64);
      console.log(`\n${bar}\n 공개 주소 (이 창을 닫으면 끊기고, 다시 실행하면 주소가 바뀝니다)\n` +
        `   앱           ${m[0]}\n   심사위원용   ${m[0]}/?demo=1   ← 가상 위치(시연 모드)로 열림\n${bar}\n`);
    },
  });
}
