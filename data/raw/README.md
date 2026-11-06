# 원본 데이터 보관 폴더 (data/raw)

직접 내려받은 원본 파일을 아래 폴더에 **파일명 그대로** 넣습니다. 이용 조건을 확인하기 전에는 저장소에 커밋되지 않습니다(.gitignore).

| 폴더 | 파일 | 현재 상태 (2026-11-05) |
|---|---|---|
| `hayoung_olle/` | 하영올레 SHP — 코스별 6개 파일 `hayoung-olle-course-N.{shp,shx,dbf,prj,cpg,qmd}` | 1·2·3코스 적재 완료 (8.84 / 5.87 / 6.23 km, 공시 8.4 / 5.8 / 6.2 km) |
| `olle_course_status/` | 제주특별자치도_올레코스현황 CSV | 있음 (29개 코스, 기준일 2026-07-31) |
| `poi/` | 공공와이파이 관광지 AP CSV, OpenAPI 활용가이드(HWP) 2종 | 있음 — 좌표는 OpenAPI로 받음 |
| `juso_entrc/` | **도로명주소 위치정보 요약DB** 제주특별자치도 파일(.txt 또는 .zip) — 주소 → 좌표 변환용(공공누리 1유형, 출처표시) | 승인 대기. 받은 뒤 `python -m app.data_import juso-inspect`로 형식 확인 |

| `stores/` | 좌표가 들어 있는 업소 목록 — **소상공인 상가(상권)정보**(`python -m app.data_import sbiz-fetch`로 받은 JSON 또는 data.go.kr CSV)와 **인허가 음식점·숙박 CSV**(좌표정보 X/Y). 착한가격업소 등 주소만 있는 장소에 상호·주소로 좌표를 맞추는 용도 | 승인 없이 가능(상가정보 API 키는 자동승인) |

좌표 채우는 순서: 위 파일을 넣고 → `python -m app.data_import goodprice`. 상호·주소가 맞는 업소만 채우고 애매하면 비워 둡니다.

받는 곳: <http://www.juso.go.kr/externalLink/goUrl.do?menuId=DT03> (로그인·본인인증·이용목적 심사). 주소기반산업지원서비스 > 주소정보 다운로드에서는 **"제공하는 주소(도형, 좌표)"** 쪽입니다("공개하는 주소(속성)"의 주소DB·건물DB에는 좌표가 없습니다).

## 적재

```bash
cd backend
.venv/Scripts/python -m app.data_import all ../data/raw
```

`all`은 폴더의 CSV·SHP, 서귀포시 착한가격업소 OpenAPI(키 불필요), 제주데이터허브 공공와이파이 OpenAPI(`JEJU_DATAHUB_APPKEY`가 있을 때), 하영올레 경유지 위치 보정을 차례로 실행합니다.

## 활용가이드(HWP)에서 읽은 API

| API | 요청 | 키 | 응답 | 이 앱에서의 용도 |
|---|---|---|---|---|
| 서귀포시 착한가격 업소 V2.0 | `GET https://www.seogwipo.go.kr/openapi/goodPriceService/?page=&pageSize=` | 없음 | XML: shop_code(001 음식점·002 이미용·003 세탁·004 숙박·005 목욕), shop_title, shop_tel, shop_address, shop_goods, shop_holiday | 음식점·숙박 후보(품목·가격·전화). 좌표가 없어 카카오 지오코딩 전에는 주소 목록으로만 안내 |
| 제주 공공와이파이 AP 정보 (제주데이터허브) | `GET https://open.jejudatahub.net/api/proxy/Dtb18ta1btbD1Da1a81aaDttab6tDabb/{프로젝트키}?number=&limit=100&baseDate=&addressDong=&category=` — 날짜별 기록이 쌓인 데이터(약 630만 건)라 **최신 baseDate 하루치(약 3,100 AP)**만 받음 | 제주데이터허브 프로젝트키 | JSON: totCnt, hasMore, data[apGroupName, category, addressDetail, **latitude, longitude**, baseDate] | 쉼터·공공장소 후보(거리순), 하영올레 경유지 위치 보정(천지연폭포·새연교·솜반천 등) |
