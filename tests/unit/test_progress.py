"""core/progress: store/clamp/emit/clear lifecycle."""
from core import events
from core.progress import clear_progress, get_progress, set_progress


def test_set_get_clear(monkeypatch):
    emitted = []
    monkeypatch.setattr(events, "emit", lambda ev, **kw: emitted.append((ev, kw)))
    clear_progress(9001)

    assert get_progress(9001) is None

    set_progress(9001, "EDITING", 60, "Rendering scene 1/5")
    p = get_progress(9001)
    assert p["stage"] == "EDITING" and p["detail"] == "Rendering scene 1/5"
    assert p["pct"] == 60 and p["started"] > 0
    assert emitted[-1] == ("project_state", {"project_id": 9001})

    started = p["started"]
    set_progress(9001, "EDITING", 150)  # clamp + keep started
    p = get_progress(9001)
    assert p["pct"] == 100 and p["started"] == started and p["detail"] == ""

    clear_progress(9001)
    assert get_progress(9001) is None
