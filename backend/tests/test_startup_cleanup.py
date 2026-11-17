"""What the app tidies at startup: leftovers of the removed diary/voice features, and old raw GPS points."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, inspect, text

from app.db import SessionLocal
from app.main import drop_removed_feature_leftovers
from app.models import GpsPoint, TrackingSession
from app.services.tracking_service import purge_old_points

from .conftest import make_trip, route_id


def _legacy_db(tmp_path, diary_rows: int = 0):
    """A database as it was before the diary/photo and voice features were removed."""
    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE user_preferences (user_id VARCHAR(32) PRIMARY KEY, pace_kmh FLOAT NOT NULL, "
                       "journal_style VARCHAR(200), consent_photos BOOLEAN NOT NULL, consent_microphone BOOLEAN NOT NULL, "
                       "consent_location BOOLEAN NOT NULL)"))
        c.execute(text("CREATE TABLE journal_entries (id INTEGER PRIMARY KEY, body TEXT)"))
        c.execute(text("CREATE TABLE journey_books (id INTEGER PRIMARY KEY)"))
        c.execute(text("CREATE TABLE media_assets (id INTEGER PRIMARY KEY)"))
        for i in range(diary_rows):
            c.execute(text("INSERT INTO journal_entries (body) VALUES ('오늘의 기록')"))
    return eng


def test_removed_feature_columns_and_empty_tables_are_dropped(tmp_path):
    eng = _legacy_db(tmp_path)
    drop_removed_feature_leftovers(eng)
    insp = inspect(eng)
    assert {c["name"] for c in insp.get_columns("user_preferences")} == {"user_id", "pace_kmh", "consent_location"}
    assert not {"journal_entries", "journey_books", "media_assets"} & set(insp.get_table_names())
    drop_removed_feature_leftovers(eng)  # a second start finds nothing to do


def test_a_diary_table_with_entries_is_not_deleted(tmp_path):
    eng = _legacy_db(tmp_path, diary_rows=2)
    drop_removed_feature_leftovers(eng)
    tables = set(inspect(eng).get_table_names())
    assert "journal_entries" in tables and not {"journey_books", "media_assets"} & tables
    with eng.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM journal_entries")).scalar() == 2


def test_raw_gps_points_are_deleted_30_days_after_the_walk_ends(client, auth):
    trip = make_trip(client, auth)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        def walk(ended_days_ago: int | None) -> str:
            s = TrackingSession(trip_id=trip, route_id=route_id(client, "7"), status="active" if ended_days_ago is None else "ended",
                                ended_at=None if ended_days_ago is None else now - timedelta(days=ended_days_ago))
            s.points = [GpsPoint(timestamp=now, lat=33.25, lon=126.56)]
            db.add(s)
            db.flush()
            return s.id

        old, recent, live = walk(31), walk(5), walk(None)
        db.commit()
        assert purge_old_points(db) == 1
        left = {p.session_id for p in db.query(GpsPoint)}
        assert old not in left and {recent, live} <= left
