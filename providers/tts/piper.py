"""Piper TTS provider — native-accent voices for languages Kokoro lacks.

Kokoro v1.0 was never trained on German or Indonesian: the "borrowed" English
voices passed a Whisper round-trip but sound English reading German or
Indonesian text, which is exactly what a viewer hears. Piper's voices are
trained per language — id_ID news_tts is an Indonesian newsreader — so the
accent is native. Trade-off: Piper's catalog is small (Indonesia has a single
voice, no gender choice yet) and the quality is a notch below Kokoro's, which
is why Kokoro keeps every language it can actually do.

Voice models download on first use (10-60 MB each) into the same runtime/models
dir as the Kokoro weights, so a project renders offline after the first run.
piper-tts ships its own espeak-ng-data inside the wheel — short enough for the
bundle's path-length limit.

Downloads pull from rhasspy/piper-voices first and fall back to
csukuangfj's per-voice mirrors, which carry the whole catalog. HF_HUB_DISABLE_XET
is forced: the xet metadata service intermittently answers 404 for anonymous
users while the classic CDN path keeps working.
"""
from __future__ import annotations

import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # before huggingface_hub import

import shutil
import threading
import time
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
        "lang_label": "Indonesian",
    },
    "de_DE-thorsten-medium": {
        "name": "Thorsten", "gender": "male", "lang": "de",
        "lang_label": "German",
    },
    "de_DE-ramona-low": {
        "name": "Ramona", "gender": "female", "lang": "de",
        "lang_label": "German",
    },
    "fr_FR-gilles-low": {
        "name": "Gilles", "gender": "male", "lang": "fr",
        "lang_label": "French",
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


# Download sources per voice, tried in order: (repo_id, onnx path, json path).
# rhasspy/piper-voices is the canonical home; csukuangfj's per-voice repos
# mirror the whole catalog and have rescued voices rhasspy quietly removed
# (the entire id/ tree vanished from it in 2026-09).
def _sources(key: str) -> list[tuple[str, str, str]]:
    lang, rest = key.split("_", 1)
    region, rest = rest.split("-", 1)
    name, quality = rest.rsplit("-", 1)
    canonical = (f"rhasspy/piper-voices",
                 f"{lang}/{region}/{name}/{quality}/{key}.onnx",
                 f"{lang}/{region}/{name}/{quality}/{key}.onnx.json")
    mirror = (f"csukuangfj/vits-piper-{key}", f"{key}.onnx", f"{key}.onnx.json")
    return [canonical, mirror]


def _hf_fetch(key: str, model: Path, config: Path) -> None:
    """Fetch onnx+json from the first source that has them."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError

    last_error: Exception | None = None
    for repo, onnx_name, json_name in _sources(key):
        try:
            for dest, fname in ((model, onnx_name), (config, json_name)):
                if dest.exists():
                    continue
                log.info("fetching piper voice file %s from %s", fname, repo)
                got = hf_hub_download(repo_id=repo, filename=fname,
                                      local_dir=str(dest.parent))
                if Path(got) != dest:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(got, dest)
            return
        except EntryNotFoundError as e:
            # a source that simply does not have the voice: move on at once
            last_error = e
            log.info("voice %s not on %s — trying the next mirror", key, repo)
        except Exception as e:
            # rate limits and network trouble: recorded, retried by the caller
            last_error = e
            log.warning("piper fetch from %s failed (%s)", repo, str(e)[:80])
    if last_error:
        raise last_error


def ensure_model(key: str) -> Path:
    """The voice's .onnx path, fetching it (plus its config) once.

    Downloads go through huggingface_hub — the official client handles the
    Hub's signed-redirect CDN. Sources are tried in order; transient failures
    (the CDN's anonymous-download cooldown) are retried on a minutes-long
    schedule. Callers that cannot block that long should check is_cached()
    first and surface "downloading, try again" — this function is the patient
    background fetch.

    Raises instead of guessing: a missing voice key is a registry bug, a failed
    download is a network problem the user should see, not silently skip.
    """
    if key not in VOICES:
        raise ProviderError(f"unknown piper voice '{key}'")
    model, config = _model_paths(key)
    with _DOWNLOAD_LOCK:
        if not model.exists():
            last_error: Exception | None = None
            for wait in (0, 30, 90, 180, 300):
                if wait:
                    log.info("piper download rate-limited — waiting %ds", wait)
                    time.sleep(wait)
                try:
                    _hf_fetch(key, model, config)
                    last_error = None
                    break
                except Exception as e:
                    last_error = e
            if last_error or not model.exists():
                raise ProviderError(
                    f"downloading piper voice '{key}' failed after 5 attempts "
                    f"over ~10 minutes: {str(last_error)[:120]}")
    return model


def is_cached(key: str) -> bool:
    """True when the voice's onnx is already on disk — no network needed."""
    return _model_paths(key)[0].exists()


# one download at a time: parallel first-time fetches trip the CDN's
# unauthenticated rate limit, and every one of them then fails
_DOWNLOAD_LOCK = threading.Lock()


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
