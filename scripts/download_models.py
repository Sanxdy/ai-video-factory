"""Download all local models (spec §3): Kokoro + Whisper.

The LLM is BYOK API only (see providers/llm) — nothing to pull for it.
Kokoro/Whisper auto-download on first use; this pre-fetches everything.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.model_manager import discover_models, download_model, verify_model  # noqa: E402


def main() -> int:
    ok = True
    for m in discover_models():
        if m.get("status") == "disabled":
            continue
        if verify_model(m["name"]):
            print(f"[have] {m['name']}")
            continue
        if m.get("required") is False:
            print(f"[skip] {m['name']} (optional — {m.get('description', '').split('.')[0].lower()})")
            continue
        if m.get("status") == "fallback":
            print(f"[skip] {m['name']} (fallback — install only if needed)")
            continue
        print(f"[pull] {m['name']} via {m['install_method']}")
        try:
            ok &= download_model(m["name"])
        except Exception as e:
            print(f"[fail] {m['name']}: {e}")
            ok = False

    # TTS + STT models auto-download on first use; trigger now
    try:
        from providers.tts.kokoro import KokoroTTS
        t = KokoroTTS()
        t.available()
        print("[have] kokoro models")
    except Exception as e:
        print(f"[warn] kokoro: {e}")
    try:
        from faster_whisper import WhisperModel
        WhisperModel("tiny")
        print("[have] whisper tiny")
    except Exception as e:
        print(f"[warn] whisper: {e}")

    print("DONE" if ok else "SOME DOWNLOADS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
