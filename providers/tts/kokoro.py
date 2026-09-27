"""Kokoro TTS provider (spec §16) — local, no API key.

Backend support:
1. kokoro-onnx (CPU, works on Linux ARM + macOS) — primary for VPS proof
2. kokoro-mlx (macOS Apple Silicon) — production backend on Mac

Voice config comes from config/app.yaml tts.voice.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from core.config import config, data_dir
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("tts")

# espeak-ng aborts the whole process — exit(1), no traceback, nothing in the
# log — once its data path reaches 160 characters, and then falls back to the
# path baked in when the library was built (…/Users/runner/work/…), which
# exists only on the build machine. The .app bundle's own copy sits at 167.
# Measured on espeak-ng 1.52: 159 characters works, 160 kills the process.
_ESPEAK_PATH_MAX = 159

# Kokoro v1.0 voices, grouped by the language espeak phonemizes them in. Only
# the ids are listed: the display name is the id after the prefix, and the
# prefix itself carries the language and the gender.
#
# ja and cmn are deliberately absent. Kokoro was trained on misaki's dictionary
# phonemes for those two, and espeak's substitutes are wrong rather than merely
# accented — 世界 phonemizes to "(en)chinese(ja)lete". Adding them means the
# misaki[ja] / misaki[zh] extras, not a longer list here.
VOICES: dict[str, tuple[str, ...]] = {
    "en-us": ("af_heart", "af_alloy", "af_aoede", "af_bella", "af_jessica",
              "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah",
              "af_sky", "am_adam", "am_echo", "am_eric", "am_fenrir",
              "am_liam", "am_michael", "am_onyx", "am_puck", "am_santa"),
    "en-gb": ("bf_alice", "bf_emma", "bf_isabella", "bf_lily",
              "bm_daniel", "bm_fable", "bm_george", "bm_lewis"),
    "es": ("ef_dora", "em_alex", "em_santa"),
    "fr-fr": ("ff_siwis",),
    "hi": ("hf_alpha", "hf_beta", "hm_omega", "hm_psi"),
    "it": ("if_sara", "im_nicola"),
    "pt-br": ("pf_dora", "pm_alex", "pm_santa"),
}

# Languages Kokoro v1.0 was never trained on (German, Indonesian) used to be
# served by "borrowed" English voices — intelligible on a Whisper round-trip,
# but sounding English, which viewers hear immediately. They are gone: those
# languages now come from Piper (providers/tts/piper.py), whose voices are
# trained per language and accent it natively.
_ALL_VOICES: dict[str, tuple[str, ...]] = {**VOICES}

LANGUAGE_LABELS = {
    "en-us": "English (US)", "en-gb": "English (UK)", "es": "Spanish",
    "fr-fr": "French", "hi": "Hindi", "it": "Italian",
    "pt-br": "Portuguese (Brazil)",
}

# espeak language code → the language name the LLM prompts ask for. The
# two-letter Piper codes ("id", "de") sit in the same table: language_for_voice
# returns them for piper: ids, and narration_language() names the language.
PROMPT_LANGUAGE = {
    "en-us": "English", "en-gb": "English", "es": "Spanish", "fr-fr": "French",
    "hi": "Hindi", "it": "Italian", "pt-br": "Portuguese (Brazil)",
    "de": "German", "id": "Indonesian",
}

# voice-id prefix → espeak language. Derived here so no call site has to pass
# one: synth() never had a language argument and still does not need one.
_LANG_BY_PREFIX = {v[:2]: lang for lang, ids in _ALL_VOICES.items() for v in ids}

# AUTO voices: the video follows its text. The topic is Indonesian → the script
# is written in Indonesian and an Indonesian voice reads it; French topic →
# French. The user only picks the gender. Resolved per project in
# stage_script once the script's language is known (langdetect on the body).
AUTO_GROUP = "Auto — follows the video's language"
AUTO_VOICE_NAMES = {
    "en": ("Heart", "Michael"), "en-gb": ("Alice", "Daniel"),
    "es": ("Dora", "Alex"), "fr": ("Siwis", "Gilles"),
    "de": ("Ramona", "Thorsten"), "id": ("News reader", "News reader"),
    "it": ("Sara", "Nicola"), "pt": ("Dora", "Alex"), "hi": ("Alpha", "Omega"),
}
AUTO_DEFAULT_LANGUAGE = "en"

# language → (female voice id, male voice id). Kokoro where it is native,
# Piper where only Piper has the language, and for French's male voice
# (Kokoro ships exactly one French voice, and she is female).
AUTO_VOICE_MAP: dict[str, tuple[str, str]] = {
    "en": ("af_heart", "am_michael"),
    "es": ("ef_dora", "em_alex"),
    "fr": ("ff_siwis", "piper:fr_FR-gilles-low"),
    "de": ("piper:de_DE-ramona-low", "piper:de_DE-thorsten-medium"),
    "id": ("piper:id_ID-news_tts-medium", "piper:id_ID-news_tts-medium"),
    "it": ("if_sara", "im_nicola"),
    "pt": ("pf_dora", "pm_alex"),
    "hi": ("hf_alpha", "hm_omega"),
}

# ids that existed before Piper (the borrowed de/id English voices). A setting
# still holding one is translated at read time to its native-accent successor —
# the closest voice to what the user picked, in the language they wanted.
LEGACY_VOICE_MAP: dict[str, str] = {
    "de_af_nova": "piper:de_DE-ramona-low",
    "de_am_michael": "piper:de_DE-thorsten-medium",
    "id_af_nova": "piper:id_ID-news_tts-medium",
    "id_am_michael": "piper:id_ID-news_tts-medium",
}


def resolve_auto_voice(voice: str, text: str) -> str:
    """The concrete voice for an auto-* id, chosen by the text's language.

    Runs on the finished script (a paragraph or two — far more reliable than
    a two-word topic). The detector is restricted to the languages AVF has
    voices for; anything it cannot place falls back to English, and a non-auto
    id passes through untouched.
    """
    if voice not in ("auto-female", "auto-male"):
        return voice
    gender = 0 if voice == "auto-female" else 1
    lang = AUTO_DEFAULT_LANGUAGE
    try:
        from lingua import Language, LanguageDetectorBuilder
        detector = LanguageDetectorBuilder.from_languages(
            *[l for l in (Language.ENGLISH, Language.GERMAN, Language.FRENCH,
                          Language.SPANISH, Language.ITALIAN,
                          Language.PORTUGUESE, Language.HINDI,
                          Language.INDONESIAN)
              if AUTO_VOICE_MAP.get(l.iso_code_639_1.name.lower())]
        ).build()
        detected = detector.detect_language_of(text)
        if detected is not None:
            lang = detected.iso_code_639_1.name.lower()
    except Exception:
        pass
    pair = AUTO_VOICE_MAP.get(lang)
    if pair is None:
        log.info("auto voice: language '%s' has no voices — using English", lang)
        pair = AUTO_VOICE_MAP[AUTO_DEFAULT_LANGUAGE]
    else:
        log.info("auto voice: script language '%s' → %s", lang, pair[gender])
    return pair[gender]


def model_voice(voice: str) -> str:
    """The voice id the model actually knows.

    Borrowed-language ids carried their language as an extra prefix
    ("de_am_michael"); the model has no such entry, only "am_michael". Native
    ids pass through untouched.
    """
    parts = voice.split("_")
    return "_".join(parts[1:]) if len(parts) > 2 else voice


def narration_language() -> str | None:
    """The language the LLM must WRITE the narration in: the voice's.

    A Spanish voice reading an English script is not a Spanish video — espeak
    mangles English words into the target language's phonemes, and the Whisper
    round-trip scores that at 59-86% versus 96% for a script written in the
    voice's own language. Every narration-writing prompt takes this.

    With an auto voice the video's language follows the topic instead, so this
    returns None and the prompt says so — the concrete voice is resolved after
    the script exists (resolve_auto_voice).
    """
    from core.settings import get_setting
    voice = (get_setting("tts.voice")
             or (config.get("tts", default={}) or {}).get("voice", "af_heart"))
    if voice in ("auto-female", "auto-male"):
        return None
    return PROMPT_LANGUAGE.get(language_for_voice(voice), "English")


def voice_options() -> list[dict[str, str]]:
    """Flat {id, label, group} list for the settings API and the wizard.

    `group` is the language heading the UI shows as an <optgroup>. The two
    auto voices come first, then Kokoro's native languages, then Piper's —
    each piper id groups under a "<Language> (native)" heading.
    """
    from providers.tts.piper import VOICES as PIPER_VOICES

    out = [{"id": "auto-female", "group": AUTO_GROUP, "label": "Auto (female)"},
           {"id": "auto-male", "group": AUTO_GROUP, "label": "Auto (male)"}]
    for lang, ids in _ALL_VOICES.items():
        for v in ids:
            parts = v.split("_")
            gender = "female" if parts[-2][1] == "f" else "male"
            out.append({"id": v, "group": LANGUAGE_LABELS[lang],
                        "label": f"{parts[-1].capitalize()} ({gender})"})
    for key, entry in PIPER_VOICES.items():
        out.append({"id": f"piper:{key}",
                    "group": f"{entry['lang'].capitalize()} (native)",
                    "label": f"{entry['name']} ({entry['gender']})"})
    return out


def voice_ids() -> set[str]:
    return {o["id"] for o in voice_options()}


def language_for_voice(voice: str) -> str:
    """espeak language for a voice id; unknown prefixes fall back to English."""
    if voice.startswith("piper:"):
        from providers.tts.piper import language_for_voice as piper_lang
        return piper_lang(voice) or "en-us"
    if voice in ("auto-female", "auto-male"):
        return f"{AUTO_DEFAULT_LANGUAGE}-us" if AUTO_DEFAULT_LANGUAGE == "en" else AUTO_DEFAULT_LANGUAGE
    return _LANG_BY_PREFIX.get(voice[:2], "en-us")


def _espeak_data_dir() -> Path:
    """A short local copy of espeak-ng's data dir, made once.

    A symlink would not do: phonemizer calls Path.resolve() on the path before
    handing it to the library, which expands the link back to the long target.
    """
    import tempfile

    from espeakng_loader import get_data_path

    candidates = (data_dir() / "runtime" / "espeak-ng-data",
                  Path(tempfile.gettempdir()) / "avf-espeak-ng-data")
    target = next((c for c in candidates if len(str(c)) <= _ESPEAK_PATH_MAX),
                  None)
    if target is None:
        raise ProviderError(
            f"espeak-ng aborts the process when its data path reaches "
            f"{_ESPEAK_PATH_MAX + 1} characters, and neither {data_dir()} nor "
            f"{tempfile.gettempdir()} is short enough — install AVF closer to "
            f"the filesystem root")

    if not (target / "phontab").exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(get_data_path(), target, dirs_exist_ok=True)
    return target


class KokoroTTS:
    """The TTS facade every caller uses, despite the name.

    Voices starting with "piper:" route to the Piper engine (native accents
    for languages Kokoro cannot do); everything else stays Kokoro.
    """

    def __init__(self, voice: str | None = None, speed: float | None = None,
                 voice_override: str | None = None):
        tts_cfg = config.get("tts", default={}) or {}
        from core.settings import get_setting
        # a per-project override (an auto voice resolved to the script's
        # language) wins; then the runtime setting (with pre-Piper borrowed ids
        # translated to their native-accent successors), the explicit arg, app.yaml
        stored = LEGACY_VOICE_MAP.get(get_setting("tts.voice") or "",
                                      get_setting("tts.voice"))
        self.voice = (voice_override
                      or stored
                      or voice
                      or tts_cfg.get("voice", "af_heart"))
        self.language = language_for_voice(self.voice)
        self.speed = speed if speed is not None else tts_cfg.get("speed", 1.0)
        self._onnx_ok = False
        self._mlx_ok = False

    def available(self) -> bool:
        """Both backends probed; True if at least one works."""
        if self.voice.startswith("piper:"):
            return True
        if shutil.which("kokoro"):
            self._mlx_ok = True
            return True
        try:
            from kokoro_onnx import Kokoro  # noqa
            self._onnx_ok = True
            return True
        except ImportError:
            pass
        return False

    def synth(self, text: str, out_path: Path,
              language: str | None = None) -> Path:
        """Synthesize text → WAV at out_path. Returns path.

        language defaults to the selected voice's own language, so a Spanish
        voice reads Spanish without every caller having to know that.
        """
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if self.voice.startswith("piper:"):
            from providers.tts.piper import PiperTTS
            return PiperTTS(self.voice, speed=self.speed).synth(text, out_path)

        lang = language or self.language

        self.available()  # ensure flags set in this process

        cli = self._mlx_ok or shutil.which("kokoro")
        # the `kokoro` CLI takes no language flag, so it is only correct for
        # English; anything else goes through onnx, which does
        if cli and lang.startswith("en"):
            return self._synth_cli(text, out_path)
        if self._onnx_ok:
            return self._synth_onnx(text, out_path, lang)
        if cli:
            return self._synth_cli(text, out_path)

        raise ProviderError("Kokoro not installed — pip install kokoro-onnx "
                            "(or `kokoro` CLI on macOS)")

    def _synth_cli(self, text: str, out_path: Path) -> Path:
        cmd = ["kokoro", "-t", text, "-v", self.voice, "-o", str(out_path)]
        if self.speed != 1.0:
            cmd += ["-s", str(self.speed)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode != 0 or not out_path.exists():
            raise ProviderError(f"kokoro CLI failed: {r.stderr[:200]}")
        return out_path

    def _synth_onnx(self, text: str, out_path: Path, language: str) -> Path:
        import numpy as np
        import soundfile as sf
        from kokoro_onnx import EspeakConfig, Kokoro

        # before _ensure_models: a bad espeak path must fail in a second, not
        # after a 310MB model download
        espeak_data = _espeak_data_dir()
        model, voices = self._ensure_models()
        kokoro = Kokoro(str(model), str(voices),
                        espeak_config=EspeakConfig(data_path=str(espeak_data)))
        samples, sample_rate = kokoro.create(text, voice=model_voice(self.voice),
                                             speed=self.speed, lang=language)
        sf.write(str(out_path), np.array(samples), sample_rate)
        return out_path

    def _ensure_models(self) -> tuple[Path, Path]:
        """Download kokoro models (first run ~300MB) to runtime/models."""
        import urllib.request
        models_dir = data_dir() / "runtime" / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        model = models_dir / "kokoro-v1.0.onnx"
        voices = models_dir / "voices-v1.0.bin"
        urls = {
            model: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
            voices: "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
        }
        for path, url in urls.items():
            if not path.exists():
                log.info("downloading %s → %s (%.0fMB)", url.split("/")[-1],
                         path, 300 if path == model else 10)
                urllib.request.urlretrieve(url, path)
        return model, voices