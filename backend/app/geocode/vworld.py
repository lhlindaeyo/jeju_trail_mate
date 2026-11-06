"""브이월드(국토교통부) Geocoder 2.0 / Search 2.0.

    GET https://api.vworld.kr/req/address?service=address&request=getCoord&type=ROAD|PARCEL&address=...&key=
    GET https://api.vworld.kr/req/search?service=search&request=search&type=PLACE&query=...&key=

Real-time use only: the Geocoder terms forbid storing results in a database, so `storable = False`.
The key is bound to the domain given at issuance (VWORLD_DOMAIN)."""
from __future__ import annotations

import logging

import httpx

from ..services.geo import haversine_m
from .base import GeoHit, in_jeju

log = logging.getLogger(__name__)
BASE = "https://api.vworld.kr/req"


class VWorldGeocoder:
    name = "vworld"
    storable = False

    def __init__(self, api_key: str, domain: str | None = None, client: httpx.Client | None = None):
        self.key, self.domain = api_key, domain
        self.client = client or httpx.Client(timeout=10)

    def _get(self, path: str, params: dict) -> dict | None:
        params = {**params, "key": self.key, "format": "json"}
        if self.domain:
            params["domain"] = self.domain
        r = self.client.get(f"{BASE}/{path}", params=params)
        if r.status_code != 200:
            log.warning("vworld %s -> HTTP %s", path, r.status_code)
            return None
        resp = r.json().get("response") or {}
        if resp.get("status") == "ERROR":
            log.warning("vworld %s error: %s", path, (resp.get("error") or {}).get("text"))
            return None
        return resp if resp.get("status") == "OK" else None

    def geocode_address(self, address: str) -> GeoHit | None:
        for kind in ("ROAD", "PARCEL"):
            resp = self._get("address", {"service": "address", "request": "getCoord", "type": kind, "address": address})
            point = ((resp or {}).get("result") or {}).get("point")
            if point:
                lat, lon = float(point["y"]), float(point["x"])
                if in_jeju(lat, lon):
                    return GeoHit(lat, lon, address, self.name, self.storable, "address")
        return None

    def search_place(self, name: str, center: tuple[float, float] | None = None, radius_m: float = 3000) -> list[GeoHit]:
        resp = self._get("search", {"service": "search", "request": "search", "type": "PLACE", "query": name, "size": 10})
        items = (((resp or {}).get("result") or {}).get("items")) or []
        hits = []
        for it in items:
            point = it.get("point") or {}
            try:
                lat, lon = float(point["y"]), float(point["x"])
            except (KeyError, TypeError, ValueError):
                continue
            if not in_jeju(lat, lon):
                continue
            if center and haversine_m(center[0], center[1], lat, lon) > radius_m:
                continue
            hits.append(GeoHit(lat, lon, it.get("title") or name, self.name, self.storable, "place"))
        return hits
