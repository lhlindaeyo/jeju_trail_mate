# 직접 해야 할 일 (Owner TODO)

코드가 대신할 수 없는 계정·키·권리 확인 작업입니다. 위에서부터 중요도 순입니다.

## 1. 바로 (로컬에서 AI까지 써 보기)

- [ ] **Anthropic API 키 발급** — https://console.anthropic.com → API Keys. `backend/.env`의 `ANTHROPIC_API_KEY`에 넣습니다. 기본 모델은 `claude-opus-5-5`(effort `medium`). 비용을 줄이려면 `LLM_MODEL=claude-sonnet-5-5`.
- [ ] **기상청 단기예보 키** — https://www.data.go.kr 에서 “기상청_단기예보 ((구)_동네예보) 조회서비스” 활용 신청 → 일반 인증키(Decoding)를 `KMA_SERVICE_KEY`에.
- [ ] **관광 POI 키** — 같은 포털에서 “한국관광공사_국문 관광정보 서비스_GW”(KorService2) 활용 신청 → `TOUR_API_KEY`에. 승인 후 응답 형식이 코드(`services/poi_service.py`)와 맞는지 한 번 확인해 주세요.
- [ ] `backend/.env`의 `JWT_SECRET`, `ADMIN_TOKEN`을 긴 임의 문자열로 바꾸기.

## 2. 출시 전 반드시 (명세 §02·§17 리스크)

- [ ] **제주올레 데이터 이용 조건 문의** — 코스 정보·GPX·공지·사진·상표 사용 가능 여부와 조건을 사단법인 제주올레에 서면으로 확인. 허가 전에는 링크 연결과 수동 관리 데이터만 사용합니다.
- [ ] **시드 코스 데이터 검증** — `backend/app/data/seed_routes.json`은 미검증 값·근사 좌표입니다. 공식 사이트와 대조 후 관리자 API(`/v1/admin/routes/{코스}/verify`)로 확정하세요.
- [ ] **공지 확인 운영 절차** — 매일 공식 사이트의 우회·통제 공지를 확인해 등록(`/notices`)하거나 “확인함”(`/notices-checked`)으로 표시할 담당자/주기 정하기. 24시간이 지나면 앱이 “공지 확인 필요”로 바꿉니다.
- [ ] **지도 SDK 선정** — 카카오맵/네이버 지도: 도보 경로, 타일 캐싱·오프라인, 상업적 이용, 비용 약관 비교 후 키 발급. 현재는 개발용 OSM 타일.
- [ ] **개인정보 처리방침·이용약관** 작성(위치정보 포함 시 위치정보사업/위치기반서비스사업 신고 대상인지 법률 검토).
- [ ] **운영 인프라** — Supabase(Auth/Storage/Postgres) 또는 동등 서비스 계정, 도메인(HTTPS), 백업 보존 기간 정책.
- [ ] **푸시** — Firebase 프로젝트(FCM) / Apple 개발자 계정(APNs). 네이티브 앱 전환 시 필요.

## 3. 현장 검증

- [ ] 1~2개 코스로 실제 걷기 테스트: 배터리, GPS 누락, 통신 끊김, 알림 빈도, 일기 품질, PDF 결과물.
- [ ] 5~10명 사용성 테스트로 지표 기준선 측정(명세 §16).
- [ ] 백그라운드 GPS가 꼭 필요하면 React Native(Expo) 전환 결정.

## 4. GitHub

- [ ] 저장소: https://github.com/lhlindaeyo/jeju_trail_mate — 커밋을 푸시해 두었습니다(푸시가 실패했다면 아래 명령을 직접 실행).

```bash
git push -u origin main
```
