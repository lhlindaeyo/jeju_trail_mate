import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, or_, text

from .config import get_settings
from .db import Base, SessionLocal, engine
from .routers import brief, me, misc, routes, sessions, trips
from .services.route_service import seed_routes
from .services.tour_service import seed_tour_source
from .services.tracking_service import purge_old_points

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# What the removed diary/photo and voice features left in databases created before their removal.
LEGACY_PREFERENCE_COLUMNS = ("consent_photos", "consent_microphone", "journal_style", "interests", "consent_memory")
LEGACY_TABLES = ("journal_entries", "journey_books", "media_assets")


def drop_removed_feature_leftovers(bind=engine) -> None:
    """Columns the app no longer has but that are NOT NULL without a default (`consent_photos`, `consent_microphone`,
    `interests`, `consent_memory`): on an old database a new profile could not even be inserted, so drop them. The old diary tables are dropped only when empty — one that
    still holds rows is left alone (and logged) rather than deleting someone's entries."""
    insp = inspect(bind)
    cols = {c["name"] for c in insp.get_columns("user_preferences")}
    tables = set(insp.get_table_names())
    with bind.begin() as conn:
        for col in LEGACY_PREFERENCE_COLUMNS:
            if col in cols:
                conn.execute(text(f"ALTER TABLE user_preferences DROP COLUMN {col}"))
        for table in LEGACY_TABLES:
            if table not in tables:
                continue
            if conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar():
                log.warning("table %s belongs to a removed feature but still has rows — left in place", table)
            else:
                conn.execute(text(f"DROP TABLE {table}"))


def _drop_removed_feature_alerts() -> None:
    """Alerts saved by the removed diary feature: '오늘의 기록' prompts and waypoint alerts whose button was
    '사진 남기기' (open_journal). Their buttons lead nowhere, so they are deleted."""
    from .models import Notification
    from .rules.engine import EVENT_TYPES, REMOVED_ACTIONS

    with SessionLocal() as db:
        stale = db.query(Notification).filter(or_(Notification.event_type.notin_(EVENT_TYPES),
                                                  Notification.event_type == "waypoint_arrival"))
        for n in stale:
            if n.event_type not in EVENT_TYPES or any(a.get("id") in REMOVED_ACTIONS for a in (n.actions or [])):
                db.delete(n)
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables & load the seed course set. Use Alembic migrations in production.
    Base.metadata.create_all(engine)
    drop_removed_feature_leftovers()
    _drop_removed_feature_alerts()
    with SessionLocal() as db:
        seed_routes(db)
        seed_tour_source(db)
        purge_old_points(db)  # Settings promises raw positions are deleted 30 days after a walk ends
    yield


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="JEJU TRAIL MATE API", version="0.1.0", lifespan=lifespan,
                  docs_url="/docs" if s.app_env != "production" else None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"])
    for r in (me.router, routes.router, trips.router, sessions.router, brief.router, misc.router):
        app.include_router(r)

    @app.get("/health")
    def health():
        from .agent.llm import get_llm

        return {"ok": True, "ai_provider": get_llm().name,
                "weather": bool(s.kma_apihub_key or s.kma_service_key)}

    return app


app = create_app()
