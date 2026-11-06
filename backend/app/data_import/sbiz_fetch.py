"""소상공인시장진흥공단 상가(상권)정보 OpenAPI → data/raw/stores/sbiz_<시군구>_<업종>.json

    GET https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInUpjong
        ?serviceKey=<일반 인증키(Decoding)>&type=json&pageNo=1&numOfRows=1000
        &divId=signguCd&key=50130&indsLclsCd=I2

시군구코드: 서귀포시 50130, 제주시 50110. 업종 대분류: I1 숙박, I2 음식, S2 수리·개인서비스(이·미용·세탁·목욕).
엔드포인트·파라미터·업종코드는 공식 문서를 직접 확인하지 못하고 작성했습니다 — 응답이 비거나 오류면 활용가이드와
대조하세요(`--classes`, `--sigungu`로 바꿀 수 있음). 이 파일은 data/raw 아래라 git에 올라가지 않습니다.
인증키는 공공데이터포털 키(SBIZ_SERVICE_KEY, 없으면 KMA_SERVICE_KEY와 같은 계정 키를 사용)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .common import ImportError_

API = "https://apis.data.go.kr/B553077/api/open/sdsc2/storeListInUpjong"
KEEP = ("bizesNm", "brchNm", "rdnmAdr", "lnoAdr", "lat", "lon", "indsLclsNm", "indsSclsNm")


def fetch(service_key: str | None, out_dir: Path, *, sigungu: str = "50130", classes: tuple[str, ...] = ("I1", "I2", "S2"),
          client: httpx.Client | None = None) -> dict:
    if not service_key:
        raise ImportError_("SBIZ_SERVICE_KEY(또는 KMA_SERVICE_KEY)가 없습니다. 공공데이터포털에서 "
                           "'소상공인시장진흥공단_상가(상권)정보'를 활용신청한 뒤 일반 인증키를 backend/.env에 넣어 주세요.")
    c = client or httpx.Client(timeout=30)
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"files": {}}
    for cls in classes:
        stores: list[dict] = []
        page, total = 1, None
        while True:
            resp = c.get(API, params={"serviceKey": service_key, "type": "json", "pageNo": page, "numOfRows": 1000,
                                      "divId": "signguCd", "key": sigungu, "indsLclsCd": cls})
            if resp.status_code != 200:
                raise ImportError_(f"상가정보 API 응답 {resp.status_code}: {resp.text[:200].replace(service_key, '<KEY>')}")
            try:
                data = resp.json()
            except ValueError as e:
                raise ImportError_(f"상가정보 API가 JSON이 아닌 응답을 줬습니다(키/활용신청 확인): {resp.text[:200].replace(service_key, '<KEY>')}") from e
            header = data.get("header") or {}
            if header.get("resultCode") not in (None, "00"):
                raise ImportError_(f"상가정보 API 오류 {header.get('resultCode')}: {header.get('resultMsg')}")
            body = data.get("body") or {}
            items = body.get("items") or []
            if isinstance(items, dict):
                items = [items]
            stores += [{k: it.get(k) for k in KEEP} for it in items]
            total = int(body.get("totalCount") or 0)
            if not items or len(stores) >= total or page >= 200:
                break
            page += 1
        path = out_dir / f"sbiz_{sigungu}_{cls}.json"
        path.write_text(json.dumps({"source": "소상공인시장진흥공단 상가(상권)정보", "sigungu": sigungu, "class": cls,
                                    "fetched_at": datetime.now(timezone.utc).isoformat(), "total": total, "stores": stores},
                                   ensure_ascii=False), encoding="utf-8")
        report["files"][path.name] = {"stores": len(stores), "with_coords": sum(1 for s in stores if s.get("lat") and s.get("lon"))}
    return report
