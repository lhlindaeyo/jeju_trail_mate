# JEJU TRAIL MATE

제주올레길을 걷는 개별 여행자를 위한 개인화 AI 트레킹 에이전트.
**계획(PLAN) → 걷기(WALK) → 기록(REMEMBER) → 완주 디지털 책**을 하나로 잇습니다.

기획·개발 명세서: [docs/development_spec.html](docs/development_spec.html) · 직접 해야 할 일: [docs/OWNER_TODO.md](docs/OWNER_TODO.md)

> JEJU TRAIL MATE는 사단법인 제주올레가 운영·보증하는 서비스가 아닙니다. 이 앱은 구조·구급 서비스가 아닙니다.

## 구성

| 영역 | 위치 | 내용 |
|---|---|---|
| Backend API | `backend/app` | Python FastAPI, SQLAlchemy(SQLite 개발 / PostgreSQL 운영) |
| AI Agent | `backend/app/agent` | 단일 오케스트레이터 + 도구 호출(Claude), 승인 필요한 제안, 응급 정책 |
| 능동 알림 | `backend/app/rules/engine.py` | 규칙 기반(LLM 호출 없음), 중복 억제·“오늘은 그만”·안전 우선 |
| 일기·책 | `backend/app/services/journal_service.py`, `book_service.py`, `templates/book.html` | 사용자 기록만 쓰는 초안, HTML→PDF 책 |
| Mobile web (PWA) | `frontend/` | React + Vite, Leaflet 지도, GPS 세션·오프라인 큐, 음성 메모 |

### 명세 → 구현 대응 (MVP P0)

| ID | 기능 | 구현 |
|---|---|---|
| F-01 | 프로필·선호·동의 | `PATCH /v1/me/preferences` — 위치·마이크·사진·AI 처리·개인화 기억 동의 각각 분리, 철회 가능 |
| F-02 | 코스 탐색 | `GET /v1/routes` — 출처·확인일·“최신 확인 필요” 배지, 관리자 검증 API |
| F-03 | 개인화 일정 | 하루 거리·종료 시각·**일몰**·관심사로 초안 → **승인해야 확정**, 변경은 새 revision |
| F-04 | 트레킹 세션·GPS | 시작/일시정지/재개/종료, 정확도·점프 필터, 권한 거부 시 수동 기록 |
| F-05 | AI 동행자 | `POST /v1/agent/chat` — 도구로만 사실 확인, 출처 표시, 일정 변경은 제안 카드 |
| F-06 | 능동 알림 | 일정 지연(GPS 양호 시만), 휴식, 종료시각/일몰, 공식 공지, 공지 미확인, 비 예보, 기록 제안 |
| F-07/08 | 일기·하루 요약 | 텍스트/음성/사진, AI 초안은 저장 전 편집, 기록 없으면 생성 거부 |
| F-09 | 디지털 책 | `draft → generating → preview_ready → user_approved → exported`, 빈 날짜는 비워 둠 |
| §13 | 보안·개인정보 | 소유권 검사(404), 비공개 저장소 + 서명 URL(5분), 데이터 내보내기·계정 삭제, 원시 GPS 30일 보존 |

## 로컬 실행

필요: Python 3.11+, Node 20+.

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt      # macOS/Linux: .venv/bin/pip
cp .env.example .env                                 # 키는 비워 둬도 동작(오프라인 모드)
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 (API는 /v1 프록시)
```

API 문서: http://localhost:8000/docs · 테스트: `cd backend && .venv/Scripts/python -m pytest -q`

### 키가 없을 때의 동작

- `ANTHROPIC_API_KEY` 없음 → **오프라인 모드**: 동행자는 키워드로 도구(코스·공지·날씨·도착 예상·장소·일기)를 호출해 확인된 결과만 답합니다. 일기 초안은 사용자 문장을 그대로 모읍니다.
- `KMA_SERVICE_KEY` 없음 → “예보 확인 필요”로 안내, 날씨 알림 비활성.
- `TOUR_API_KEY` 없음 → 주변 장소를 지어내지 않고 “확인된 장소 정보 없음”.

### 관리자: 코스·공지 검증

```bash
curl -X POST localhost:8000/v1/admin/routes/7/verify -H "X-Admin-Token: $ADMIN_TOKEN" -H "content-type: application/json" -d '{"distance_km": 17.6}'
curl -X POST localhost:8000/v1/admin/routes/7/notices-checked -H "X-Admin-Token: $ADMIN_TOKEN"
```

공지 등록은 `POST /v1/admin/routes/{코스}/notices` (title, notice_type=detour|closure|safety|info, source_url 필수).

## 설계 원칙 (명세 §01·§07)

- 시간·거리·일몰·진행률은 **코드**로 계산, LLM은 의도 파악·설명·초안만.
- 일정 변경·책 내보내기·삭제는 사용자 승인 후 실행.
- 확인되지 않은 장소·영업시간·교통·공지 상태를 사실처럼 말하지 않음.
- LLM에는 최소 맥락(요약된 진행 상황)만 전달, 원시 GPS 로그는 보내지 않음.
- 위급 키워드는 LLM을 거치지 않고 즉시 119/112 안내.

## 알려진 제약 / 다음 단계

- 시드 코스 데이터 6개(1·6·7·10·14·17코스)는 **미검증 값·근사 좌표**입니다. 관리자 검증 전까지 앱에 “최신 확인 필요”로 표시됩니다. GPX 경로는 이용 허가 전까지 포함하지 않습니다.
- 지도 타일은 개발용 OpenStreetMap입니다. 운영 전 카카오/네이버 지도 SDK 등으로 교체하세요.
- 알림은 앱이 열려 있을 때 60초 폴링 + 브라우저 알림입니다. 백그라운드 푸시는 FCM/Web Push 연동이 필요합니다.
- 브라우저 PWA는 화면 꺼짐/백그라운드에서 GPS가 멈출 수 있습니다. 현장 테스트 단계에서 React Native(Expo) 전환을 검토하세요(명세 §08).
- 서버 PDF는 WeasyPrint 설치 시 사용, 없으면 브라우저 인쇄→PDF로 저장합니다.
- DB 마이그레이션(Alembic), PostGIS, Supabase Auth/Storage 어댑터는 운영 단계 작업입니다.
