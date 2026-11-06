"""Geocoding providers behind one interface; the chain comes from settings (GEOCODER_CHAIN)."""
from __future__ import annotations

import logging

from ..config import get_settings
from .base import Geocoder, GeocoderChain, GeocoderError, GeoHit

__all__ = ["Geocoder", "GeocoderChain", "GeocoderError", "GeoHit", "get_geocoder"]
log = logging.getLogger(__name__)


def get_geocoder(chain: str | None = None) -> GeocoderChain:
    """Build the chain from `GEOCODER_CHAIN` (default local,vworld,kakao); unconfigured entries are skipped."""
    s = get_settings()
    providers: list[Geocoder] = []
    for name in (chain if chain is not None else s.geocoder_chain).split(","):
        name = name.strip().lower()
        if name == "local":
            from .local_juso import LocalJusoGeocoder

            g = LocalJusoGeocoder(s.juso_entrc_dir)
            if g.available():
                providers.append(g)
            else:
                log.info("local juso DB not found in %s — skipped", s.juso_entrc_dir)
        elif name == "stores":
            from .store_match import StoreMatchGeocoder

            st = StoreMatchGeocoder(s.stores_dir)
            if st.available():
                providers.append(st)
            else:
                log.info("no store lists in %s — skipped", s.stores_dir)
        elif name == "vworld" and s.vworld_api_key:
            from .vworld import VWorldGeocoder

            providers.append(VWorldGeocoder(s.vworld_api_key, s.vworld_domain))
        elif name == "kakao" and s.kakao_rest_api_key:
            from .kakao import KakaoGeocoder

            providers.append(KakaoGeocoder(s.kakao_rest_api_key, s.kakao_storable))
        elif name and name not in ("vworld", "kakao"):
            log.warning("unknown GEOCODER_CHAIN entry %r ignored", name)
    return GeocoderChain(providers)
