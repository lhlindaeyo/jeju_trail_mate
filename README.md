# JEJU TRAIL MATE

제주의 길을 걷는 개별 여행자를 위한 AI 트레킹 에이전트. **계획 → 걷기 → 앞길 브리핑**에 집중합니다.

> 사단법인 제주올레·서귀포시가 운영·보증하는 서비스가 아니며, 구조·구급 서비스도 아닙니다.

## 1. 에이전트 아키텍처

```
폰(PWA) ── /v1/agent/chat ──> Orchestrator ──> LLM 체인 ──> 도구 호출 ──> 서비스·DB
                                  │                              │
                                  └── 검증된 결과 + 출처 ◄───────┘
폰 ── GPS 점 ──> 규칙 엔진(LLM 없음) ──> 알림
```

| 구성 | 위치 | 역할 |
|---|---|---|
| Orchestrator | `backend/app/agent/orchestrator.py` | 채팅 한 턴을 처리: 응급 문구 감지 → AI 처리 동의 확인 → 최소 컨텍스트 생성 → LLM 호출 → 답변·출처·승인 제안 반환 |
| LLM 체인 | `backend/app/agent/llm.py` | 제공사를 `LLM_CHAIN`에 적은 순서대로 시도하고, 장애·한도면 다음으로 넘어감 |
| 도구 | `backend/app/agent/tools.py` | 모델이 호출하는 13개 기능(아래 표). 요청한 사용자의 권한으로만 실행 |
| 시스템 프롬프트 | `backend/app/agent/prompts.py` | 도구 결과로 확인된 사실만 답하고, 한두 문장으로 결론부터 말하게 함 |
| 규칙 엔진 | `backend/app/rules/engine.py` | GPS 점이 들어올 때마다 규칙으로 알림을 만듦. 매번 LLM을 부르지 않음 |

**LLM 제공사** — `LLM_CHAIN`에 쉼표로 적습니다(예: `gemini,openrouter,mock`).

| 이름 | 실행 위치 | 특징 |
|---|---|---|
| `ollama` | 백엔드 서버(같은 PC·사설망) | 대화가 서버 밖으로 나가지 않고 비용 없음. 기본 모델 `qwen3:4b` |
| `gemini`, `groq`, `openrouter`, `anthropic`, `openai`(OpenAI 호환 주소) | 외부 | 대화가 외부 서버로 전송됨 |
| `mock` | 백엔드 서버 | AI 없이 키워드로 도구를 호출해 결과만 답함. 다른 모델이 모두 안 될 때, 또는 설정된 모델이 없을 때 사용 |

- **동의 규칙**: 체인에 외부 제공사가 하나라도 있으면, 사용자가 설정에서 'AI 처리 동의'를 켠 경우에만 AI 대화를 실행합니다. 꺼져 있으면 Ollama를 포함해 어떤 모델도 호출하지 않고 안내만 표시합니다. 동의 없이 서버 안에서만 처리하려면 `LLM_CHAIN=ollama,mock`처럼 외부 제공사를 빼세요.
- **도구 호출 보완**: OpenAI 호환 모델이 도구를 부르지 않고 답하려 하면, 질문의 키워드를 보고 해당 도구를 대신 호출한 뒤 결과를 모델이 문장으로 정리하게 합니다. 키워드는 글자 포함 여부로 확인하므로 "비" 한 글자가 아니라 "비가", "비 올", "우산"처럼 여러 글자 표현만 등록해, "비빔밥"이 날씨 질문으로 처리되지 않습니다.
- **승인 구조**: 일정 변경은 에이전트가 직접 하지 않고 '제안'만 만듭니다. 사용자가 화면에서 승인해야 반영됩니다.
- **응급**: "살려줘", "119", "다쳤어" 같은 문구가 있으면 LLM을 거치지 않고 119·112 안내를 바로 돌려줍니다.
- **개인정보**: 모델에는 코스·일정·진행 요약만 보냅니다. 원시 GPS 기록은 보내지 않고, 위치 기록은 세션 종료 30일 뒤 삭제됩니다.

