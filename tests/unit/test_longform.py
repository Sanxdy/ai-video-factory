"""Long-form 16:9 path: chunked script, deterministic storyboard, thumbnail.

The invariant under test is that adding long-form did not change Shorts:
every new parameter defaults to the old behaviour, so a project without a
`target_duration` must still take the Shorts code path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

import apps.scripting.script_gen as sg
import apps.storyboard.storyboard_gen as sbg
import core.settings as cs
from core.database import Database


@pytest.fixture()
def db(tmp_path):
    return Database(tmp_path / "test.db")


@pytest.fixture()
def settings(monkeypatch):
    """In-memory settings store, so tests never touch the production DB."""
    store = {}
    monkeypatch.setattr(cs, "get_setting", lambda k: store.get(k))
    monkeypatch.setattr(cs, "set_setting", lambda k, v: store.__setitem__(k, v))
    return store


# ── target duration is the single switch between Shorts and long-form ──

def test_no_target_duration_means_shorts(settings):
    assert sg._target_duration(7) == 0.0
    assert sg._max_words(7) == 150


def test_target_duration_drives_word_budget(settings):
    settings["project.7.target_duration"] = "330"
    assert sg._target_duration(7) == 330.0
    assert sg._max_words(7) == round(330 / 2.1)


def test_countdown_items_scales_with_duration():
    assert sg._countdown_items(330) == 5      # the approved 5-item test video
    assert sg._countdown_items(200) == 3      # floor
    assert sg._countdown_items(900) == 8      # ceiling


def test_topic_number_wins_over_the_duration_estimate():
    """A "6 Places" topic must produce 6 items, not the 5 that 360s implies.

    Project 296 was a 6-item topic on a 5-item count: the model numbered its
    items six..two, so the countdown never reached number one — the payoff the
    whole format is built on.
    """
    assert sg._countdown_items(360, "6 Places on Earth That Are Impossible to Reach") == 6
    assert sg._countdown_items(360, "Five Shipwrecks Found Somewhere Odd") == 5
    assert sg._countdown_items(360, "Places You Cannot Visit") == 5   # no number → duration
    assert sg._countdown_items(360, "12 Places You Cannot Visit") == 8  # clamped
    assert sg._topic_item_count("7 Cases That Were Never Solved") == 7
    assert sg._topic_item_count("The Deepest Place on Earth") is None


def test_countdown_ranks_run_down_to_number_one(db, settings, monkeypatch):
    """Every item gets an explicit rank, and the LAST one is number one."""
    settings["project.9.target_duration"] = "360"
    idea_id = db.insert("ideas", topic="6 Places You Cannot Reach",
                        status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    monkeypatch.setattr(sg, "generate_parsed",
                        lambda kind, prompt, temperature=0.2:
                        (asked.append(prompt), _Out())[1])
    sg.generate_script(db, idea_id, project_id=9)

    import re
    notes = [m for p in asked
             for m in re.findall(r"Ranking: (.*)", p)]
    item_notes = [n for n in notes if "ranked item number" in n]
    assert len(item_notes) == 6, f"expected 6 ranked items, got {item_notes}"
    # numbering descends: item 1 is number 6, the last item is number 1
    assert "item number 6 of 6" in item_notes[0]
    assert "item number 1 of 6" in item_notes[-1]
    # the intro is told the count, and the outro gets no rank at all
    assert any("counts down 6 items" in n for n in notes)
    assert sum(1 for n in notes if n.strip() == "") == 1


def test_intro_prompt_is_a_hook_not_a_summary(db, settings, monkeypatch):
    """The intro must hook, promise, and hand off — not summarise.

    A flat factual opener ("Point Nemo sits 2,688 km from land... We start at
    number six") gave the viewer nothing to wait for. The intro is now asked
    for a viewer-directed hook, a tease of number one, and an explicit hand-off
    into the countdown.
    """
    settings["project.9.target_duration"] = "360"
    idea_id = db.insert("ideas", topic="6 Places on Earth", status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    monkeypatch.setattr(sg, "generate_parsed",
                        lambda kind, prompt, temperature=0.2:
                        (asked.append(prompt), _Out())[1])
    sg.generate_script(db, idea_id, project_id=9)

    intro = [p for p in asked if "Section to write: intro" in p]
    assert len(intro) == 1, "exactly one intro section"
    body = intro[0]
    assert "HOOK" in body
    assert "three beats" in body
    assert "number one is the most extreme" in body   # the tease
    assert "starting the countdown out loud" in body  # the hand-off
    # items still open on a concrete fact, not a question
    item = [p for p in asked if "Section to write: item" in p][0]
    assert "Open each ITEM with something concrete" in item
    # escalation + vary-pattern rules are present for every item section
    assert "RANKING ESCALATION" in item
    assert "VARY DESCRIPTION PATTERNS" in item
    assert "culturally FAMOUS" in item


def test_longform_word_budget_uses_measured_pace(db, settings, monkeypatch):
    """Kokoro speaks ~3.1 words/sec, not the 2.1 the Shorts estimator assumes.

    Budgeting with 2.1 made a 330s target come back as a 221s video, so the
    per-section word request must be derived from the measured pace.
    """
    settings["project.9.target_duration"] = "330"
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    def fake_generate(kind, prompt, temperature=0.2):
        asked.append(prompt)
        return _Out()

    monkeypatch.setattr(sg, "generate_parsed", fake_generate)
    sg.generate_script(db, idea_id, project_id=9)

    # the prompt carries the per-section word target
    import re
    targets = [int(m) for p in asked
               for m in re.findall(r"Target length for THIS section: (\d+) spoken words", p)]
    assert targets, "word budget not passed to the section prompt"
    total = sum(targets[:1]) + sum(targets[1:-1]) + sum(targets[-1:])
    # the budget is in FINISHED seconds: 360s (330 raised to the floor) at
    # LONGFORM_VIDEO_WPS. Budgeting at the 3.1 speech pace overshot to 440s.
    assert total == round(360 * sg.LONGFORM_VIDEO_WPS) + 2, \
        f"budget {total} words does not match the finished-video rate"


def test_budget_uses_the_finished_video_rate_not_the_speech_pace(db, settings,
                                                                 monkeypatch):
    """The pad and the countdown holds live outside the narration.

    A finished video is speech + 0.25s of pad per scene + a 1.8s hold per
    section, so budgeting words at the 3.1 speech pace produced a 440s video
    for a 360s target (+22%). The budget must use the finished-video rate.
    """
    assert sg.LONGFORM_VIDEO_WPS < sg.LONGFORM_WPS, \
        "the finished-video rate must be lower than the speech pace"

    settings["project.9.target_duration"] = "360"
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    monkeypatch.setattr(sg, "generate_parsed",
                        lambda kind, prompt, temperature=0.2:
                        (asked.append(prompt), _Out())[1])
    sg.generate_script(db, idea_id, project_id=9)

    import re
    targets = [int(m) for p in asked
               for m in re.findall(r"Target length for THIS section: (\d+) spoken words", p)]
    total = sum(targets)
    # a perfectly compliant script must land on the target, not 22% over it
    projected = total / sg.LONGFORM_VIDEO_WPS
    assert 340 <= projected <= 380, \
        f"{total} words projects to {projected:.0f}s, not a 360s video"


def test_longform_budget_never_goes_below_five_minutes(db, settings, monkeypatch):
    """The user asked for "at least 5 minutes, never less".

    A project that asks for a shorter target must still be budgeted for the
    floor, otherwise the script comes back short and the render fails QC.
    """
    settings["project.9.target_duration"] = "120"     # under the floor
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    monkeypatch.setattr(sg, "generate_parsed",
                        lambda kind, prompt, temperature=0.2:
                        (asked.append(prompt), _Out())[1])
    sg.generate_script(db, idea_id, project_id=9)

    import re
    words = [int(m) for p in asked
             for m in re.findall(r"Target length for THIS section: (\d+) spoken words", p)]
    assert words, "word budget not passed to the section prompt"
    # the budget is expressed in finished seconds, so the floor check uses the
    # finished-video rate, not the speech pace
    assert sum(words) >= sg.LONGFORM_MIN_SECONDS * sg.LONGFORM_VIDEO_WPS, \
        f"{sum(words)} words is under the 5-minute floor"


# ── chunked script: one LLM call per section ──

def test_outro_prompt_carries_real_shorts_ctas(db, settings, monkeypatch):
    """The long-form outro must ask a viewer question, like the Shorts cta does.

    Project 296 ended on a flat "Thank you for watching", which drives no
    comments. The outro prompt is given the channel's own recent cta lines.
    """
    settings["project.9.target_duration"] = "360"
    db.insert("scripts", idea_id=db.insert("ideas", topic="Old Short"),
              version=1, hook="h", body="b",
              cta="Which of those records surprised you most? Tell me in the comments.",
              duration=45, status="DRAFT")
    idea_id = db.insert("ideas", topic="6 Places You Cannot Reach", status="NEW")
    asked = []

    class _Out:
        def model_dump(self):
            return {"heading": "H", "narration": "word " * 10}

    monkeypatch.setattr(sg, "generate_parsed",
                        lambda kind, prompt, temperature=0.2:
                        (asked.append(prompt), _Out())[1])
    sg.generate_script(db, idea_id, project_id=9)

    outro = [p for p in asked if "Section to write: outro" in p][0]
    assert "Which of those records surprised you most?" in outro, \
        "the outro prompt must show the channel's own cta voice"
    assert "direct question to the viewer" in outro


def test_longform_script_is_one_call_per_section(db, settings, monkeypatch):
    settings["project.9.target_duration"] = "330"
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    calls = []

    class _Out:
        def __init__(self, kind, i):
            self.kind, self.i = kind, i

        def model_dump(self):
            return {"heading": f"Chapter {self.i}",
                    "narration": f"Section {self.i} narration. " * 12}

    def fake_generate(kind, prompt, temperature=0.2):
        calls.append(kind)
        return _Out(kind, len(calls))

    monkeypatch.setattr(sg, "generate_parsed", fake_generate)
    sid = sg.generate_script(db, idea_id, project_id=9)

    # 5 items + intro + outro, each its own call — a single call for ~700
    # spoken words is what gets truncated
    assert len(calls) == 7
    assert set(calls) == {"longform"}
    script = db.get("scripts", sid)
    assert script["hook"].startswith("Section 1")
    assert script["cta"].startswith("Section 7")
    # chapter markers survive into the body for the storyboard to split on
    assert script["body"].count("## ") == 5


def test_shorts_script_path_untouched(db, settings, monkeypatch):
    """No target_duration → the original single-call Shorts generator."""
    idea_id = db.insert("ideas", topic="Why the Sky is Blue", status="NEW")
    kinds = []

    class _Out:
        def model_dump(self):
            return {"hook": "h", "body": "a b c d e " * 20, "cta": "c",
                    "duration_seconds": 45}

    def fake_generate(kind, prompt, temperature=0.2):
        kinds.append(kind)
        return _Out()

    monkeypatch.setattr(sg, "generate_parsed", fake_generate)
    sg.generate_script(db, idea_id, project_id=7)
    assert kinds == ["script"]


# ── deterministic storyboard split ──

def test_split_sections_parses_chapters():
    body = ("## First Place\nIt is very hot here.\n\n"
            "## Second Place\nIt is very cold here.")
    assert sbg._split_sections(body) == [
        ("First Place", "It is very hot here."),
        ("Second Place", "It is very cold here."),
    ]


def test_split_sections_handles_headingless_text():
    assert sbg._split_sections("just narration, no chapter") == [
        ("", "just narration, no chapter")]


def test_longform_scenes_split_word_for_word(monkeypatch):
    """The split must be lossless — the Shorts path relies on the LLM for
    this, which is exactly what breaks at 60+ scenes."""
    monkeypatch.setattr(sbg, "_visual_shots", lambda chunks: [
        {"visual_prompt": "shot", "stock_query": "q", "motion_prompt": "zoom-in"}
        for _ in chunks])
    hook = " ".join(["hookword"] * 55)
    cta = " ".join(["ctaword"] * 40)
    bodies = [" ".join([f"item{i}word"] * 120) for i in range(1, 6)]
    sections = "\n\n".join(f"## Item {i}\n{b}" for i, b in enumerate(bodies, 1))
    scenes = sbg._longform_scenes(
        {"hook": hook, "body": sections, "cta": cta}, scene_dur=5.0)

    # ~66 scenes for a 5.5-minute countdown at 5s per scene
    assert 55 <= len(scenes) <= 75
    assert [s["scene_number"] for s in scenes] == list(range(1, len(scenes) + 1))
    assert all(s["duration"] == 5.0 for s in scenes)
    # nothing dropped and nothing reordered (chapter headings are never spoken)
    joined = " ".join(s["narration"] for s in scenes).split()
    assert joined == f"{hook} {' '.join(bodies)} {cta}".split()


def test_split_never_cuts_mid_phrase():
    """Sentences are preferred; clauses are used when sentences are too few —
    a word-level cut in the middle of a phrase is audible in the final video."""
    text = "Alpha one, alpha two, alpha three, alpha four, alpha five, alpha six."
    assert sbg._split_into(text, 3) == [
        "Alpha one, alpha two,",
        "alpha three, alpha four,",
        "alpha five, alpha six.",
    ]


def test_split_uses_sentences_when_there_are_enough():
    text = "One is here. Two is here. Three is here. Four is here."
    assert sbg._split_into(text, 2) == ["One is here. Two is here.",
                                        "Three is here. Four is here."]


def test_longform_does_not_duplicate_the_cta(db, settings, monkeypatch):
    """The long-form cta is already the last scene; appending it again made the
    outro play twice (project 290, first smoke run)."""
    settings["project.9.target_duration"] = "330"
    settings["project.9.max_scene_duration"] = "5"
    monkeypatch.setattr(sbg, "_visual_shots", lambda chunks: [
        {"visual_prompt": "shot", "stock_query": "q", "motion_prompt": "zoom-in"}
        for _ in chunks])
    cta = "Subscribe for the next list"
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    sid = db.insert("scripts", idea_id=idea_id, version=1, hook="hook " * 30,
                    body="## Item\n" + "word " * 600, cta=cta,
                    duration=330, status="DRAFT")
    sbg.generate_storyboard(db, sid, project_id=9)
    narr = " ".join(s["narration"] for s in
                    db.all("scenes", "script_id=?", (sid,)))
    assert narr.count(cta) == 1


def test_shorts_path_still_appends_cta(db, settings, monkeypatch):
    """The Shorts path must keep appending the cta to the last scene."""
    monkeypatch.setattr(sbg, "generate_parsed", lambda *a, **k: _ShortOut())
    idea_id = db.insert("ideas", topic="Shorts Topic", status="NEW")
    sid = db.insert("scripts", idea_id=idea_id, version=1, hook="h",
                    body="b", cta="Follow for more", duration=40, status="DRAFT")
    sbg.generate_storyboard(db, sid, project_id=7)
    last = db.all("scenes", "script_id=? ORDER BY scene_number", (sid,))[-1]
    assert "Follow for more" in last["narration"]


class _ShortOut:
    def model_dump(self):
        return {"scenes": [{"scene_number": 1, "duration": 8.0,
                            "narration": "body", "visual_prompt": "a shot",
                            "stock_query": "q", "motion_prompt": "zoom-in",
                            "text_overlay": "", "asset_type": "image"}]}


def test_visual_failure_falls_back_to_narration(monkeypatch):
    """A visual description is cosmetic; a failing batch must not kill the run."""
    def boom(kind, prompt, temperature=0.2):
        raise RuntimeError("model at capacity")

    monkeypatch.setattr(sbg, "generate_parsed", boom)
    shots = sbg._visual_shots([("", "the narration text")])
    assert shots[0]["visual_prompt"] == "the narration text"


def test_longform_storyboard_branch_skips_llm_slicing(db, settings, monkeypatch):
    """generate_storyboard must not ask the model to slice a long script."""
    settings["project.9.target_duration"] = "330"
    settings["project.9.max_scene_duration"] = "5"
    monkeypatch.setattr(sbg, "_visual_shots", lambda chunks: [
        {"visual_prompt": "shot", "stock_query": "q", "motion_prompt": "zoom-in"}
        for _ in chunks])

    def no_storyboard_llm(kind, prompt, temperature=0.2):
        raise AssertionError(f"unexpected LLM call: {kind}")

    monkeypatch.setattr(sbg, "generate_parsed", no_storyboard_llm)
    idea_id = db.insert("ideas", topic="Extreme Places", status="NEW")
    sid = db.insert("scripts", idea_id=idea_id, version=1, hook="hook " * 30,
                    body="## Item\n" + "word " * 600, cta="cta " * 20,
                    duration=330, status="DRAFT")
    ids = sbg.generate_storyboard(db, sid, project_id=9)
    assert len(ids) > 10
    assert db.get("scripts", sid)["status"] == "STORYBOARDED"


# ── thumbnail ──

def test_set_thumbnail_posts_video_id(tmp_path, monkeypatch):
    import apps.uploader.youtube as yt

    img = tmp_path / "thumb.jpg"
    img.write_bytes(b"\xff\xd8jpegbytes")
    seen = {}

    class R:
        status_code = 200
        text = "{}"

    def fake_post(url, params=None, headers=None, data=None, timeout=None):
        seen.update(url=url, params=params, headers=headers, data=data)
        return R()

    monkeypatch.setattr(yt, "_access_token", lambda creds: "tok")
    monkeypatch.setattr(yt, "_creds", lambda: object())
    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    assert yt.set_thumbnail("abc123", img) is True
    assert seen["url"] == yt.THUMB_URL
    assert seen["params"] == {"videoId": "abc123"}
    assert seen["headers"]["Content-Type"] == "image/jpeg"
    assert seen["data"] == b"\xff\xd8jpegbytes"


def test_set_thumbnail_failure_is_not_fatal(tmp_path, monkeypatch):
    import apps.uploader.youtube as yt
    import requests

    img = tmp_path / "thumb.jpg"
    img.write_bytes(b"x")

    class R:
        status_code = 403
        text = "forbidden"

    monkeypatch.setattr(yt, "_access_token", lambda creds: "tok")
    monkeypatch.setattr(yt, "_creds", lambda: object())
    monkeypatch.setattr(requests, "post",
                        lambda *a, **k: R())
    assert yt.set_thumbnail("abc123", img) is False


# ── resolution helper ──

def test_resolution_helper():
    from apps.editor.editor import resolution
    assert resolution("16:9") == "1920x1080"
    assert resolution("9:16") == "1080x1920"
    assert resolution(None) == "1080x1920"   # default = Shorts
