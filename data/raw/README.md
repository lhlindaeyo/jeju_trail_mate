# 원본 데이터 보관 폴더 (data/raw)

직접 내려받은 원본 파일을 아래 폴더에 **파일명 그대로** 넣습니다. 이용 조건을 확인하기 전에는 저장소에 커밋되지 않습니다(.gitignore).

| 폴더 | 파일 | 현재 상태 (2026-11-05) |
|---|---|---|
| `hayoung_olle/` | 하영올레 SHP — 코스별 6개 파일 `hayoung-olle-course-N.{shp,shx,dbf,prj,cpg,qmd}` | 1·2·3코스 적재 완료 (8.84 / 5.87 / 6.23 km, 공시 8.4 / 5.8 / 6.2 km) |
| `olle_course_status/` | 제주특별자치도_올레코스현황 CSV | 있음 (29개 코스, 기준일 2026-07-31) |
| `olle_official/` | `road_endpoints.json` — 제주올레 공식 홈페이지 코스 페이지 스크립트에서 뽑은 코스별 시작·종점 좌표 (`python -m app.data_import olle-fetch`로 받음) | 29개 코스 (2026-11-16). 경로선은 없음. 사진·글·지도 이미지는 쓰지 않음 |
| `olle_osm/` | `overpass_olle_relations.json` — OpenStreetMap 제주올레 route relation (`olle-fetch`로 받음, ODbL) | 21개 relation 중 공식 거리 ±12%·시작·종점 500m 이내를 통과한 1·2·11·19·20코스만 적재 (2026-11-16) |
| `olle_map_images/` | `map_<코스>.jpg` — 공식 코스 안내지도 이미지 (`olle-fetch`가 받음). 경로 위치 추정용으로만 쓰고 앱에서 보여 주지 않음 | 29장 (2026-11-16) |
| `olle_traced/` | `traces.json`(안내지도에서 따라 그린 대략 선), `ways_<코스>.json`(그 주변 OSM 보행로), `overlays/`(검토용 이미지) | OSM 검증 경로가 없는 24개 코스 모두 적재 (예전 번호 15는 15-A 경로를 씀). 7-1·13·14·15-B·18은 지도가 단순화돼 공식 거리보다 15~27% 짧게, 8·12는 OSM에 없는 해안길이 많아 40%가량을 안내지도 선으로 그림 — 모두 지도 위에서 눈으로 확인 (2026-11-16) |
| `tour_api/` | `kto_spots.json` — 한국관광공사 TourAPI 국문 관광정보(공공데이터포털 15101578)의 대표 관광지 18곳 공식 개요·좌표 (`python -m app.data_import tour-fetch`, 공공데이터포털 키 필요). 있으면 AI동행자의 관광지·이야기 답변이 이 글을 씀 | 18곳 모두 매칭 (2026-11-16). 오디(관광지 오디오 해설) API는 활용신청 전이라 미사용 |
| `toilets/` | 제주 공중화장실 — `osm_toilets.json`(OpenStreetMap, ODbL) 또는 공식 `전국공중화장실표준데이터` CSV(행정안전부, 공공데이터포털 15012892)·API 결과. CSV > API > OSM 순으로 하나만 적재 (`python -m app.data_import toilets-fetch` / `toilets`) | OSM 594곳 적재 (2026-11-16). 공식 표준데이터 API는 활용신청 전이라 "등록되지 않은 서비스키" |
| `poi/` | 공공와이파이 관광지 AP CSV, 서귀포시 착한가격업소 OpenAPI 활용가이드(HWP) | 있음 |
| `juso_entrc/` | **도로명주소 위치정보 요약DB** 제주특별자치도 파일(.txt 또는 .zip) — 주소 → 좌표 변환용(공공누리 1유형, 출처표시) | 승인 대기. 받은 뒤 `python -m app.data_import juso-inspect`로 형식 확인 |

| `stores/` | 좌표가 들어 있는 업소 목록 — **소상공인 상가(상권)정보**(`python -m app.data_import sbiz-fetch`로 받은 JSON 또는 data.go.kr CSV)와 **인허가 음식점·숙박 CSV**(좌표정보 X/Y). 착한가격업소 등 주소만 있는 장소에 상호·주소로 좌표를 맞추는 용도 | 승인 없이 가능(상가정보 API 키는 자동승인) |

좌표 채우는 순서: 위 파일을 넣고 → `python -m app.data_import goodprice`. 상호·주소가 맞는 업소만 채우고 애매하면 비워 둡니다.

받는 곳: <http://www.juso.go.kr/externalLink/goUrl.do?menuId=DT03> (로그인·본인인증·이용목적 심사). 주소기반산업지원서비스 > 주소정보 다운로드에서는 **"제공하는 주소(도형, 좌표)"** 쪽입니다("공개하는 주소(속성)"의 주소DB·건물DB에는 좌표가 없습니다).

## 적재

```bash
cd backend
.venv/Scripts/python -m app.data_import all ../data/raw
```

제주올레 좌표·경로는 먼저 `.venv/Scripts/python -m app.data_import olle-fetch`로 받은 뒤 `all`(또는 `olle-endpoints` → `olle-osm` 순서)로 적재합니다. OSM 경로는 공식 시작·종점 좌표로 검증하므로 `olle-endpoints`가 먼저 돌아야 합니다.

OSM에 없는 코스는 공식 안내지도로 추정합니다: `python tools/olle_map_trace.py`(Pillow·NumPy가 있는 Python, 앱 venv에는 없음) → `.venv/Scripts/python -m app.data_import olle-traced`. 지도 이미지의 픽셀 위치는 `backend/app/data/olle_map_trace.json`에 사람이 읽어 적어 둔 값입니다.

`all`은 폴더의 CSV·SHP·JSON, 서귀포시 착한가격업소 OpenAPI(키 불필요), 하영올레 경유지 위치 보정을 차례로 실행합니다.

## 활용가이드(HWP)에서 읽은 API

| API | 요청 | 키 | 응답 | 이 앱에서의 용도 |
|---|---|---|---|---|
| 서귀포시 착한가격 업소 V2.0 | `GET https://www.seogwipo.go.kr/openapi/goodPriceService/?page=&pageSize=` | 없음 | XML: shop_code(001 음식점·002 이미용·003 세탁·004 숙박·005 목욕), shop_title, shop_tel, shop_address, shop_goods, shop_holiday | 음식점·숙박 후보(품목·가격·전화). 좌표가 없어 카카오 지오코딩 전에는 주소 목록으로만 안내 |
