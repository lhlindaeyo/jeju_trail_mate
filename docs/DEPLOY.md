# 서버 배포 (폰에서 쓰기)

AI는 Gemini·OpenRouter 같은 클라우드 API를 쓰므로 서버에 GPU도 Ollama도 필요 없습니다. 작은 VPS(RAM 1~2GB)면 됩니다.

```
폰 ──https──> web(Caddy) ─┬─ 정적 파일(프런트 dist)
                          └─ /v1, /health ──> backend(FastAPI + SQLite)
```

프런트가 API를 `/v1/...` 상대경로로 부르기 때문에 두 가지가 한 주소에서 서빙돼야 하고, 그걸 Caddy가 맡습니다.
폰의 GPS·홈 화면 설치·푸시는 **HTTPS에서만** 동작합니다.

구성 파일: `docker-compose.prod.yml`, `frontend/Dockerfile`, `frontend/Caddyfile`, `deploy/backend.env.example`.

## 0. 준비

- Docker + Docker Compose가 깔린 리눅스 서버(저장소를 `git clone` 해 둠).
- 주소를 정하는 방법은 둘 중 하나입니다.
  - **도메인이 있다**(또는 DuckDNS 같은 무료 도메인): DNS A 레코드를 서버 IP로 향하게 하고, 서버의 80·443 포트를 엽니다.
  - **도메인이 없다**: 3-B의 Cloudflare 임시 터널로 먼저 시험합니다(주소는 재시작하면 바뀝니다).

## 1. 설정 파일 만들기

```bash
cp deploy/backend.env.example backend/.env
# backend/.env 를 열어 ADMIN_TOKEN(긴 임의 문자열), GEMINI_API_KEY, OPENROUTER_API_KEY, KMA_* 를 채웁니다.
```

내 PC의 `backend/.env`에 이미 키가 있으면 그 파일을 서버로 옮겨도 됩니다. 단 `APP_ENV`·`CORS_ORIGINS` 같은 개발용 값은 compose가 덮어쓰거나 쓰이지 않습니다.

## 2. 데이터베이스 올리기

코스·경로·경유지·장소 데이터는 DB 파일에 적재돼 있습니다(`data/raw`의 원본 일부는 git에 없음). 서버에서 다시 적재하는 대신, 지금 PC의 DB를 처음 한 번 옮깁니다.

```bash
# PC에서
scp backend/jeju_trail_mate.db <서버>:<저장소>/server_data/jeju_trail_mate.db   # 서버에 server_data/ 폴더를 먼저 만들어 둡니다
```

- 이 DB에는 **내 PC에서 쓰던 여행·걷기 기록(GPS 점 포함)**도 들어 있습니다. 서버는 기기마다 따로 사용자를 만들어서(`PER_DEVICE_USERS=1`) 그 기록이 다른 사람에게 보이진 않지만, 파일 자체는 서버에 올라갑니다. 신경 쓰이면 데이터 적재 명령(README의 "데이터 적재")을 서버에서 새로 돌려 만드세요.
- DB를 안 올리고 시작하면 시드 데이터만 있는 빈 DB가 생기고, 하영올레 경로선 같은 적재 데이터는 없습니다.
- 백업은 `server_data/jeju_trail_mate.db` 파일 하나를 복사하면 됩니다.

## 3. 실행

### 3-A. 도메인이 있을 때

```bash
echo "SITE_ADDRESS=trail.내도메인.com" > .env     # compose 가 읽는 루트 .env (git 제외)
docker compose -f docker-compose.prod.yml up -d --build
```

Caddy가 인증서를 자동으로 받아 `https://trail.내도메인.com`이 열립니다(처음엔 1분쯤 걸림).

### 3-B. 도메인이 없을 때 (임시 터널로 시험)

```bash
docker compose -f docker-compose.prod.yml --profile tunnel up -d --build
docker compose -f docker-compose.prod.yml logs tunnel | grep trycloudflare
```

나오는 `https://xxxx.trycloudflare.com`을 폰에서 엽니다. 서버를 재시작하면 주소가 바뀌니 시험용입니다. 오래 쓰려면 도메인을 연결하세요.

## 4. 폰에서 확인

1. 폰 브라우저(크롬/사파리)로 주소를 엽니다. 제주 밖에서 시험하면 뒤에 `/?demo=1`을 붙이면 가상 위치로 열립니다.
2. 브라우저 메뉴 → **홈 화면에 추가**로 앱처럼 설치합니다. iPhone은 사파리에서 해야 푸시를 쓸 수 있습니다.
3. 위치 권한을 허용합니다.
4. `https://<주소>/health`에서 `{"ok": true, "ai_provider": "gemini>openrouter>mock", ...}`가 나오는지 확인합니다. `ai_provider`가 `mock`뿐이면 AI 키가 안 읽힌 것입니다(`backend/.env` 확인).

## 5. 업데이트·운영

```bash
git pull
docker compose -f docker-compose.prod.yml up -d --build   # 프런트·백엔드 다시 빌드
docker compose -f docker-compose.prod.yml logs -f backend  # 로그
```

- 프런트를 새로 배포해도 폰이 옛 화면을 붙들고 있으면, 앱을 완전히 닫았다 열거나 새로고침하세요(서비스워커).
- `/docs`(API 문서)는 운영 모드에서 꺼집니다.
- SQLite 단일 파일이라 백엔드는 프로세스 하나로 돌립니다. 동시 사용자가 많아지면 PostgreSQL이 필요합니다(`DATABASE_URL`, README의 마이그레이션 주의 참고).

## 알려진 한계

- 이 구성은 Docker가 없는 환경에서 작성해 서버에서 실제로 실행해 보지 못했습니다(프런트 빌드와 compose YAML 파싱만 확인). 처음 올릴 때 한 번 시험하고, 막히면 `docker compose ... logs`를 보세요.
- 로그인이 없습니다. 주소를 아는 사람은 누구나 열 수 있고, 기기 ID(브라우저 localStorage)로만 사용자를 구분합니다. 공개 전에 사용자 인증을 정해야 합니다(`docs/OWNER_TODO.md` 3번 항목).
- 무료 LLM 키는 한도·모델명이 수시로 바뀝니다. 한도에 걸리면 다음 제공사, 마지막은 키워드 기반 mock 답변으로 내려갑니다.
- 폰 알림은 `VAPID_*` 키 없이는 앱이 열려 있을 때만 옵니다.
