# 서버 배포 (폰에서 쓰기)

AI는 Gemini·OpenRouter 같은 클라우드 API를 쓰므로 서버에 GPU도 Ollama도 필요 없습니다. 작은 VPS(RAM 1~2GB)면 됩니다.

```
폰 ──https──> web(Caddy) ─┬─ 정적 파일(프런트 dist)
                          └─ /v1, /health ──> backend(FastAPI + SQLite)
```

프런트가 API를 `/v1/...` 상대경로로 부르기 때문에 두 가지가 한 주소에서 서빙돼야 하고, 그걸 Caddy가 맡습니다.
폰의 GPS·홈 화면 설치·푸시는 **HTTPS에서만** 동작합니다.

구성 파일: `docker-compose.prod.yml`, `frontend/Dockerfile`, `frontend/Caddyfile`, `deploy/backend.env.example`.

## 0. 설치할 것

서버에는 **Docker 하나만** 설치하면 됩니다. Python, Node, Caddy, Ollama는 컨테이너 안에서 돌아가므로 따로 설치하지 않습니다.

| 항목 | 내용 |
|---|---|
| 서버 | 리눅스(Ubuntu 22.04·24.04 권장), RAM 2GB 이상 권장(프런트 빌드가 컨테이너 안에서 돕니다), 디스크 5GB 이상 |
| 인터넷 | 필요. Docker 이미지를 내려받고, 실행 중에는 AI·날씨 API를 호출합니다 |
| 포트 | 80, 443 (HTTPS 인증서 발급과 접속). 클라우드라면 보안 그룹·방화벽에서 열어 줍니다 |
| Docker Engine + Compose 플러그인 | 아래 명령으로 설치 |
| `unzip` | zip으로 받은 경우 풀기용 |
| `git` | zip으로 받으면 필요 없습니다 |

```bash
sudo apt update && sudo apt install -y unzip
curl -fsSL https://get.docker.com | sudo sh       # Docker Engine + Compose 플러그인
sudo usermod -aG docker $USER                      # 이후 로그아웃·로그인 한 번
docker compose version                             # 버전이 나오면 설치 완료
```

받은 `jeju_trail_mate_server.zip`은 서버에 올려서 풉니다.

```bash
scp jeju_trail_mate_server.zip <서버>:~/
ssh <서버>
unzip jeju_trail_mate_server.zip -d jeju_trail_mate && cd jeju_trail_mate
```

주소를 정하는 방법은 둘 중 하나입니다.
  - **도메인이 있다**(또는 DuckDNS 같은 무료 도메인): DNS A 레코드를 서버 IP로 향하게 하고, 서버의 80·443 포트를 엽니다.
  - **도메인이 없다**: 3-B의 Cloudflare 임시 터널로 먼저 시험합니다(주소는 재시작하면 바뀝니다).

## 1. 설정 파일 채우기

zip에는 `backend/.env`가 **키 값만 비어 있는 상태**로 들어 있습니다(변수 이름과 AI 순서·모델명 같은 설정은 그대로). 서버에서 열어 값을 채웁니다.

```bash
nano backend/.env
```

| 변수 | 채울 값 |
|---|---|
| `ADMIN_TOKEN` | 긴 임의 문자열 (예: `openssl rand -hex 32`) |
| `GEMINI_API_KEY`, `OPENROUTER_API_KEY` | AI 키 (둘 중 하나만 있어도 됨) |
| `KMA_SERVICE_KEY` | 기상청 단기예보 키 (공공데이터포털) |
| `SBIZ_SERVICE_KEY` | 공공데이터포털 키. 한국관광공사 TourAPI의 위치 기반 조회에도 쓰임 |
| 나머지(`JEJU_DATAHUB_APPKEY`, `KAKAO_*`, `NAVER_*`) | 데이터를 서버에서 새로 적재할 때만 필요. 비워 둬도 됩니다 |

키를 어디서 받는지는 README의 "5. 사용하는 API와 키"에 있습니다. `APP_ENV`·`CORS_ORIGINS` 같은 값은 compose가 덮어쓰거나 쓰이지 않습니다.

## 2. 데이터베이스

코스·경로·경유지·장소 데이터는 DB 파일에 적재돼 있고, zip의 `server_data/jeju_trail_mate.db`에 들어 있어서 **따로 할 일은 없습니다**. 실행하면 이 파일을 그대로 씁니다.

- 이 DB는 개발 PC의 파일에서 **여행·걷기 기록·GPS 점·알림·사용자 정보를 모두 지운 복사본**입니다(코스·경로·장소·출처 정보만 남김).
- 이 파일이 없으면 시드 데이터만 있는 빈 DB가 생기고, 하영올레 경로선 같은 적재 데이터는 없습니다.
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
docker compose -f docker-compose.prod.yml up -d --build   # 코드를 바꾼 뒤 프런트·백엔드 다시 빌드
docker compose -f docker-compose.prod.yml logs -f backend  # 로그
docker compose -f docker-compose.prod.yml down            # 중지 (데이터는 server_data/ 에 남음)
```

새 zip을 받으면 **`backend/.env`와 `server_data/`는 덮어쓰지 말고**(키와 서버에 쌓인 데이터가 지워집니다) 나머지만 풀어서 `up -d --build`를 다시 실행하세요.

```bash
unzip -o 새파일.zip -x 'backend/.env' 'server_data/*' -d jeju_trail_mate
```

- 프런트를 새로 배포해도 폰이 옛 화면을 붙들고 있으면, 앱을 완전히 닫았다 열거나 새로고침하세요(서비스워커).
- `/docs`(API 문서)는 운영 모드에서 꺼집니다.
- SQLite 단일 파일이라 백엔드는 프로세스 하나로 돌립니다. 동시 사용자가 많아지면 PostgreSQL이 필요합니다(`DATABASE_URL`). 운영 DB의 구조가 바뀌면 마이그레이션(Alembic)이 필요한데, 아직 준비돼 있지 않습니다.

## 알려진 한계

- 이 구성은 Docker가 없는 환경에서 작성해 서버에서 실제로 실행해 보지 못했습니다(프런트 빌드와 compose YAML 파싱만 확인). 처음 올릴 때 한 번 시험하고, 막히면 `docker compose ... logs`를 보세요.
- 로그인이 없습니다. 주소를 아는 사람은 누구나 열 수 있고, 기기 ID(브라우저 localStorage)로만 사용자를 구분합니다. 공개 전에 사용자 인증을 정해야 합니다(`docs/OWNER_TODO.md` 3번 항목).
- 무료 LLM 키는 한도·모델명이 수시로 바뀝니다. 한도에 걸리면 다음 제공사, 마지막은 키워드 기반 mock 답변으로 내려갑니다.
- 폰 알림은 `VAPID_*` 키 없이는 앱이 열려 있을 때만 옵니다. 앱을 닫았을 때도 알림을 받으려면 `deploy/backend.env.example`의 `VAPID_*` 3줄을 `backend/.env`에 추가하고, 키는 `python -m app.push keygen`으로 만듭니다(서버에서는 `docker compose -f docker-compose.prod.yml run --rm backend python -m app.push keygen`).
