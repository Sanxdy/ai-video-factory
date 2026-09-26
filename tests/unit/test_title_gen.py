"""The title guard: published titles must not come back as new suggestions."""
from __future__ import annotations

from apps.research import title_gen as tg


class _Out:
    def __init__(self, youtube_titles, **kw):
        self._d = {"youtube_titles": youtube_titles, "short_titles": [],
                   "description": "d", "hashtags": [], "tags": [], **kw}

    def model_dump(self):
        return self._d


def _patch(monkeypatch, titles, captured=None):
    def fake(kind, prompt):
        if captured is not None:
            captured["prompt"] = prompt
        return _Out(titles)
    monkeypatch.setattr(tg, "generate_parsed", fake)


def test_duplicate_suggestion_is_dropped(monkeypatch):
    published = ["7 Animals With Abilities Science Still Can't Explain"]
    _patch(monkeypatch, ["7 Animals With Abilities Science Still Can't Explain",
                         "5 Machines That Changed Everything"])
    out = tg.generate_titles("topic", existing_titles=published)
    assert [t["title"] for t in out["titles"]] == ["5 Machines That Changed Everything"]
    assert out["best"]["title"] == "5 Machines That Changed Everything"


def test_published_titles_are_shown_to_the_llm(monkeypatch):
    captured = {}
    _patch(monkeypatch, ["A Fresh Title"], captured)
    tg.generate_titles("topic", existing_titles=["Old Video Title"])
    assert "- Old Video Title" in captured["prompt"]


def test_all_duplicates_falls_back_to_llm_order(monkeypatch):
    published = ["6 Lost Civilizations Found in Impossible Places"]
    _patch(monkeypatch, ["6 Lost Civilizations Found in Impossible Places"])
    out = tg.generate_titles("topic", existing_titles=published)
    # never return an empty set — a repeated title beats no title at all
    assert out["best"]["title"] == "6 Lost Civilizations Found in Impossible Places"


def test_empty_list_skips_the_channel_lookup(monkeypatch):
    def boom(**kw):
        raise AssertionError("channel lookup must not run when existing_titles=[]")
    monkeypatch.setattr(tg, "_channel_titles", boom)
    _patch(monkeypatch, ["Fresh Title"])
    out = tg.generate_titles("topic", existing_titles=[])
    assert out["best"]["title"] == "Fresh Title"


def test_channel_lookup_returns_empty_offline(monkeypatch):
    # _channel_titles swallows its own failures (no creds / quota) and yields []
    from apps.uploader import youtube as yt

    monkeypatch.setattr(yt, "list_channel_videos",
                        lambda max_results=200: (_ for _ in ()).throw(RuntimeError("quota")))
    assert tg._channel_titles() == []


def test_no_titles_yet_renders_a_placeholder(monkeypatch):
    captured = {}
    _patch(monkeypatch, ["Fresh Title"], captured)
    tg.generate_titles("topic", existing_titles=[])
    assert "(none yet)" in captured["prompt"]
