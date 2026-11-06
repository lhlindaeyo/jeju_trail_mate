"""Kakao Local API (address + keyword search). Optional. `storable` follows KAKAO_STORABLE (default False):
set it to true only after confirming the current Kakao terms allow keeping the results."""
from __future__ import annotations

import httpx

from .base import GeoHit, in_jeju


class KakaoGeocoder:
    name = "kakao"

    def __init__(self, api_key: str, storable: bool = False, client: httpx.Client | None = None):
        self.key, self.storable = api_key, storable
        self.client = client or httpx.Client(timeout=8)

    def _docs(self, path: str, params: dict) -> list[dict]:
        r = self.client.get(f"https://dapi.kakao.com/v2/local/{path}", params=params,
                            headers={"Authorization": f"KakaoAK {self.key}"})
        return r.json().get("documents", []) if r.status_code == 200 else []

    def geocode_address(self, address: str) -> GeoHit | None:
        docs = self._docs("search/address.json", {"query": address})
        if not docs:
            return None
        lat, lon = float(docs[0]["y"]), float(docs[0]["x"])
        return GeoHit(lat, lon, address, self.name, self.storable, "address") if in_jeju(lat, lon) else None

    def search_place(self, name: str, center: tuple[float, float] | None = None, radius_m: float = 3000) -> list[GeoHit]:
        params: dict = {"query": name}
        if center:
            params.update({"y": center[0], "x": center[1], "radius": int(min(radius_m, 20000))})
        return [GeoHit(float(d["y"]), float(d["x"]), d["place_name"], self.name, self.storable, "place")
                for d in self._docs("search/keyword.json", params)[:3]]
