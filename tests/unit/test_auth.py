"""Auth gate tests (isolated settings DB via unit conftest)."""
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from apps.api import auth as auth_mod


@pytest.fixture()
def client():
    # fresh signing secret per test so tokens never leak across cases
    from apps.api.server import app
    with TestClient(app) as c:
        yield c


def test_no_password_bootstrap_first_login(client):
    assert client.get("/api/auth/status").json() == {"has_password": False}
    r = client.post("/api/auth/login", json={"password": "hunter2hunter2"})
    assert r.status_code == 200
    assert "avf_session" in r.cookies
    assert client.get("/api/auth/status").json()["has_password"] is True


def test_wrong_password_rejected(client):
    client.post("/api/auth/login", json={"password": "correcthorse"})
    out = client.post("/api/auth/login", json={"password": "wrong"})
    # fresh TestClient per request chain shares cookies; wrong password → 401
    c2 = TestClient(client.app)
    assert c2.post("/api/auth/login", json={"password": "nope12345"}).status_code == 401


def test_api_blocked_without_session(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/settings/schedule", json={}).status_code == 401
    assert client.get("/api/settings/llm").status_code == 401


def test_api_open_after_login(client):
    client.post("/api/auth/login", json={"password": "open sesame seed"})
    assert client.get("/api/projects").status_code == 200
    assert client.get("/api/music").status_code == 200


def test_browser_redirects_to_login(client):
    r = client.get("/api/projects", headers={"accept": "text/html"},
                   follow_redirects=False)
    assert r.status_code == 302 and "/login" in r.headers["location"]
    assert client.get("/login").status_code == 200


def test_logout_kills_access(client):
    client.post("/api/auth/login", json={"password": "byebyenow1"})
    assert client.get("/api/projects").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/projects").status_code == 401


def test_password_min_length():
    with pytest.raises(ValueError):
        auth_mod.set_password("short")


def test_static_assets_stay_open(client):
    # login page + static frontend must not require a session
    assert client.get("/login").status_code == 200


def test_health_open_without_session(client):
    """The desktop launcher polls /api/health to know the server is up — before
    anyone has logged in. It is exempt via auth.OPEN, not by a hardcoded path."""
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True