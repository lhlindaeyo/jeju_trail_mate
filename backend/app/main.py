import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text

from .config import get_settings
from .db import Base, SessionLocal, engine
from .routers import brief, me, misc, routes, sessions, trips
from .services.route_service import seed_routes

logging.basicConfig(level=logging.INFO)


def _drop_removed_photo_consent() -> None:
    """The diary (photos) feature is gone. On a database created before that, `consent_photos` is NOT NULL
    with no default, so inserting a new profile would fail; drop the column. Old diary tables are left alone."""
    cols = {c["name"] for c in inspect(engine).get_columns("user_preferences")}
    if "consent_photos" in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE user_preferences DROP COLUMN consent_photos"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: create tables & load the seed course set. Use Alembic migrations in production.
    Base.metadata.create_all(engine)
    _drop_removed_photo_consent()
    with SessionLocal() as db:
        seed_routes(db)
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
                "weather": bool(s.kma_service_key)}

    return app


app = create_app()