**규칙 엔진 알림**: 경유지 도착, 경로 이탈(50m 넘게 2분 이상), 종점 근처(150m), 일정 지연, 휴식 제안, 일몰 전 종료 위험, 공식 공지, 공지 최신 확인 필요, 비 예보(강수확률 60% 이상).

## 2. 에이전트 호출 도구

| 도구 | 하는 일 |
|---|---|
| `list_routes` | 코스 목록 조회. 제주올레(`jeju_olle`)와 하영올레(`hayoung_olle`)를 구분 |
| `get_route` | 코스 상세(거리, 소요 시간, 시점·종점, 경유지, 출처·기준일) |
| `get_waypoints` | 하영올레 경유지 목록 |
| `get_next_waypoint` | 진행 중인 세션의 다음 경유지, 남은 거리, 내 속도에 맞춘 다음 휴식·오늘 목표 지점 |
| `get_route_notices` | 코스의 공식 우회·통제·안전 공지와 마지막 확인 시각 |
| `get_weather` | 기상청 단기예보 |
| `find_places_near` | 주변 장소를 거리순으로 검색(`food`, `cafe`, `lodging`, `rest`, `toilet`, `wifi`). 출처·기준일 포함 |
| `find_tour_spots` | 대표 관광지 목록(가까운 순) |
| `get_tour_story` | 관광지 안내와 설화·유래. 한국관광공사 공식 안내문이 있으면 그 글을 사용 |
| `estimate_arrival` | 남은 거리, 예상 도착 시각, 일몰 시각 |
| `get_trip_plan` | 여행의 날짜별 승인된 일정과 승인 대기 초안 |
| `propose_schedule_change` | 일정 변경 **제안** 생성(사용자 승인 필요) |
| `get_trail_brief` | 앞길 브리핑 전체(일몰 대비 도착, 앞쪽 편의시설, 하루 날씨)를 한 번에 조회 |

## 3. 탭별 기능

하단의 알약 모양 네비게이션에 4개 탭이 있고, 왼쪽 위 ☰ 메뉴에서 일정·설정을 엽니다.

| 탭 | 기능 |
|---|---|
| **홈** | 전체 화면 코스 지도(완료 구간 노란 실선, 남은 구간 흰 점선, 내 위치 표시). 걷기 시작·일시정지·종료와 진행·남은 거리, 다음 경유지 카드. 걷는 동안 GPS를 기록해 진행률과 경유지 도착을 계산합니다. |
| **코스탐색** | 제주올레·하영올레 코스를 둘러보고 고릅니다(거리·소요 시간·출처). 고른 코스는 홈 지도에서 봅니다. 걷는 중에는 코스를 바꿀 수 없습니다. |
| **AI동행자** | 걷다가 궁금한 것을 묻는 채팅. 예시 질문 제공, 답변마다 출처 표시, 일정 변경 제안은 승인·나중에 버튼으로 처리. |
| **앞길브리핑** | 일몰 전 도착 여부(일몰·도착 예상·남은 거리·늦어도 출발할 시각), 앞쪽 화장실·식당·쉼터, 하루 날씨(비·강풍), 코스 공지, 최근 알림. 1분마다 갱신합니다. |

**☰ 메뉴**
- **여행 일정**: 여행 만들기, 날짜별 코스 초안·편집·등록, AI 일정 제안 승인·거절, 여행 삭제
- **설정·동의**: 언어(한국어·English), 하루 최대 거리·걷기 속도·출발·종료 시각·휴식 간격, 위치·AI 처리 동의, 알림 종류, 폰 알림(앱을 닫아도 수신)
- **가상 위치(시연 모드)**: 제주 밖에서도 코스를 걷는 것처럼 시험(걷기·빨리 걷기·위치 슬라이더·길 벗어나기)
- **처음부터 다시 체험하기**: 모든 기록과 설정을 지우고 첫 화면부터 다시 시작

