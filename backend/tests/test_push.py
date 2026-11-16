"""Web Push: subscriptions, delivery of new alerts, cleanup of dead endpoints. The push service is faked."""
from app import push
from app.db import SessionLocal
from app.models import Notification, PushSubscription

SUB = {"endpoint": "https://push.example.test/abc", "keys": {"p256dh": "B" + "x" * 86, "auth": "a" * 22}}


def test_config_says_why_push_is_off(client, auth, monkeypatch):
    monkeypatch.setattr(push, "library_available", lambda: False)
    c = client.get("/v1/push/config", headers=auth).json()
    assert c["enabled"] is False and "pywebpush" in c["reason"] and c["public_key"] is None
    assert client.post("/v1/push/test", headers=auth).json()["sent"] == 0


def test_subscribe_send_and_drop_gone_endpoints(client, auth, monkeypatch):
    monkeypatch.setattr(push, "status", lambda: {"enabled": True, "public_key": "pub", "reason": None})
    assert client.post("/v1/push/subscribe", headers=auth, json=SUB).json()["ok"]
    assert client.post("/v1/push/subscribe", headers=auth, json=SUB).json()["ok"]  # same browser again: no duplicate
    assert client.get("/v1/push/config", headers=auth).json()["subscriptions"] == 1

    sent = []
    monkeypatch.setattr(push, "_send_one", lambda sub, data: sent.append((sub.endpoint, data)) or None)
    r = client.post("/v1/push/test", headers=auth).json()
    assert r["sent"] == 1 and sent[0][0] == SUB["endpoint"] and "잠금화면" in sent[0][1]["body"]

    monkeypatch.setattr(push, "_send_one", lambda sub, data: 410)  # browser unsubscribed / app removed
    assert client.post("/v1/push/test", headers=auth).json()["failed"] == 1
    assert client.get("/v1/push/config", headers=auth).json()["subscriptions"] == 0


def test_safety_payload_and_async_delivery(client, auth, monkeypatch):
    client.post("/v1/push/subscribe", headers=auth, json=SUB)
    monkeypatch.setattr(push, "status", lambda: {"enabled": True, "public_key": "pub", "reason": None})
    got = []
    monkeypatch.setattr(push, "_send_one", lambda sub, data: got.append(data) or None)

    class Now:  # run the 'thread' inline
        def __init__(self, target, daemon): self.target = target
        def start(self): self.target()

    monkeypatch.setattr(push.threading, "Thread", Now)
    with SessionLocal() as db:
        uid = db.query(PushSubscription).first().user_id
        n = Notification(user_id=uid, event_key="t-safety", event_type="end_time_risk", severity="safety",
                         title="종료 목표 시각 이후 도착 예상", message="가까운 마을에서 마치세요.")
        db.add(n)
        db.commit()
        push.deliver_async([n])
    assert got and got[0]["severity"] == "safety" and got[0]["title"].startswith("종료") and got[0]["url"] == "/#brief"
    client.post("/v1/push/unsubscribe", headers=auth, json={"endpoint": SUB["endpoint"]})
    assert client.get("/v1/push/config", headers=auth).json()["subscriptions"] == 0


def test_subscribe_rejects_non_https_endpoint(client, auth):
    assert client.post("/v1/push/subscribe", headers=auth, json={**SUB, "endpoint": "http://evil.test/x"}).status_code == 422
