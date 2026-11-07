# JEJU TRAIL MATE

제주의 길을 걷는 개별 여행자를 위한 개인화 AI 트레킹 에이전트.
**계획(PLAN) → 걷기(WALK) → 앞길 브리핑(AHEAD)** 에 집중합니다. 일기·사진·디지털 책 기능은 없앴고, 걷는 동안 필요한 판단(일몰 전 도착, 앞쪽 쉴 곳, 날씨)을 한 화면에 보여 줍니다.

- 기획·개발 명세서 v1.0: [docs/development_spec.html](docs/development_spec.html)
- **개발계획 v1.2**(하영올레 편입, 제주데이터허브 데이터, UI·디자인 명세)가 현재 기준입니다. 요약은 아래 "v1.2 반영 내용"을 보세요.
- 직접 해야 할 일: [docs/OWNER_TODO.md](docs/OWNER_TODO.md) · 원본 데이터 두는 곳: [data/raw/README.md](data/raw/README.md)

> JEJU TRAIL MATE는 사단법인 제주올레·서귀포시가 운영·보증하는 서비스가 아닙니다. 이 앱은 구조·구급 서비스가 아닙니다.

## v1.2 반영 내용

| 항목 | 구현 |
|---|---|
| 트레일 2유형 | `jeju_olle`(사단법인 제주올레, 27코스, 경로 좌표 없음) / `hayoung_olle`(서귀포시, 1·2·3코스, SHP 경로·경유지). 화면·AI 답변에서 항상 구분하고, 하영올레는 제주올레 완주에 합산하지 않습니다. |
| 데이터 레지스트리 | `data_sources` 테이블: 출처·기준일·이용 조건·파일 해시. 앱 메뉴 → **데이터 출처** 화면. 모든 코스 카드에 기준일, "최신 확인 필요" 배지(F-10). |
| 데이터 파이프라인 | `python -m app.data_import` — 올레코스현황 CSV, 하영올레 SHP(6개 파일 검증 → `.prj` 좌표계 → WGS84 변환 → 경로·경유지 적재), 휴게음식점 CSV(폐업 제외, 좌표 없으면 거리 추천 제외). |
| DB | `trail_routes`(trail_type, operator, course_no, 소요시간 low/high, has_geometry, path, source_id), `route_waypoints`, `route_segments`, `poi_places`, `waypoint_visits`, `daily_schedules.extra_route_ids`(하영올레 묶음) |
| 진행률·체크인 | 하영올레: GPS를 경로에 맞춰 진행률 계산(순환 코스 대응), 경유지 반경 40m 체크인, 경로 이탈 50m 이상이 2분 넘게 이어지면 "위치 확인" 안내(정확도 30m보다 나쁘면 보류). 제주올레: 거리 기록과 종점 근접 확인. |
| 일정 | 제주올레: 전날 종점에서 시작하는 코스를 이어 붙임. 하영올레: 반나절(1)·하루(1+2)·여유(3) 묶음 추천, 시간이 맞으면 하루에 묶음. |
| AI 도구 | `list_routes(trail_type)`, `get_waypoints`, `get_next_waypoint`, `find_places_near`(poi_places + 출처·기준일), 기존 도구 유지. 두 길을 섞지 않도록 시스템 프롬프트에 명시. |
| 알림 | 경유지 도착(앞길 보기), 경로 이탈, 종점 근처 + 기존(지연·일몰·공지·비). |
| 앞길 브리핑 | `GET /v1/brief` (AI 도구 `get_trail_brief`와 같은 계산). 저장하는 것 없이 요청 때마다 만듭니다. ① **일몰 대비 도착**: 걷는 중이면 도착 예상과 일몰 여유(분), 출발 전이면 "늦어도 ○○시까지 출발"(해 지기 30분 전 마치기 기준). 여유 30분 미만 '빠듯함', 일몰 이후 도착은 '늦음'. ② **앞쪽 화장실·식당·쉼터**: 경로 좌표가 있는 하영올레는 앞 5km·길에서 300m 안쪽을 '앞 ○○m'로, 제주올레는 경로 좌표가 없어 내 주변 2km를 거리순으로(영업 여부는 실시간이 아니라 '확인 필요', 출처·기준일 표기). ③ **하루(24시간) 날씨**: 기상청 단기예보에서 비 시작 시각·강풍을 요약(키가 없으면 '확인 불가'). ④ **다음 경유지**(걷는 중이면 남은 거리, 출발 전이면 첫 경유지) ⑤ **공지**(코스의 활성 우회·통제 공지와 마지막 확인 시각). 화면은 1분마다 갱신하고, 오프라인에서 이전 응답이 나오면 몇 분 전 정보인지 표시합니다. |
| UI | 하단 검정 알약 네비(홈·코스탐색·AI동행자·앞길브리핑, 흰 원 250ms 이동, URL 해시 동기화), 전체 화면 지도 홈(완료 구간 노랑 실선·남은 구간 흰 점선, 내 위치 펄스, 미니맵·다음 포인트·요약 카드), 청록 `#0F8F9B` 포인트·Pretendard. 메뉴(☰)에서 여행 일정·설정·동의·데이터 출처. |

## 구성

| 영역 | 위치 |
|---|---|
| Backend API (FastAPI, SQLAlchemy) | `backend/app` |
| 데이터 파이프라인 | `backend/app/data_import` |
| AI 오케스트레이터·도구 | `backend/app/agent` |
| 규칙 기반 알림 | `backend/app/rules/engine.py` |
| 모바일 웹(PWA, React + Vite + Leaflet) | `frontend/` |

