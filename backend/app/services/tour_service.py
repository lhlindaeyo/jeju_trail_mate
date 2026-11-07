"""대표 관광지와 전해 내려오는 이야기. 내장 큐레이션(app/data/tour_spots.json)을 읽어 돌려준다.

공식 데이터셋이 아니므로 모든 응답에 출처(자체 정리)와 '위치는 대략적' 주의를 붙인다.
이야기는 파일에 적힌 문장만 쓰고, 도구가 만들어 내지 않는다."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import DataSource
from .geo import haversine_m

TOUR_FILE = Path(__file__).resolve().parent.parent / "data" / "tour_spots.json"
CAVEAT = "위치는 대략적이고 운영시간·입장료는 담지 않았어요. 이야기는 전해 내려오는 설화라 사실과 다를 수 있으니 방문 전 공식 안내를 확인하세요."


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(TOUR_FILE.read_text(encoding="utf-8"))


def seed_tour_source(db: Session) -> None:
    src = _data()["data_source"]
    if not db.get(DataSource, src["id"]):
        db.add(DataSource(**src))
        db.commit()


def _source_fields() -> dict:
    s = _data()["data_source"]
    return {"source": s["name"], "source_url": s["platform_url"], "fetched_at": s["ref_date"], "caveat": CAVEAT}


def _brief(p: dict, d: float | None) -> dict:
    return {"id": p["id"], "name": p["name"], "area": p["area"], "summary": p["summary"], "tags": p["tags"],
            "distance_m": round(d) if d is not None else None, "location": [p["lat"], p["lon"]]}


def find_spots(lat: float | None = None, lon: float | None = None, limit: int = 5) -> dict:
    """lat/lon이 있으면 가까운 순, 없으면 파일 순서(서귀포 대표 장소 먼저)."""
    spots = _data()["spots"]
    if lat is not None and lon is not None:
        ranked = sorted(((haversine_m(lat, lon, p["lat"], p["lon"]), p) for p in spots), key=lambda x: x[0])
        places, basis = [_brief(p, d) for d, p in ranked[:limit]], "nearby"
        note = None
    else:
        places, basis = [_brief(p, None) for p in spots[:limit]], "featured"
        note = "내 위치를 몰라 거리순이 아니라 서귀포 대표 관광지부터 보여 드려요."
    return {"status": "ok", "basis": basis, "places": places, "note": note, **_source_fields()}


def get_story(spot: str | None = None, lat: float | None = None, lon: float | None = None) -> dict:
    spots = _data()["spots"]
    names = [p["name"] for p in spots]
    if spot:
        q = spot.replace(" ", "")
        hit = next((p for p in spots if q in p["name"].replace(" ", "") or p["name"].replace(" ", "") in q
                    or q.lower() == p["id"]), None)
        if hit is None:
            return {"status": "not_found", "message": f"'{spot}'은(는) 이야기를 정리해 둔 관광지에 없어요.", "available": names,
                    **_source_fields()}
        chosen = [hit]
    else:
        near = find_spots(lat, lon, limit=3)["places"]
        by_id = {p["id"]: p for p in spots}
        chosen = [by_id[p["id"]] for p in near]
    return {"status": "ok", "stories": [{"name": p["name"], "area": p["area"], "story": p["story"]} for p in chosen],
            **_source_fields()}