## 4. 실행 방법

필요: Python 3.11+, Node 20+.

**① 설치 (처음 한 번)**

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
```

**② 설정**: `backend/.env`를 만듭니다(git에 올라가지 않음). 없어도 실행되고, 키가 필요한 기능만 꺼집니다.

```ini
ADMIN_TOKEN=<긴 임의 문자열>
LLM_CHAIN=gemini,openrouter,mock       # 사용할 AI 순서
GEMINI_API_KEY=
OPENROUTER_API_KEY=
KMA_SERVICE_KEY=                       # 기상청 단기예보(공공데이터포털). 없으면 날씨는 '확인 불가'
```

Ollama를 쓰려면 `LLM_CHAIN=ollama,mock`과 `OLLAMA_MODEL`을 지정합니다. 키를 어디서 받는지는 [5. 사용하는 API와 키](#5-사용하는-api와-키)를 보세요. 그 밖의 설정 이름은 `backend/app/config.py`에 있습니다.

**③ 데이터 적재 (처음 한 번)**: 코스·경로·장소 데이터를 DB에 넣습니다. 원본은 `data/raw/`에 둡니다.

```bash
cd backend
.venv/Scripts/python -m app.data_import all ../data/raw
```

**④ 실행**: 저장소 루트에서 한 줄이면 백엔드(8000)와 프런트(5173)가 같이 뜹니다.

```bash
npm run dev          # http://localhost:5173  (제주 밖에서는 http://localhost:5173/?demo=1)
```

**폰에서 써 보기 (임시 공개 주소)**: `cloudflared`가 필요합니다(`winget install --id Cloudflare.cloudflared -e`).

```bash
npm run share
```

출력되는 `https://....trycloudflare.com` 주소를 폰에서 엽니다(제주 밖이면 뒤에 `/?demo=1`). 이 창을 닫거나 PC가 잠들면 끊기고, 다시 실행하면 주소가 바뀝니다. 브라우저마다 따로 사용자가 만들어집니다.

**서버에 배포**: [docs/DEPLOY.md](docs/DEPLOY.md) (`docker-compose.prod.yml`)

**테스트**

```bash
cd backend && .venv/Scripts/python -m pytest -q
```

## 5. 사용하는 API와 키

키가 없어도 앱은 실행됩니다. 해당 기능만 꺼지고 대체 동작으로 넘어갑니다. **앱을 제대로 써 보려면 AI 키 1개와 기상청 키**가 있으면 됩니다.

### 앱이 실행 중에 호출하는 API