## 로컬 실행

필요: Python 3.11+, Node 20+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

`backend/.env`는 git에 올라가지 않는 파일이라 직접 만듭니다(없어도 실행은 되고 키가 필요한 기능만 꺼집니다). 최소 예:

```ini
APP_ENV=development
ADMIN_TOKEN=<긴 임의 문자열>
LLM_CHAIN=ollama,mock          # AI: Ollama(qwen3:4b) → 없으면 오프라인 mock
KMA_SERVICE_KEY=               # 공공데이터포털 일반 인증키: 기상청 단기예보
SBIZ_SERVICE_KEY=              # 같은 포털: 소상공인 상가정보 (sbiz-fetch)
JEJU_DATAHUB_APPKEY=           # 제주데이터허브 프로젝트키
GEOCODER_CHAIN=local,stores
```

그 밖의 설정(Gemini·Groq 등 LLM, 브이월드·카카오, DB 주소 등)은 `backend/app/config.py`의 필드 이름을 대문자로 쓰면 됩니다.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

**한 번에 실행**: 위 설치(venv·`pip install`)를 한 번 해둔 뒤에는 저장소 루트에서 아래 한 줄이면 백엔드(8000)와 프런트(5173)가 같이 뜹니다. `frontend/node_modules`가 없으면 자동으로 `npm install`도 합니다. Ctrl+C로 둘 다 종료됩니다.

```bash
npm run dev
```

테스트: `cd backend && .venv/Scripts/python -m pytest -q`

> DB 구조가 v1.2에서 바뀌었습니다. 예전 개발용 `jeju_trail_mate.db`가 있으면 지우고 다시 실행하세요(운영에서는 Alembic 마이그레이션 필요).

### 데이터 적재

원본 위치와 각 API 사용법은 [data/raw/README.md](data/raw/README.md)에 있습니다.

```bash
cd backend
.venv/Scripts/python -m app.data_import all ../data/raw --license "공공누리 제1유형"
```

| 단계 | 하는 일 |
|---|---|
| `olle` | 올레코스현황 CSV(코스별·코스명·거리·소요시간정보·시종점정보·데이터기준일자) → 29개 코스, (A)/(B) 분리, 목록에서 빠진 옛 코스 정리 |
| `hayoung` | 코스별 SHP(조각난 다중 선)를 이어 붙여 걷는 순서의 순환 경로로 복원: 중복 선 제거, 새섬 같은 왕복 구간은 두 번, 공시 거리와 비교 |
| `poi` | 공공와이파이 관광지 AP CSV(주소만) |
| `goodprice` | 서귀포시 착한가격업소 OpenAPI(키 불필요) |
| `wifi-api` | 제주데이터허브 공공와이파이 OpenAPI(`JEJU_DATAHUB_APPKEY`, 위도·경도 포함) |
| `locate-waypoints` | 좌표가 있는 장소 이름과 경유지 이름을 맞춰, 경로 150m 안이면 경로 위로 붙여 경유지 체크인을 켬. 진행 방향도 검증 |

### 키가 없을 때

- LLM 제공사는 `LLM_CHAIN`(예: `ollama,gemini,mock`)으로 순서를 정하고, 한도·장애 시 다음으로 넘어갑니다. 키가 없는 항목은 건너뛰고, API 키는 쉼표로 여러 개 넣으면 돌려 씁니다. 로컬 모델(Ollama)은 `OLLAMA_MODEL`, 센터 PC용은 `docker-compose.yml`. 설정 이름은 `backend/app/config.py`.
- 아무것도 없으면(`ANTHROPIC_API_KEY`·`LLM_CHAIN` 없음) → 오프라인 모드(키워드로 도구 조회 결과만 답함).
- `KMA_SERVICE_KEY` 없음 → "예보 확인 필요", 비 알림 꺼짐.
- 주소 → 좌표 변환기(`GEOCODER_CHAIN`: `local`=위치정보 요약DB, `stores`=상가정보·인허가 업소 목록과 상호·주소 매칭, `vworld`, `kakao`)가 하나도 없으면 착한가격업소 등 주소만 있는 장소는 "주소 기준 목록"으로만 안내. 브이월드·카카오(기본 설정)는 약관상 결과 저장이 안 되어 보고서의 "제안"으로만 나오고 DB에는 쓰지 않습니다. 확인: `python -m app.data_import geocode --address "서귀포시 중앙로 62번길 5"`.
- `JEJU_DATAHUB_APPKEY` 없음 → 공공와이파이 좌표가 없어 하영올레 경유지 도착 알림은 출발·도착점만.

## 알려진 제약

- 시드 데이터: 제주올레 27코스는 UI 프로토타입 표(올레코스현황 2020-01-14 기준)에서 옮긴 값이고, 하영올레는 경유지 이름·순서만 있습니다. 원본을 적재하면 교체됩니다. 경유지 코드 규칙(S/F/순번)은 추정이라 실제 SHP로 확인해야 합니다.
- 지도는 개발용 OpenStreetMap 타일입니다. 명세의 "입체 지도"(DEM/지도 SDK 3D 또는 일러스트)는 결정이 필요합니다.
- 체크인 반경 40m, 이탈 50m/2분, 완주 80%는 초안 값이며 하영올레 현장 테스트로 조정합니다(`backend/app/services/tracking_service.py`).
- 브라우저 PWA는 화면이 꺼지면 GPS가 멈출 수 있고, 알림은 앱이 열려 있을 때만 옵니다(백그라운드 푸시는 FCM/네이티브 전환 시).
