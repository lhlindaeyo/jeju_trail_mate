"""한국관광공사 TourAPI(국문 관광정보 서비스, 공공데이터포털 15101578) → 대표 관광지의 공식 안내문.

For each spot in app/data/tour_spots.json: keyword search (searchKeyword2, 관광지 type 12, 제주 주소만),
pick the entry whose title matches, then detailCommon2 for the official overview (개요) and coordinates.
The overview often carries the place's legend too (정방폭포의 서불 이야기 등), so it replaces the
curated text. Saved to data/raw/tour_api/kto_spots.json; tour_service reads it when present.

License: 공공데이터포털 "이용허락범위 제한 없음", overview text 공공누리 제1유형 — 출처(한국관광공사) 표시."""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..services.tour_service import TOUR_FILE
from .common import ImportError_, register

SOURCE_ID = "kto_tourapi"
BASE = "https://apis.data.go.kr/B551011/KorService2"
FILE_NAME = "kto_spots.json"
COMMON = {"MobileOS": "ETC", "MobileApp": "TrailApp", "_type": "json"}
# Where the curated name is not how TourAPI files the place: (search keyword, wanted title).
KEYWORDS = {
    "새연교·새섬": ("새연교", "새연교"),
    "대포 주상절리대": ("주상절리", "대포주상절리"),
    "협재해변": ("협재해수욕장", "협재해수욕장"),
    "산방산": ("산방산", "제주 서귀포 산방산"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+|\(.*?\)|\[.*?\]", "", s or "")


def clean_overview(text: str) -> str:
    t = html.unescape(re.sub(r"<br\s*/?>", "\n", text or "", flags=re.I))
    t = re.sub(r"<[^>]+>", "", t)
    return "\n".join(line.strip() for line in t.splitlines() if line.strip())


def pick(items: list[dict], wanted: str) -> dict | None:
    """Exact title (ignoring spaces and bracketed notes like '[유네스코 세계자연유산]') first, then a title starting
    with it. '소정방폭포' never wins for '정방폭포'."""
    jeju = [i for i in items if "제주" in (i.get("addr1") or "") and str(i.get("contenttypeid")) == "12"]
    w = _norm(wanted)
    return next((i for i in jeju if _norm(i["title"]) == w), None) or next((i for i in jeju if _norm(i["title"]).startswith(w)), None)


def _get(c: httpx.Client, op: str, key: str, **params) -> list[dict]:
    r = c.get(f"{BASE}/{op}", params={**COMMON, **params, "serviceKey": key})
    if r.status_code != 200 or not r.text.lstrip().startswith("{"):
        raise ImportError_(f"TourAPI {op} 응답 오류 HTTP {r.status_code} — 공공데이터포털 활용신청·키를 확인하세요")
    body = (r.json().get("response") or {}).get("body") or {}
    items = body.get("items") or {}
    items = items.get("item", []) if isinstance(items, dict) else []
    return items if isinstance(items, list) else [items]


def fetch(folder: Path, key: str | None, *, client: httpx.Client | None = None) -> dict:
    if not key:
        raise ImportError_("공공데이터포털 서비스키가 없습니다 (SBIZ_SERVICE_KEY 또는 KMA_SERVICE_KEY)")
    spots = json.loads(TOUR_FILE.read_text(encoding="utf-8"))["spots"]
    c = client or httpx.Client(timeout=20)
    out, missing = {}, []
    try:
        for p in spots:
            kw, wanted = KEYWORDS.get(p["name"], (p["name"], p["name"]))
            hit = pick(_get(c, "searchKeyword2", key, keyword=kw, contentTypeId=12, numOfRows=20, pageNo=1), wanted)
            if hit is None:
                missing.append(p["name"])
                continue
            d = _get(c, "detailCommon2", key, contentId=hit["contentid"])
            if not d:
                missing.append(p["name"])
                continue
            d = d[0]
            out[p["id"]] = {"contentid": str(hit["contentid"]), "title": d.get("title") or hit["title"],
                            "overview": clean_overview(d.get("overview", "")), "addr": d.get("addr1") or hit.get("addr1"),
                            "lat": float(d.get("mapy") or hit["mapy"]), "lon": float(d.get("mapx") or hit["mapx"]),
                            "modified": d.get("modifiedtime"), "copyright": d.get("cpyrhtDivCd"), "homepage": d.get("homepage")}
            time.sleep(0.2)
    finally:
        if client is None:
            c.close()
    if not out:
        raise ImportError_("TourAPI에서 관광지를 하나도 찾지 못했습니다")
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"source": "한국관광공사 TourAPI 국문 관광정보 서비스", "fetched_at": datetime.now(timezone.utc).isoformat(),
               "spots": out, "not_found": missing}
    (folder / FILE_NAME).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"file": str(folder / FILE_NAME), "matched": len(out), "not_found": missing}


def run(db: Session, folder: Path, key: str | None, *, license: str | None = None, dry_run: bool = False) -> dict:
    report = fetch(folder, key)
    if not dry_run:
        register(db, SOURCE_ID, [folder / FILE_NAME], license=license, ref_date=datetime.now().date().isoformat())
        db.commit()
    from ..services import tour_service
    tour_service.reload()
    return report
