"""Piper TTS provider — native-accent voices for languages Kokoro lacks.

Kokoro v1.0 was never trained on German or Indonesian: the "borrowed" English
voices passed a Whisper round-trip but sound English reading German or
Indonesian text, which is exactly what a viewer hears. Piper's voices are
trained per language — id_ID news_tts is an Indonesian newsreader — so the
accent is native. Trade-off: Piper's catalog is small (Indonesia has a single
voice, no gender choice yet) and the quality is a notch below Kokoro's, which
is why Kokoro keeps every language it can actually do.

Voice models download from rhasspy/piper-voices on first use (10-60 MB each)
into the same runtime/models dir as the Kokoro weights, so a project renders
offline after the first run. piper-tts ships its own espeak-ng-data inside the
wheel — short enough for the bundle's path-length limit.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from core.config import data_dir
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("tts")

HF = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# voice key (after the "piper:" prefix) → catalog entry. The key IS the
# piper-voices path stem: {lang}/{region}/{name}/{quality} → id filename
# {lang}_{region}-{name}-{quality}.onnx. Keep names stable — they end up in
# user settings and on disk.
VOICES: dict[str, dict[str, str]] = {
    "id_ID-news_tts-medium": {
        "name": "News reader", "gender": "female", "lang": "id",
    },
    "de_DE-thorsten-medium": {
        "name": "Thorsten", "gender": "male", "lang": "de",
    },
    "de_DE-ramona-low": {
        "name": "Ramona", "gender": "female", "lang": "de",
    },
    "fr_FR-gilles-low": {
        "name": "Gilles", "gender": "male", "lang": "fr",
    },
}


def voice_id(key: str) -> str:
    """The registry id for a piper voice key ("id_ID-news_tts-medium")."""
    return f"piper:{key}"


def language_for_voice(voice: str) -> str | None:
    """Two-letter language of a "piper:…" id; None when unknown."""
    key = voice.removeprefix("piper:")
    entry = VOICES.get(key)
    return entry["lang"] if entry else None


def _model_paths(key: str) -> tuple[Path, Path]:
    models = data_dir() / "runtime" / "models" / "piper"
    return models / f"{key}.onnx", models / f"{key}.onnx.json"


def voice_urls(key: str) -> tuple[str, str]:
    """The (onnx, onnx.json) urls for a piper voice key.

    The key is {lang}_{region}-{name}-{quality} (the name itself may contain
    underscores, e.g. news_tts); the url path inserts slashes:
    {lang}/{region}/{name}/{quality}/{key}
    """
    lang, rest = key.split("_", 1)
    region, rest = rest.split("-", 1)
    name, quality = rest.rsplit("-", 1)
    base = f"{HF}/{lang}/{region}/{name}/{quality}/{key}"
    return f"{base}.onnx", f"{base}.onnx.json"


def ensure_model(key: str) -> Path:
    """The voice's .onnx path, fetching it (plus its config) once.

    Downloads go through huggingface_hub — the official client handles the
    Hub's signed-redirect CDN that plain urllib trips over. Files land in the
    same runtime/models tree as the Kokoro weights, so the project stays
    offline-capable after the first run.

    Raises instead of guessing: a missing voice key is a registry bug, a failed
    download is a network problem the user should see, not silently skip.
    """
    if key not in VOICES:
        raise ProviderError(f"unknown piper voice '{key}'")
    model, config = _model_paths(key)
    if not model.exists():
        from huggingface_hub import hf_hub_download

        lang, rest = key.split("_", 1)
        region, rest = rest.split("-", 1)
        name, quality = rest.rsplit("-", 1)
        for dest, suffix in ((model, ".onnx"), (config, ".onnx.json")):
            if dest.exists():
                continue
            path_in_repo = f"{lang}/{region}/{name}/{quality}/{key}{suffix}"
            log.info("fetching piper voice file %s", path_in_repo)
            got = hf_hub_download(repo_id="rhasspy/piper-voices",
                                  filename=path_in_repo,
                                  local_dir=str(model.parent))
            if Path(got) != dest:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(got, dest)
    return model


class PiperTTS:
    """One voice; mirrors the KokoroTTS.synth(text, out_path) contract."""

    def __init__(self, voice: str, speed: float = 1.0):
        if not voice.startswith("piper:"):
            raise ProviderError(f"not a piper voice id: '{voice}'")
        self.key = voice.removeprefix("piper:")
        self.speed = speed if speed > 0 else 1.0

    def synth(self, text: str, out_path: Path) -> Path:
        import wave

        from piper import PiperVoice, SynthesisConfig

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        model = ensure_model(self.key)
        voice = PiperVoice.load(model, config_path=f"{model}.json")
        # piper's length_scale: >1 is slower, so it is the reciprocal of the
        # speed factor every other part of AVF uses
        cfg = SynthesisConfig(length_scale=1.0 / self.speed)
        with wave.open(str(out_path), "wb") as wf:
            voice.synthesize_wav(text, wf, syn_config=cfg)
        if not out_path.exists() or out_path.stat().st_size < 1000:
            raise ProviderError(f"piper produced no audio for '{self.key}'")
        return out_path
