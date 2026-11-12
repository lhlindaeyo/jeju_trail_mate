"""PER_DEVICE_USERS (public demo): each browser's X-Device-Id is its own user; off = one local user."""
import pytest

from app.config import get_settings

from .conftest import make_trip

A = {"X-Device-Id": "judge-a-0123456789abcdef"}
B = {"X-Device-Id": "judge-b-0123456789abcdef"}


@pytest.fixture
def per_device():
    s = get_settings()
    s.per_device_users = True
    yield
    s.per_device_users = False


def test_devices_are_separate_users(client, per_device):
    a, b = client.get("/v1/me", headers=A).json(), client.get("/v1/me", headers=B).json()
    assert a["user_id"] != b["user_id"]
    assert client.get("/v1/me", headers=A).json()["user_id"] == a["user_id"]  # stable per device

    trip = make_trip(client, A)
    assert trip in [t["id"] for t in client.get("/v1/trips", headers=A).json()["trips"]]
    assert trip not in [t["id"] for t in client.get("/v1/trips", headers=B).json()["trips"]]
    assert client.get(f"/v1/trips/{trip}/today", headers=B).status_code == 404

    # erasing one tester's data leaves the other alone
    assert client.delete("/v1/account/data", headers=B).status_code in (200, 204)
    assert trip in [t["id"] for t in client.get("/v1/trips", headers=A).json()["trips"]]
    client.delete("/v1/account/data", headers=A)


def test_device_id_required_and_validated(client, per_device):
    assert client.get("/v1/me").status_code == 400
    assert client.get("/v1/me", headers={"X-Device-Id": "short"}).status_code == 400
    assert client.get("/v1/me", headers={"X-Device-Id": "x" * 20 + "/../"}).status_code == 400


def test_single_user_mode_ignores_device_id(client, auth):
    me = client.get("/v1/me").json()["user_id"]
    assert client.get("/v1/me", headers=A).json()["user_id"] == me
    assert client.get("/v1/me", headers=B).json()["user_id"] == me
