# Model & Provider Licenses

Recorded per spec §45. The system must warn before using a model whose
license does not clearly permit commercial use. The LLM itself is a
third-party API (BYOK) — its terms are the operator's to accept.

| Model / Provider | Source | License | Commercial use | Attribution | Restrictions |
|---|---|---|---|---|---|
| Kokoro v1.0 ONNX (TTS) | HuggingFace kokoro-onnx | Apache 2.0 | ✅ Yes | Not required | Voice packs: check per-voice (af_heart = Apache 2.0 per Kokoro repo) |
| faster-whisper tiny (STT) | HuggingFace SYSTRAN | MIT | ✅ Yes | Not required | None |
| FFmpeg | ffmpeg.org system pkg | LGPL 2.1+ / GPL (build-dep) | ✅ Yes (LGPL build) | Not required | GPL components if re-distributed — we only invoke the binary |
| DejaVu Sans (subtitle font) | system fonts | Bitstream Vera / DejaVu license | ✅ Yes | Not required | None material |
| YouTube Data API | Google | Google ToS | ✅ (API free tier) | — | Quota 10k units/day; content must comply with YouTube policies |

## Warnings

- None of the models above prohibit commercial use as of 2026-08.
- If a provider is added with a non-commercial license (e.g. some
  research-only checkpoints), add a warning here AND in the loader code.
