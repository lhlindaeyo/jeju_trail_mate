import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="jtm-test-")
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "STORAGE_DIR": f"{_tmp}/storage",
    "LLM_PROVIDER": "mock",
    "LLM_CHAIN": "",  # a developer's .env may set a chain; tests always run offline on the mock
    "ADMIN_TOKEN": "test-admin",
    "KMA_SERVICE_KEY": "",
    
    "APP_ENV": "test",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agent import llm as llm_mod  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_llm():
    llm_mod.set_llm(None)
    yield
    llm_mod.set_llm(None)


_counter = {"n": 0}


@pytest.fixture
def auth(client):
    """Fresh user with all consents on; returns headers."""
    _counter["n"] += 1
    r = client.post("/v1/auth/dev-login", json={"email": f"user{_counter['n']}@example.com"})
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.patch("/v1/me/preferences", headers=h, json={
        "consent_location": True, "consent_microphone": True, "consent_photos": True,
        "consent_ai_processing": True, "walk_start_time": "08:00", "walk_end_time": "18:00",
        "daily_distance_target_km": 20, "pace_kmh": 4.0, "rest_every_minutes": 90,
    })
    return h


def route_id(client, code: str, trail_type: str = "jeju_olle") -> str:
    routes = client.get("/v1/routes", params={"trail_type": trail_type}).json()["routes"]
    return next(r["id"] for r in routes if r["course_no"] == code)


def make_trip(client, h, start="2026-11-10", end="2026-11-12") -> str:
    return client.post("/v1/trips", headers=h, json={"title": "가을 올레", "start_date": start, "end_date": end}).json()["id"]
