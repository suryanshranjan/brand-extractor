import json
import time

import pytest

from brandscan import pipeline
from brandscan.pipeline import ScanError, ScanOptions, run_scan

from brandscan.instagram import LoginRequired, parse_profile
from brandscan.models import Profile

from .conftest import FakeClient, PosterReader
from .test_parsing import PROFILE_PAGE


@pytest.fixture
def poster_ocr(monkeypatch):
    monkeypatch.setattr(pipeline, "get_reader", lambda *a: PosterReader())


def test_full_scan(settings, poster_ocr):
    events = []
    result = run_scan(settings, ScanOptions("@MokshaNSUT", ner=False), FakeClient(), events.append)

    types = {e.handle or e.key: e.type for e in result.entities}
    assert types["redbullindia"] == "brand"
    assert types["djbassworm"] == "creator"
    assert types["salimsulaimanmusic"] == "creator"
    assert types["rahul_99"] == "personal"
    assert types["ollyesse"] == "not found"

    brands = [e.handle or e.display for e in result.brands]
    assert brands[:2] == ["gethingyms", "redbullindia"]
    assert {"unstop.world", "Samsung", "Hans Outfit", "Replit"} <= set(brands)
    assert "Congratulations" not in brands and "Congratulations!" not in brands
    assert result.completed and result.posts_scanned == 4

    report = json.loads(open(result.files["json"], encoding="utf-8").read())
    assert report["brands"][0]["handle"] == "gethingyms"
    assert [e["stage"] for e in events][0] == "profile" and events[-1]["stage"] == "done"


def test_resume_skips_scanned_posts(settings):
    run_scan(settings, ScanOptions("mokshansut", limit=2, ner=False, ocr=False), FakeClient())
    events = []
    result = run_scan(settings, ScanOptions("mokshansut", ner=False, ocr=False), FakeClient(), events.append)
    assert result.posts_scanned == 4


def test_unknown_target(settings):
    with pytest.raises(ScanError, match="doesn't exist"):
        run_scan(settings, ScanOptions("nobody_here", ocr=False), FakeClient())


def test_private_account(settings):
    client = FakeClient(profiles={"secret": Profile("secret", "9", is_private=True)})
    with pytest.raises(ScanError, match="private account"):
        run_scan(settings, ScanOptions("secret", ocr=False), client)


def test_logged_out_falls_back_to_profile_page(settings, monkeypatch):
    from brandscan.instagram import InstagramClient, parse_profile_page
    client = InstagramClient(None, delay=(0, 0))
    def refuse(*a, **k):
        raise LoginRequired("Please wait a few minutes before you try again.")
    monkeypatch.setattr(client, "get_json", refuse)
    monkeypatch.setattr(client, "get_profile_page", lambda u: (parse_profile(parse_profile_page(PROFILE_PAGE)),
                                                               parse_profile_page(PROFILE_PAGE)))
    result = run_scan(settings, ScanOptions("engifest_dtu", ner=False, ocr=False), client)
    assert result.posts_scanned == 1 and "Connect Instagram" in result.message
    assert "redbullindia" in [e.handle for e in result.brands]


def test_stop_request(settings):
    result = run_scan(settings, ScanOptions("mokshansut", ner=False, ocr=False), FakeClient(),
                      should_stop=lambda: True)
    assert not result.completed and "Stopped" in result.message


# --- web service -----------------------------------------------------------

