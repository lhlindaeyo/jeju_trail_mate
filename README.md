# JEJU TRAIL MATE

제주의 길을 걷는 개별 여행자를 위한 개인화 AI 트레킹 에이전트.
**계획(PLAN) → 걷기(WALK) → 기록(REMEMBER) → 완주 디지털 책**을 하나로 잇습니다.

- 기획·개발 명세서 v1.0: [docs/development_spec.html](docs/development_spec.html)
- **개발계획 v1.2**(하영올레 편입, 제주데이터허브 데이터, UI·디자인 명세)가 현재 기준입니다. 요약은 아래 "v1.2 반영 내용"을 보세요.
- 직접 해야 할 일: [docs/OWNER_TODO.md](docs/OWNER_TODO.md) · 원본 데이터 두는 곳: [data/raw/README.md](data/raw/README.md)

> JEJU TRAIL MATE는 사단법인 제주올레·서귀포시가 운영·보증하는 서비스가 아닙니다. 이 앱은 구조·구급 서비스가 아닙니다.

## v1.2 반영 내용

| 항목 | 구현 |
|---|---|
| 트레일 2유형 | `jeju_olle`(사단법인 제주올레, 27코스, 경로 좌표 없음) / `hayoung_olle`(서귀포시, 1·2·3코스, SHP 경로·경유지). 화면·AI 답변·책 통계에서 항상 구분하고, 하영올레는 제주올레 완주에 합산하지 않습니다. |
| 데이터 레지스트리 | `data_sources` 테이블: 출처·기준일·이용 조건·파일 해시. 앱 메뉴 → **데이터 출처** 화면. 모든 코스 카드에 기준일, "최신 확인 필요" 배지(F-10). |
| 데이터 파이프라인 | `python -m app.data_import` — 올레코스현황 CSV, 하영올레 SHP(6개 파일 검증 → `.prj` 좌표계 → WGS84 변환 → 경로·경유지 적재), 휴게음식점 CSV(폐업 제외, 좌표 없으면 거리 추천 제외). |
| DB | `trail_routes`(trail_type, operator, course_no, 소요시간 low/high, has_geometry, path, source_id), `route_waypoints`, `route_segments`, `poi_places`, `waypoint_visits`, `daily_schedules.extra_route_ids`(하영올레 묶음) |
| 진행률·체크인 | 하영올레: GPS를 경로에 맞춰 진행률 계산(순환 코스 대응), 경유지 반경 40m 체크인, 경로 이탈 50m 이상이 2분 넘게 이어지면 "위치 확인" 안내(정확도 30m보다 나쁘면 보류). 제주올레: 거리 기록과 종점 근접 확인. |
| 일정 | 제주올레: 전날 종점에서 시작하는 코스를 이어 붙임. 하영올레: 반나절(1)·하루(1+2)·여유(3) 묶음 추천, 시간이 맞으면 하루에 묶음. |
| AI 도구 | `list_routes(trail_type)`, `get_waypoints`, `get_next_waypoint`, `find_places_near`(poi_places + 출처·기준일), 기존 도구 유지. 두 길을 섞지 않도록 시스템 프롬프트에 명시. |
| 알림 | 경유지 도착(사진 남기기), 경로 이탈, 종점 근처 + 기존(지연·일몰·공지·비·기록 제안). |
| 일기 | **사진 1장 + 글 최대 3줄(150자)**, 사진은 800px 이하로 줄여 저장, 경유지 연결(사용자 선택). 하루 요약 초안에는 "지나온 곳"을 사실로만 표기. |
| 디지털 책 | **여행 마지막 날 + 계획 코스 완주(진행률 80%)** + 저장한 일기 1편 이상일 때만 생성(서버에서도 검사). 여정 지도에 하영올레 경로(점선)와 GPS 기록(실선)을 구분 표시. |
| UI | 하단 검정 알약 네비(홈·코스탐색·AI동행자·일기작성, 흰 원 250ms 이동, URL 해시 동기화), 전체 화면 지도 홈(완료 구간 노랑 실선·남은 구간 흰 점선, 내 위치 펄스, 미니맵·다음 포인트·요약 카드), 청록 `#0F8F9B` 포인트·Pretendard. 메뉴(☰)에서 여행 일정·설정·동의·데이터 출처. |

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
cp .env.example .env
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

테스트: `cd backend && .venv/Scripts/python -m pytest -q`

> DB 구조가 v1.2에서 바뀌었습니다. 예전 개발용 `jeju_trail_mate.db`가 있으면 지우고 다시 실행하세요(운영에서는 Alembic 마이그레이션 필요).

### 다운로드한 데이터 적재

파일을 `data/raw/` 아래 정해진 폴더에 두고(자세한 위치: [data/raw/README.md](data/raw/README.md)):

```bash
cd backend
.venv/Scripts/python -m app.data_import hayoung ../data/raw/hayoung_olle --inspect
.venv/Scripts/python -m app.data_import all ../data/raw --license "공공누리 제1유형"
```

`--inspect`는 SHP의 필드·좌표계·지오메트리 종류만 출력합니다. 경유지 코드 필드를 자동으로 못 찾으면 `--code-field`, `--name-field`로 지정합니다. 하영올레 SHP를 적재하기 전까지는 하영올레의 거리·지도 경로·진행률이 "계산 예정"으로 표시됩니다(값을 지어내지 않음).

### 키가 없을 때

- `ANTHROPIC_API_KEY` 없음 → 오프라인 모드(키워드로 도구 조회 결과만 답함).
- `KMA_SERVICE_KEY` 없음 → "예보 확인 필요", 비 알림 꺼짐.
- 장소 CSV 미적재 → "검증된 장소 데이터가 아직 연결되지 않았어요".

## 알려진 제약

- 시드 데이터: 제주올레 27코스는 UI 프로토타입 표(올레코스현황 2020-01-14 기준)에서 옮긴 값이고, 하영올레는 경유지 이름·순서만 있습니다. 원본을 적재하면 교체됩니다. 경유지 코드 규칙(S/F/순번)은 추정이라 실제 SHP로 확인해야 합니다.
- 지도는 개발용 OpenStreetMap 타일입니다. 명세의 "입체 지도"(DEM/지도 SDK 3D 또는 일러스트)는 결정이 필요합니다.
- 체크인 반경 40m, 이탈 50m/2분, 완주 80%는 초안 값이며 하영올레 현장 테스트로 조정합니다(`backend/app/services/tracking_service.py`).
- 브라우저 PWA는 화면이 꺼지면 GPS가 멈출 수 있고, 알림은 앱이 열려 있을 때만 옵니다(백그라운드 푸시는 FCM/네이티브 전환 시).
