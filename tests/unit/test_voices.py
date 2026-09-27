"""The voice registry: AUTO voices, Piper's native accents, legacy migration.

The borrowed de/id English voices are gone — a viewer hears the wrong accent
immediately, however well a Whisper round-trip scores. Everything Piper
exposes must be native-trained, and AUTO must resolve by the script's text.
"""
from providers.tts.kokoro import (AUTO_VOICE_MAP, LEGACY_VOICE_MAP,
                                  language_for_voice, narration_language,
                                  resolve_auto_voice, voice_ids,
                                  voice_options)
from providers.tts.piper import voice_urls


ID_TEXT = ("Pemerintah mengumumkan rencana transportasi baru hari ini untuk "
           "mengurangi kemacetan di ibu kota.")
DE_TEXT = ("Die Regierung hat heute einen neuen Verkehrsplan für die "
           "Hauptstadt vorgestellt.")


def test_auto_voices_are_listed_first():
    options = voice_options()
    assert options[0]["id"] == "auto-female"
    assert options[1]["id"] == "auto-male"
    assert "Auto" in options[0]["group"]


def test_borrowed_voices_are_gone():
    ids = voice_ids()
    for legacy in LEGACY_VOICE_MAP:
        assert legacy not in ids, \
            f"{legacy} sounds English reading its target language — remove it"
    for option in voice_options():
        assert "experimental" not in option["group"]


def test_piper_voices_are_native_grouped():
    ids = voice_ids()
    assert "piper:id_ID-news_tts-medium" in ids
    assert "piper:de_DE-thorsten-medium" in ids
    groups = {o["id"]: o["group"] for o in voice_options()}
    assert groups["piper:id_ID-news_tts-medium"] == "Indonesian (native)"
    assert groups["piper:de_DE-thorsten-medium"] == "German (native)"


def test_every_mapped_auto_voice_exists():
    """A typo in AUTO_VOICE_MAP would surface as 'unknown voice' at synth time
    — mid-pipeline, on the user's machine. Check the registry here instead."""
    ids = voice_ids()
    for pair in AUTO_VOICE_MAP.values():
        for voice in pair:
            assert voice in ids, f"AUTO_VOICE_MAP entry '{voice}' is not registered"


def test_resolve_auto_follows_the_text_language():
    assert resolve_auto_voice("auto-female", ID_TEXT) == "piper:id_ID-news_tts-medium"
    assert resolve_auto_voice("auto-male", ID_TEXT) == "piper:id_ID-news_tts-medium"
    assert resolve_auto_voice("auto-female", DE_TEXT) == "piper:de_DE-ramona-low"
    assert resolve_auto_voice("auto-male", DE_TEXT) == "piper:de_DE-thorsten-medium"
    assert resolve_auto_voice("auto-female",
                              "The government announced a new transport plan today.") == "af_heart"
    assert resolve_auto_voice("auto-male",
                              "The government announced a new transport plan today.") == "am_michael"


def test_resolve_auto_is_deterministic_and_falls_back():
    # langdetect is seeded; the same text must resolve the same voice twice
    first = resolve_auto_voice("auto-female", ID_TEXT)
    assert first == resolve_auto_voice("auto-female", ID_TEXT)
    # a language with no voices falls back to English, not to a crash
    fallback = resolve_auto_voice("auto-female", "النص العربي هنا قصير جدا")
    assert fallback in voice_ids()
    # non-auto ids pass through: a chosen voice is never second-guessed
    assert resolve_auto_voice("am_michael", ID_TEXT) == "am_michael"


def test_legacy_setting_maps_to_native_successor():
    assert LEGACY_VOICE_MAP["id_am_michael"] == "piper:id_ID-news_tts-medium"
    assert LEGACY_VOICE_MAP["de_af_nova"] == "piper:de_DE-ramona-low"


def test_language_for_voice():
    assert language_for_voice("piper:id_ID-news_tts-medium") == "id"
    assert language_for_voice("auto-female") == "en-us"
    assert language_for_voice("ef_dora") == "es"


def test_narration_language_none_for_auto(monkeypatch):
    import core.settings as settings
    monkeypatch.setattr(settings, "get_setting", lambda k, d=None: "auto-female")
    assert narration_language() is None
    monkeypatch.setattr(settings, "get_setting",
                        lambda k, d=None: "ef_dora")
    assert narration_language() == "Spanish"


def test_piper_urls_match_the_repo_layout():
    onnx, js = voice_urls("id_ID-news_tts-medium")
    assert onnx == ("https://huggingface.co/rhasspy/piper-voices/resolve/main"
                    "/id/ID/news_tts/medium/id_ID-news_tts-medium.onnx")
    assert js == onnx + ".json"
