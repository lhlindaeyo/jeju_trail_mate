"""Relational data model (spec §09). PostGIS geometry is deferred; lat/lon floats are used
so the same schema runs on SQLite (dev) and PostgreSQL (prod)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    provider_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    locale: Mapped[str] = mapped_column(String(10), default="ko")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    preferences: Mapped[UserPreferences | None] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    trips: Mapped[list[Trip]] = relationship(back_populates="user", cascade="all, delete-orphan")


class UserPreferences(Base):
    __tablename__ = "user_preferences"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    daily_distance_target_km: Mapped[float] = mapped_column(Float, default=15.0)
    pace_kmh: Mapped[float] = mapped_column(Float, default=3.5)
    walk_start_time: Mapped[str] = mapped_column(String(5), default="08:30")
    walk_end_time: Mapped[str] = mapped_column(String(5), default="17:30")
    rest_every_minutes: Mapped[int] = mapped_column(Integer, default=90)
    interests: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["바다", "카페", "문화"]
    notification_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    journal_style: Mapped[str | None] = mapped_column(String(200))
    # Separate, revocable consents (spec §13)
    consent_location: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_microphone: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_photos: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_ai_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_memory: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(back_populates="preferences")


class TrailRoute(Base):
    __tablename__ = "trail_routes"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    official_code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    start_point: Mapped[str | None] = mapped_column(String(200))
    end_point: Mapped[str | None] = mapped_column(String(200))
    start_lat: Mapped[float | None] = mapped_column(Float)
    start_lon: Mapped[float | None] = mapped_column(Float)
    end_lat: Mapped[float | None] = mapped_column(Float)
    end_lon: Mapped[float | None] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)
    difficulty: Mapped[str] = mapped_column(String(20))  # 상 | 중 | 하
    estimated_duration_min: Mapped[int] = mapped_column(Integer)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    source_url: Mapped[str] = mapped_column(String(500))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(String(20), default="unverified")  # verified | unverified
    # Last time an admin checked the official site for detour/closure notices on this route.
    notices_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1)

    notices: Mapped[list[RouteNotice]] = relationship(back_populates="route", cascade="all, delete-orphan")


class RouteNotice(Base):
    __tablename__ = "route_notices"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    route_id: Mapped[str] = mapped_column(ForeignKey("trail_routes.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    notice_type: Mapped[str] = mapped_column(String(20))  # detour | closure | safety | info
    source_url: Mapped[str] = mapped_column(String(500))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | expired | withdrawn

    route: Mapped[TrailRoute] = relationship(back_populates="notices")


class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    lodging_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="planning")  # planning | active | completed
    consent_flags: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="trips")
    schedules: Mapped[list[DailySchedule]] = relationship(back_populates="trip", cascade="all, delete-orphan")
    sessions: Mapped[list[TrackingSession]] = relationship(back_populates="trip", cascade="all, delete-orphan")
    journal_entries: Mapped[list[JournalEntry]] = relationship(back_populates="trip", cascade="all, delete-orphan")
    books: Mapped[list[JourneyBook]] = relationship(back_populates="trip", cascade="all, delete-orphan")
    proposals: Mapped[list[AgentProposal]] = relationship(back_populates="trip", cascade="all, delete-orphan")


class DailySchedule(Base):
    """One row per (trip, date, revision). The approved plan is the highest revision with
    user_approved_at set; drafts never overwrite an approved row."""

    __tablename__ = "daily_schedules"
    __table_args__ = (UniqueConstraint("trip_id", "date", "revision"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("trail_routes.id"))
    planned_start: Mapped[str | None] = mapped_column(String(5))  # HH:MM local (Asia/Seoul)
    planned_end: Mapped[str | None] = mapped_column(String(5))
    note: Mapped[str | None] = mapped_column(Text)
    checklist: Mapped[list] = mapped_column(JSON, default=list)  # 확인 필요 항목
    revision: Mapped[int] = mapped_column(Integer, default=1)
    user_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    trip: Mapped[Trip] = relationship(back_populates="schedules")
    route: Mapped[TrailRoute | None] = relationship()


class TrackingSession(Base):
    __tablename__ = "tracking_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    route_id: Mapped[str | None] = mapped_column(ForeignKey("trail_routes.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_seconds: Mapped[int] = mapped_column(Integer, default=0)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    distance_m: Mapped[float] = mapped_column(Float, default=0.0)
    manual_distance_m: Mapped[float | None] = mapped_column(Float)  # when GPS is denied
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lon: Mapped[float | None] = mapped_column(Float)
    last_accuracy_m: Mapped[float | None] = mapped_column(Float)
    last_point_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | paused | ended
    tracking_mode: Mapped[str] = mapped_column(String(10), default="gps")  # gps | manual

    trip: Mapped[Trip] = relationship(back_populates="sessions")
    route: Mapped[TrailRoute | None] = relationship()
    points: Mapped[list[GpsPoint]] = relationship(back_populates="session", cascade="all, delete-orphan")


class GpsPoint(Base):
    __tablename__ = "gps_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("tracking_sessions.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), default="device")

    session: Mapped[TrackingSession] = relationship(back_populates="points")


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(10), default="memo")  # memo | daily
    text: Mapped[str | None] = mapped_column(Text)  # user's raw text / voice transcript
    ai_draft: Mapped[str | None] = mapped_column(Text)
    user_edited_text: Mapped[str | None] = mapped_column(Text)  # final, user-approved
    mood_tags: Mapped[list] = mapped_column(JSON, default=list)
    is_highlight: Mapped[bool] = mapped_column(Boolean, default=False)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    visibility: Mapped[str] = mapped_column(String(10), default="private")
    status: Mapped[str] = mapped_column(String(10), default="draft")  # draft | saved

    trip: Mapped[Trip] = relationship(back_populates="journal_entries")
    media: Mapped[list[MediaAsset]] = relationship(back_populates="entry", cascade="all, delete-orphan")


class MediaAsset(Base):
    __tablename__ = "media_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("journal_entries.id", ondelete="CASCADE"), index=True)
    storage_key: Mapped[str] = mapped_column(String(500))
    media_type: Mapped[str] = mapped_column(String(10))  # photo | audio
    content_type: Mapped[str] = mapped_column(String(100))
    caption: Mapped[str | None] = mapped_column(Text)  # user-written description
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    consent_state: Mapped[str] = mapped_column(String(20), default="granted")

    entry: Mapped[JournalEntry] = relationship(back_populates="media")


class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    event_key: Mapped[str] = mapped_column(String(100), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(10))  # safety | info
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    actions: Mapped[list] = mapped_column(JSON, default=list)
    source: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    action_taken: Mapped[str | None] = mapped_column(String(30))  # opened | dismissed | snooze_today | <action id>


class AgentProposal(Base):
    """Changes the AI suggests but may not apply on its own (spec §07 step 6)."""

    __tablename__ = "agent_proposals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # schedule_change
    summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    trip: Mapped[Trip] = relationship(back_populates="proposals")


class JourneyBook(Base):
    __tablename__ = "journey_books"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    trip_id: Mapped[str] = mapped_column(ForeignKey("trips.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    theme: Mapped[str] = mapped_column(String(30), default="olle_green")
    cover_media_id: Mapped[str | None] = mapped_column(String(32))
    prologue: Mapped[str | None] = mapped_column(Text)
    epilogue: Mapped[str | None] = mapped_column(Text)
    excluded_entry_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    # draft → generating → preview_ready → user_approved → exported (or failed)
    error: Mapped[str | None] = mapped_column(Text)
    storage_key: Mapped[str | None] = mapped_column(String(500))
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    share_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    trip: Mapped[Trip] = relationship(back_populates="books")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    event_type: Mapped[str] = mapped_column(String(50))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
