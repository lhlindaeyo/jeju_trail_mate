from .conftest import make_trip

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa4\x00\x00\x00\x00IEND\xaeB`\x82")


def test_daily_draft_uses_only_user_records(client, auth):
    trip = make_trip(client, auth, "2026-11-10", "2026-11-11")
    r = client.post(f"/v1/trips/{trip}/journals/daily-draft", headers=auth, json={"date": "2026-11-10"})
    assert r.status_code == 422  # nothing recorded → no invented diary

    client.post("/v1/journals", headers=auth, json={"trip_id": trip, "date": "2026-11-10", "text": "오늘 바다가 정말 예뻤어", "is_highlight": True})
    d = client.post(f"/v1/trips/{trip}/journals/daily-draft", headers=auth, json={"date": "2026-11-10"}).json()
    assert d["status"] == "draft" and d["ai_draft_label"]
    assert "오늘 바다가 정말 예뻤어" in d["ai_draft"]  # mock provider: user's words only
    saved = client.put(f"/v1/journals/{d['id']}", headers=auth, json={"text": d["ai_draft"] + " 바람도 시원했다."}).json()
    assert saved["status"] == "saved"


def test_media_upload_and_signed_url(client, auth):
    trip = make_trip(client, auth)
    e = client.post("/v1/journals", headers=auth, json={"trip_id": trip, "date": "2026-11-10", "text": "사진"}).json()
    r = client.post(f"/v1/journals/{e['id']}/media", headers=auth, files={"file": ("a.png", PNG, "image/png")}, data={"caption": "광치기 해변"})
    assert r.status_code == 201, r.text
    url = r.json()["media"][0]["url"]
    assert client.get(url).status_code == 200
    assert client.get(url.replace("sig=", "sig=0")).status_code == 403
    bad = client.post(f"/v1/journals/{e['id']}/media", headers=auth, files={"file": ("a.exe", b"MZ", "application/x-msdownload")})
    assert bad.status_code == 415


def test_book_lifecycle(client, auth):
    trip = make_trip(client, auth, "2026-11-10", "2026-11-12")
    e = client.post("/v1/journals", headers=auth, json={"trip_id": trip, "date": "2026-11-10", "text": "첫날, <b>설렜다</b>"}).json()
    b = client.post(f"/v1/trips/{trip}/book", headers=auth, json={"title": "나의 올레"}).json()
    assert b["status"] == "preview_ready", b
    html = client.get(b["preview_url"]).text
    assert "&lt;b&gt;설렜다&lt;/b&gt;" in html  # user text is escaped
    assert html.count("이 날의 기록이 없습니다") == 2  # empty days are not filled in
    assert "공식 스탬프" in html

    assert client.get(f"/v1/books/{b['id']}/export", headers=auth).status_code == 409  # must approve first
    b2 = client.patch(f"/v1/books/{b['id']}", headers=auth, json={"excluded_entry_ids": [e["id"]]}).json()
    assert "설렜다" not in client.get(b2["preview_url"]).text
    assert client.post(f"/v1/books/{b['id']}/approve", headers=auth).json()["status"] == "user_approved"
    r = client.get(f"/v1/books/{b['id']}/export", headers=auth)
    assert r.status_code == 200 and (r.headers["content-type"].startswith("application/pdf") or r.headers.get("x-pdf-engine") == "browser-print")


def test_account_delete_removes_everything(client):
    tok = client.post("/v1/auth/dev-login", json={"email": "bye@example.com"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    client.patch("/v1/me/preferences", headers=h, json={"consent_photos": True})
    trip = make_trip(client, h)
    e = client.post("/v1/journals", headers=h, json={"trip_id": trip, "text": "x"}).json()
    url = client.post(f"/v1/journals/{e['id']}/media", headers=h, files={"file": ("a.png", PNG, "image/png")}).json()["media"][0]["url"]
    assert client.get("/v1/account/export", headers=h).json()["trips"]
    r = client.delete("/v1/account/data", headers=h).json()
    assert r["deleted"] and r["files_removed"] >= 1
    assert client.get(url).status_code == 404
    assert client.get("/v1/me", headers=h).status_code == 401
