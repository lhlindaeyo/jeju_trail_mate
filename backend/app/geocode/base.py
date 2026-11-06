"""Geocoder interface. Importers depend only on this, so providers (local national address DB,
VWorld, Kakao, ...) can be swapped or chained from settings (GEOCODER_CHAIN).

`storable` is a licence flag: a provider whose terms forbid keeping its results (VWorld) may be used
to *look something up*, but its coordinates must never be written to the database."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


class GeocoderError(RuntimeError):
    pass


@dataclass
class GeoHit:
    lat: float
    lon: float
    label: str
    provider: str
    storable: bool
    precision: str = "address"  # address | place


class Geocoder(Protocol):
    name: str
    storable: bool

    def geocode_address(self, address: str) -> GeoHit | None: ...

    def search_place(self, name: str, center: tuple[float, float] | None = None, radius_m: float = 3000) -> list[GeoHit]: ...


def in_jeju(lat: float, lon: float) -> bool:
    return 33.0 < lat < 34.0 and 126.0 < lon < 127.0


def norm_name(s: str | None) -> str:
    return re.sub(r"[\s()（）·\-_]", "", s or "").lower()


class GeocoderChain:
    """Providers are tried in order. `storable_only` lookups skip providers whose results may not be stored."""

    name = "chain"
    storable = False  # per-hit flag is what matters

    def __init__(self, providers: list[Geocoder]):
        self.providers = providers

    def __bool__(self) -> bool:
        return bool(self.providers)

    def geocode_address(self, address: str, *, storable_only: bool = False) -> GeoHit | None:
        for p in self.providers:
            if storable_only and not p.storable:
                continue
            try:
                hit = p.geocode_address(address)
            except Exception as exc:  # one provider failing must not stop the import
                log.warning("geocoder %s failed for %r: %s", p.name, address, exc)
                continue
            if hit:
                return hit
        return None

    def search_place(self, name: str, center: tuple[float, float] | None = None, radius_m: float = 3000,
                     *, storable_only: bool = False) -> list[GeoHit]:
        for p in self.providers:
            if storable_only and not p.storable:
                continue
            try:
                hits = p.search_place(name, center, radius_m)
            except Exception as exc:
                log.warning("geocoder %s failed for %r: %s", p.name, name, exc)
                continue
            if hits:
                return hits
        return []

    def business_to_xy(self, name: str | None, address: str) -> tuple[float, float] | None:
        """Like address_to_xy, but providers that can match a shop by name (store lists) use it too."""
        for p in self.providers:
            if not p.storable:
                continue
            try:
                fn = getattr(p, "geocode_business", None)
                hit = fn(name, address) if fn and name else p.geocode_address(address)
            except Exception as exc:
                log.warning("geocoder %s failed for %r: %s", p.name, address, exc)
                continue
            if hit:
                return hit.lat, hit.lon
        return None

    def address_to_xy(self, address: str) -> tuple[float, float] | None:
        """(lat, lon) from a provider whose results may be stored, else None. What importers persist."""
        hit = self.geocode_address(address, storable_only=True)
        return (hit.lat, hit.lon) if hit else None
