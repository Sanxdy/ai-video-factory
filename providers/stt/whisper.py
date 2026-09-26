"""Whisper STT provider (spec §17) — local subtitles via faster-whisper."""
from __future__ import annotations

from pathlib import Path

from core.config import data_dir
from core.errors import ProviderError
from core.logging import get_logger

log = get_logger("stt")


class WhisperSTT:
    def __init__(self, model: str = "small"):
        self.model = model
        self._model = None

    def available(self) -> bool:
        try:
            import faster_whisper  # noqa
            return True
        except ImportError:
            return False

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            # keep model weights inside AVF's data dir instead of ~/.cache/huggingface
            self._model = WhisperModel(self.model, device="cpu",
                                       compute_type="int8",
                                       download_root=str(data_dir() / "runtime" / "models"))
            log.info("whisper %s loaded (cpu/int8)", self.model)

    def transcribe(self, audio_path: Path) -> list[dict]:
        """Return segments: [{start, end, text}]."""
        if not self.available():
            raise ProviderError("faster-whisper not installed — pip install faster-whisper")
        self._load()
        assert self._model is not None
        segments, _info = self._model.transcribe(str(audio_path),
                                                 vad_filter=True)
        out = [{"start": s.start, "end": s.end, "text": s.text.strip()}
               for s in segments]
        log.info("transcribed %s → %d segments", Path(audio_path).name, len(out))
        return out

    def to_srt(self, audio_path: Path, out_path: Path) -> Path:
        """Transcribe audio → .srt file."""
        segs = self.transcribe(audio_path)
        lines = []
        for i, s in enumerate(segs, 1):
            lines.append(str(i))
            lines.append(f"{_fmt_srt(s['start'])} --> {_fmt_srt(s['end'])}")
            lines.append(s["text"])
            lines.append("")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines), encoding="utf-8")
        return out_path


def _fmt_srt(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"