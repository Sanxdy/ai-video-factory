"""The static-export catch-all must not answer 200 for unknown paths.

`/{full_path:path}` is the SPA fallback. Every real page ships its own
`.html`, so anything that matches no file is a genuinely unknown path and
must 404 — serving the dashboard with a 200 hid typos and broken bookmarks.
"""
from fastapi.testclient import TestClient

from apps.api.server import app

client = TestClient(app)


def test_root_serves_dashboard():
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Video Factory" in r.text


def test_real_pages_still_resolve():
    for path in ("/settings", "/storyboard", "/gallery", "/analytics", "/system"):
        assert client.get(path).status_code == 200, path


def test_project_shell_still_resolves_for_any_id():
    # /project/<id> is the one dynamic route: the exported shell reads the
    # real id from the URL, so any id must load the shell.
    r = client.get("/project/290")
    assert r.status_code == 200


def test_unknown_path_is_404_not_dashboard():
    assert client.get("/nonsense-page").status_code == 404
    assert client.get("/api/no-such-endpoint").status_code == 404
