// 백엔드(FastAPI)와 프런트(Vite)를 한 번에 띄우는 로컬 개발용 런처. 추가 의존성 없음.
//   npm run dev
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

const COLORS = { api: "\x1b[36m", web: "\x1b[35m" };
const RESET = "\x1b[0m";
const children = [];
let stopping = false;

function run(name, cmd, args, cwd, opts = {}) {
  const child = spawn(cmd, args, { cwd, shell: opts.shell ?? false, env: { ...process.env, FORCE_COLOR: "1" } });
  const prefix = `${COLORS[name]}[${name}]${RESET} `;
  const pipe = (stream, out) => {
    let buf = "";
    stream.on("data", (chunk) => {
      buf += chunk.toString();
      const lines = buf.split(/\r?\n/);
      buf = lines.pop();
      for (const line of lines) out.write(prefix + line + "\n");
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

run("api", venvPython, ["-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"], backendDir);
run("web", "npm run dev", [], frontendDir, { shell: true });
