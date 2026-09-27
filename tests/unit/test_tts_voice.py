"""Narration voice settings: validation, persistence, TTS override."""
import pytest
from fastapi.testclient import TestClient

import apps.api.server as srv


@pytest.fixture()
def client(monkeypatch, tmp_path):
    with TestClient(srv.app) as c:
        yield c


def test_get_returns_valid_voice(client):
    r = client.get("/api/settings/voice").json()
    ids = {v["id"] for v in r["voices"]}
    assert r["voice"] in ids and len(ids) >= 8


def test_unknown_voice_422(client):
    assert client.post("/api/settings/voice",
                       json={"voice": "id_gadis"}).status_code == 422
    assert client.post("/api/settings/voice",
                       json={"voice": "not_a_voice"}).status_code == 422


def test_save_and_tts_picks_it_up(client, monkeypatch, tmp_path):
    assert client.post("/api/settings/voice",
                       json={"voice": "bm_george"}).status_code == 200
    assert client.get("/api/settings/voice").json()["voice"] == "bm_george"
    from providers.tts.kokoro import KokoroTTS
    assert KokoroTTS().voice == "bm_george"


def test_espeak_data_path_stays_under_the_crash_threshold(monkeypatch, tmp_path):
    """A 160-char data path makes espeak-ng exit(1) the whole process."""
    from providers.tts.kokoro import _espeak_data_dir
    monkeypatch.setenv("AVF_DATA_DIR", str(tmp_path))
    d = _espeak_data_dir()
    assert (d / "phontab").exists()
    # resolved, not just as-returned: phonemizer Path.resolve()s the path before
    # handing it to the library, so a symlink would expand back and defeat this
    assert len(str(d.resolve())) <= 159


def test_narration_language_follows_the_voice(client, monkeypatch):
    """The script prompts must ask for the language the voice speaks: an
    English voice reading an Indonesian script is espeak mangling English into
    Indonesian phonemes. Piper's de/id voices are native — the language they
    speak must flow into the prompts the same way Kokoro's do."""
    from providers.tts.kokoro import narration_language
    assert client.post("/api/settings/voice",
                       json={"voice": "piper:id_ID-news_tts-medium"}).status_code == 200
    assert narration_language() == "Indonesian"
    assert client.post("/api/settings/voice",
                       json={"voice": "piper:de_DE-thorsten-medium"}).status_code == 200
    assert narration_language() == "German"
    assert client.post("/api/settings/voice",
                       json={"voice": "ef_dora"}).status_code == 200
    assert narration_language() == "Spanish"
    assert client.post("/api/settings/voice",
                       json={"voice": "af_heart"}).status_code == 200
    assert narration_language() == "English"
    # an auto voice has no fixed language — the topic's text decides
    assert client.post("/api/settings/voice",
                       json={"voice": "auto-female"}).status_code == 200
    assert narration_language() is None
