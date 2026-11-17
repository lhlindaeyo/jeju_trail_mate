"""Display language for one request: Korean (default) or English.

The web app adds `?lang=en` to every API call when the user picks English in Settings (an `X-Lang` header works
too). `LangMiddleware` puts that choice in a context variable for the request, and user-facing text is written as
`t("한국어", "English")` right where it is built. Data from the sources themselves (place names, waypoint names,
official notice titles, tour texts) stays as published — mostly Korean."""
from __future__ import annotations

from contextvars import ContextVar
from urllib.parse import parse_qs

LANGS = ("ko", "en")
_lang: ContextVar[str] = ContextVar("lang", default="ko")


def lang() -> str:
    return _lang.get()


def is_en() -> bool:
    return _lang.get() == "en"


def t(ko: str, en: str) -> str:
    return en if _lang.get() == "en" else ko


def set_lang(value: str | None):
    """Set the language for the current context; returns a token for `reset_lang`."""
    return _lang.set(value if value in LANGS else "ko")


def reset_lang(token) -> None:
    _lang.reset(token)


def trail_label(trail_type: str) -> str:
    return {"jeju_olle": t("제주올레", "Jeju Olle"), "hayoung_olle": t("하영올레", "Hayoung Olle")}.get(trail_type, trail_type)


def route_name(route) -> str:
    """'제주올레 7코스' / 'Jeju Olle Route 7'. Works on a TrailRoute or a route dict."""
    get = route.get if isinstance(route, dict) else lambda k: getattr(route, k, None)
    if not is_en():
        return get("name")
    no = get("course_no")
    return f"{trail_label(get('trail_type'))} Route {no}" if no else get("name")


class LangMiddleware:
    """Pure ASGI middleware, so the context variable it sets is visible to the endpoint (and the thread pool that
    runs sync endpoints, which copies the context)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        value = parse_qs(scope.get("query_string", b"").decode("latin-1")).get("lang", [None])[0]
        if value is None:
            for k, v in scope.get("headers", []):
                if k == b"x-lang":
                    value = v.decode("latin-1").strip().lower()
                    break
        token = set_lang(value)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_lang(token)
