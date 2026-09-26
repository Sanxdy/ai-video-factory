"""YouTube settings endpoints: secret validation, auth URL, status."""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(srv, "CLIENT_SECRET", tmp_path / "client_secret.json", raising=False)
    monkeypatch.setattr(srv, "TOKEN_PATH", tmp_path / "youtube_token.json", raising=False)
    # endpoints import the paths from the uploader module — patch there too
    from apps.uploader import youtube as yt
    monkeypatch.setattr(yt, "CLIENT_SECRET", tmp_path / "client_secret.json")
    monkeypatch.setattr(yt, "TOKEN_PATH", tmp_path / "youtube_token.json")
    with TestClient(srv.app) as c:
        yield c


SECRET = '{"installed":{"client_id":"abc","client_secret":"shh"}}'


def test_status_empty(client):
    r = client.get("/api/settings/youtube").json()
    assert r == {"connected": False, "has_secret": False}


def test_secret_validation(client):
    assert client.post("/api/settings/youtube/secret",
                       json={"content": '{"web":{}}'}).status_code == 422
    assert client.post("/api/settings/youtube/secret",
                       json={"content": "not json"}).status_code == 422
    assert client.post("/api/settings/youtube/secret",
                       json={"content": SECRET}).status_code == 200
    assert client.get("/api/settings/youtube").json()["has_secret"] is True


def test_start_builds_consent_url(client):
    assert client.post("/api/settings/youtube/start", json={}).status_code == 409
    client.post("/api/settings/youtube/secret", json={"content": SECRET})
    r = client.post("/api/settings/youtube/start", json={})
    assert r.status_code == 200
    assert "accounts.google.com" in r.json()["url"] and "client_id=abc" in r.json()["url"]