def test_api(settings, monkeypatch, poster_ocr):
    from fastapi.testclient import TestClient

    from brandscan import server
    from brandscan.server import jobs

    settings.api_keys = ["acme:secret-key", "other:other-key"]
    monkeypatch.setattr(jobs, "make_client", lambda s, cookies=None: FakeClient())
    app = server.create_app(settings, start_worker=False)
    store, worker = app.state.store, app.state.worker

    with TestClient(app) as http:
        assert http.get("/api/health").json()["auth_required"] is True
        assert http.get("/api/scans").status_code == 401

        h = {"X-API-Key": "secret-key"}
        job = http.post("/api/scans", json={"target": "https://instagram.com/mokshansut/", "limit": 10}, headers=h).json()
        assert job["status"] == "queued" and job["target"] == "mokshansut"

        worker._run_job(store.claim_next())   # run synchronously instead of in the thread

        done = http.get(f"/api/scans/{job['id']}", headers=h).json()
        assert done["status"] == "done", done["message"]
        assert done["summary"]["brands"] >= 3 and done["brands"][0]["handle"] == "gethingyms"
        assert "entities" in http.get(f"/api/scans/{job['id']}?entities=true", headers=h).json()

        csv = http.get(f"/api/scans/{job['id']}/files/brands.csv?api_key=secret-key")
        assert csv.status_code == 200 and "gethingyms" in csv.text

        # Another customer can't see it.
        other = {"X-API-Key": "other-key"}
        assert http.get(f"/api/scans/{job['id']}", headers=other).status_code == 404
        assert http.get("/api/scans", headers=other).json() == []

        queued = http.post("/api/scans", json={"target": "redbullindia"}, headers=h).json()
        assert http.post(f"/api/scans/{queued['id']}/cancel", headers=h).json()["status"] == "cancelled"

        # A cancelled scan can be retried, and then actually runs (not cancelled again).
        assert http.delete(f"/api/scans/{queued['id']}", headers=other).status_code == 404
        retried = http.post(f"/api/scans/{queued['id']}/retry", headers=h).json()
        assert retried["status"] == "queued" and retried["message"] == ""
        assert http.post(f"/api/scans/{queued['id']}/retry", headers=h).status_code == 409
        assert http.delete(f"/api/scans/{queued['id']}", headers=h).status_code == 409
        worker._run_job(store.claim_next())
        assert store.get(queued["id"])["status"] == "done"

        # A finished scan can be re-run, keeping its options, and deleted.
        again = http.post(f"/api/scans/{job['id']}/retry", json={"fresh": True}, headers=h).json()
        assert again["options"] == {"limit": 10, "ocr": True, "fresh": True} and again["files"] == []
        assert again["created_at"] > job["created_at"]
        worker._run_job(store.claim_next())
        assert http.delete(f"/api/scans/{job['id']}", headers=h).status_code == 204
        assert http.get(f"/api/scans/{job['id']}", headers=h).status_code == 404
        assert [j["id"] for j in http.get("/api/scans", headers=h).json()] == [queued["id"]]


def test_serve_refuses_public_without_keys(settings):
    from brandscan.server import serve
    with pytest.raises(SystemExit, match="API keys"):
        serve(settings, host="0.0.0.0")


def test_instagram_connection_api(settings):
    from fastapi.testclient import TestClient

    from brandscan import auth, server

    app = server.create_app(settings, start_worker=False)
    with TestClient(app) as http:
        assert http.get("/api/instagram").json()["connected"] is False
        assert http.post("/api/instagram/session", json={"username": "bad name!", "sessionid": "x" * 20}).status_code == 422

        info = http.post("/api/instagram/session",
                         json={"username": "@Scraper_1", "sessionid": "csrftoken=a; sessionid=123%3Aabcdefghij; mid=b"}).json()
        assert info["connected"] and info["username"] == "scraper_1"
        cookies = auth.session_from_settings(settings)
        assert cookies["sessionid"] == "123:abcdefghij" and cookies["ds_user_id"] == "123"

        assert http.delete("/api/instagram").json()["connected"] is False
        assert auth.session_from_settings(settings) is None

    # With API keys the shared session can't be changed from the dashboard.
    settings.api_keys = ["acme:secret-key"]
    with TestClient(server.create_app(settings, start_worker=False)) as http:
        h = {"X-API-Key": "secret-key"}
        assert http.get("/api/instagram", headers=h).json()["manageable"] is False
        assert http.delete("/api/instagram", headers=h).status_code == 403
