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


def test_borrowed_language_voices():
    """German and Indonesian ride English voices under a language prefix.

    Kokoro v1.0 ships 54 voices and none of them speaks de or id — espeak-ng
    phonemizes both, so an English voice reads the target phonemes. The exposed
    id carries the language ("de_am_michael") so the picker stays unique and the
    language survives the settings round-trip; model_voice() strips it for the
    model lookup.
    """
    from providers.tts.kokoro import (language_for_voice, model_voice,
                                      narration_language, voice_ids,
                                      voice_options)
    ids = voice_ids()
    assert {"de_af_nova", "de_am_michael", "id_af_nova", "id_am_michael"} <= ids
    assert "am_michael" in ids and "af_heart" in ids      # natives untouched

    assert model_voice("de_am_michael") == "am_michael"
    assert model_voice("am_michael") == "am_michael"      # native passthrough
    assert language_for_voice("de_am_michael") == "de"
    assert language_for_voice("id_am_michael") == "id"
    assert language_for_voice("am_michael") == "en-us"    # no prefix collision

    opts = {o["id"]: o for o in voice_options()}
    assert opts["de_am_michael"]["group"] == "German (experimental)"
    assert opts["de_am_michael"]["label"] == "Michael (male)"
    assert opts["id_af_nova"]["label"] == "Nova (female)"
    assert opts["af_heart"]["label"] == "Heart (female)"  # native labels intact


def test_narration_language_follows_the_voice(client, monkeypatch):
    """The script prompts must ask for the language the voice speaks: an
    Indonesian voice reading an English script is espeak mangling English into
    Indonesian phonemes."""
    from providers.tts.kokoro import narration_language
    assert client.post("/api/settings/voice",
                       json={"voice": "id_am_michael"}).status_code == 200
    assert narration_language() == "Indonesian"
    assert client.post("/api/settings/voice",
                       json={"voice": "de_am_michael"}).status_code == 200
    assert narration_language() == "German"
    assert client.post("/api/settings/voice",
                       json={"voice": "ef_dora"}).status_code == 200
    assert narration_language() == "Spanish"
    assert client.post("/api/settings/voice",
                       json={"voice": "af_heart"}).status_code == 200
    assert narration_language() == "English"