| API | 환경 변수 | 용도 | 발급처 | 키가 없으면 |
|---|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | AI동행자 대화 | [Google AI Studio](https://aistudio.google.com/apikey) | 체인의 다음 모델 → mock |
| OpenRouter | `OPENROUTER_API_KEY` | AI동행자 대화(Gemini 한도·장애 시 대체) | [openrouter.ai/keys](https://openrouter.ai/keys) | 체인의 다음 모델 → mock |
| Groq / Anthropic Claude (선택) | `GROQ_API_KEY` / `ANTHROPIC_API_KEY` | AI동행자 대화 | [console.groq.com](https://console.groq.com/keys) / [console.anthropic.com](https://console.anthropic.com) | 체인에서 건너뜀 |
| Ollama (선택) | 키 없음 (`OLLAMA_MODEL`) | 서버에서 직접 돌리는 로컬 모델 | [ollama.com](https://ollama.com) | — |
| 기상청 단기예보 (API허브) | `KMA_APIHUB_KEY` | 앞길브리핑·AI 날씨 답변·비 알림 (먼저 시도) | [apihub.kma.go.kr](https://apihub.kma.go.kr/) 에서 '단기예보 조회서비스' 신청 | 아래 공공데이터포털 키로 대체 |
| 기상청 단기예보 (공공데이터포털) | `KMA_SERVICE_KEY` | 위와 같음 (API허브 실패 시 대체) | [data.go.kr](https://www.data.go.kr/) 에서 '기상청_단기예보 조회서비스' 활용신청 | 둘 다 없으면 날씨 '확인 불가', 비 알림 꺼짐 |
| 한국관광공사 TourAPI 국문 관광정보 | 위의 공공데이터포털 키를 그대로 사용 (`SBIZ_SERVICE_KEY`가 있으면 그것) | "여기 이야기 해줘"에서 현재 위치 근처 관광지의 공식 안내문 조회 | [공공데이터포털 15101578](https://www.data.go.kr/data/15101578/openapi.do) 활용신청 | 내장된 대표 관광지 18곳만 안내 |
| OpenStreetMap 지도 타일 | 키 없음 | 지도 배경 (개발용 타일) | — | — |

공공데이터포털은 키 하나로 여러 API를 쓰지만, **API마다 활용신청을 따로** 해야 합니다.

### 데이터 적재 때만 호출하는 API

`python -m app.data_import ...` 단계에서만 쓰이고, 앱이 실행 중에는 호출하지 않습니다.

| API | 환경 변수 | 명령 | 용도 |
|---|---|---|---|
| 서귀포시 착한가격업소 OpenAPI | 키 없음 | `goodprice` (`all`에 포함) | 음식점·숙박 후보 |
| 소상공인 상가(상권)정보 | `SBIZ_SERVICE_KEY` (없으면 `KMA_SERVICE_KEY`) | `sbiz-fetch` | 착한가격업소 같은 주소만 있는 장소에 좌표 보강 |
| 한국관광공사 TourAPI | 공공데이터포털 키 | `tour-fetch` | 대표 관광지 18곳의 공식 개요·좌표 |
| 전국공중화장실표준데이터 (공공데이터포털 15012892) | 공공데이터포털 키 | `toilets-fetch` | 공중화장실. 활용신청 전에는 OSM 화장실 파일을 대신 사용 |
| OpenStreetMap Overpass | 키 없음 | `olle-fetch` | 제주올레 경로(route relation)와 보행로 |
| 제주올레 공식 홈페이지 | 키 없음 | `olle-fetch` | 코스 시작·종점 좌표 (사이트 콘텐츠 이용 허락 확인 필요) |
| Kakao 로컬 API (선택) | `KAKAO_REST_API_KEY` | `geocode` | 주소·장소명 → 좌표. 약관상 결과 저장은 불가라 제안으로만 표시 |
| 브이월드 Geocoder (선택) | `VWORLD_API_KEY`, `VWORLD_DOMAIN` | `geocode` | 위와 같음 |

### 파일로 내려받아 `data/raw/`에 두는 데이터

API가 아니라 사이트에서 직접 받습니다. 폴더별 설명은 [data/raw/README.md](data/raw/README.md)에 있습니다.

| 데이터 | 받는 곳 |
|---|---|
| 하영올레 전체 코스 SHP, 공공와이파이 관광지 AP | [제주데이터허브](https://www.jejudatahub.net/) |
| 올레코스현황 CSV | [공공데이터포털 15043496](https://www.data.go.kr/data/15043496/fileData.do) |
| (선택) 도로명주소 위치정보 요약DB(제주) | [juso.go.kr](http://www.juso.go.kr/externalLink/goUrl.do?menuId=DT03) — 심사 필요 |

### 직접 만드는 키

- **폰 알림(Web Push)**: 외부 API가 아니라 서버가 쓰는 키 쌍을 직접 만듭니다. `python -m app.push keygen`으로 만들어 `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`(`mailto:내이메일`)를 `.env`에 넣습니다. 없으면 앱이 열려 있을 때만 알림이 옵니다.
- **관리자 토큰**: `ADMIN_TOKEN`은 공지 관리용 `/v1/admin/*` 접근에 쓰는 임의의 긴 문자열입니다.
