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

# Languages Kokoro v1.0 was never trained on — there is no de_* or id_* voice
# in voices-v1.0.bin (54 voices, enumerated). espeak-ng phonemizes both fine,
# though, and an English voice reading those phonemes is accented but
# intelligible: a Whisper round-trip (source text in, transcription of the
# audio out) scored German 96% and Indonesian 86% against a 100% English
# control. The exposed id carries the target language as an extra prefix, so
# picker entries stay unique and the language survives the settings round-trip;
# model_voice() strips it before the model lookup.
BORROWED: dict[str, tuple[str, ...]] = {
    "de": ("de_af_nova", "de_am_michael"),
    "id": ("id_af_nova", "id_am_michael"),
}

_ALL_VOICES: dict[str, tuple[str, ...]] = {**VOICES, **BORROWED}

LANGUAGE_LABELS = {
    "en-us": "English (US)", "en-gb": "English (UK)", "es": "Spanish",
    "fr-fr": "French", "hi": "Hindi", "it": "Italian",
    "pt-br": "Portuguese (Brazil)", "de": "German (experimental)",
    "id": "Indonesian (experimental)",
}

# espeak language code → the language name the LLM prompts ask for.
PROMPT_LANGUAGE = {
    "en-us": "English", "en-gb": "English", "es": "Spanish", "fr-fr": "French",
    "hi": "Hindi", "it": "Italian", "pt-br": "Portuguese (Brazil)",
    "de": "German", "id": "Indonesian",
}

# voice-id prefix → espeak language. Derived here so no call site has to pass
# one: synth() never had a language argument and still does not need one.
_LANG_BY_PREFIX = {v[:2]: lang for lang, ids in _ALL_VOICES.items() for v in ids}


def model_voice(voice: str) -> str:
    """The voice id the model actually knows.

    Borrowed-language ids carry their language as an extra prefix
    ("de_am_michael"); the model has no such entry, only "am_michael". Native
    ids pass through untouched.
    """
    parts = voice.split("_")
    return "_".join(parts[1:]) if len(parts) > 2 else voice


def narration_language() -> str:
    """The language the LLM must WRITE the narration in: the voice's.

    A Spanish voice reading an English script is not a Spanish video — espeak
    mangles English words into the target language's phonemes, and the Whisper
    round-trip scores that at 59-86% versus 96% for a script written in the
    voice's own language. Every narration-writing prompt takes this.
    """
    from core.settings import get_setting
    voice = (get_setting("tts.voice")
             or (config.get("tts", default={}) or {}).get("voice", "af_heart"))
    return PROMPT_LANGUAGE.get(language_for_voice(voice), "English")


def voice_options() -> list[dict[str, str]]:
    """Flat {id, label, group} list for the settings API and the wizard.

    `group` is the language heading the UI shows as an <optgroup>, so the 45
    voices read as nine short lists instead of one long one. Name and gender
    come from the trailing segments, so borrowed ids ("de_am_michael") label
    exactly like native ones ("am_michael").
    """
    out = []
    for lang, ids in _ALL_VOICES.items():
        for v in ids:
            parts = v.split("_")
            gender = "female" if parts[-2][1] == "f" else "male"
            out.append({"id": v, "group": LANGUAGE_LABELS[lang],
                        "label": f"{parts[-1].capitalize()} ({gender})"})
    return out


def voice_ids() -> set[str]:
    return {v for ids in _ALL_VOICES.values() for v in ids}


def language_for_voice(voice: str) -> str:
    """espeak language for a voice id; unknown prefixes fall back to English."""
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
    def __init__(self, voice: str | None = None, speed: float | None = None):
        tts_cfg = config.get("tts", default={}) or {}
        from core.settings import get_setting
        # runtime setting (Settings UI) wins over explicit arg over app.yaml
        self.voice = (get_setting("tts.voice") or voice
                      or tts_cfg.get("voice", "af_heart"))
        self.language = language_for_voice(self.voice)
        self.speed = speed if speed is not None else tts_cfg.get("speed", 1.0)
        self._onnx_ok = False
        self._mlx_ok = False

    def available(self) -> bool:
        """Both backends probed; True if at least one works."""
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