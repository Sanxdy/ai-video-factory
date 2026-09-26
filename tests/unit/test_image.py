"""Image provider chain: settings-driven order + fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from providers.image import PROVIDERS, ImageProvider, image_provider_setting


def test_no_local_ai_tier():
    """The local SD tier is gone — its weights were deleted, so offering it
    would only make `auto` attempt a multi-GB download."""
    assert "sd" not in PROVIDERS


def test_no_web_ai_tier():
    """Pollinations is gone: unverifiable, watermarked, and it sent the scene
    prompt off the machine."""
    assert "pollinations" not in PROVIDERS


def test_removed_provider_setting_degrades_to_auto(monkeypatch):
    """A DB row written before the removal must not reach `_chain`, where it
    would match no tier and fail every scene."""
    monkeypatch.setattr("core.settings.get_setting", lambda k: "pollinations")
    assert image_provider_setting() == "auto"


def test_chain_follows_setting(monkeypatch):
    monkeypatch.setattr("providers.image.image_provider_setting", lambda: "pexels")
    chain = ImageProvider()._chain(None)
    assert chain[0] == "pexels"
    assert chain[-1] == "gradient"  # always the final fallback


def test_chain_auto_skips_unavailable(monkeypatch):
    monkeypatch.setattr("providers.image.image_provider_setting", lambda: "auto")
    p = ImageProvider()
    monkeypatch.setattr(p, "_pexels_key", lambda: "")
    assert p._chain(None) == ["gradient"]


def test_chain_auto_prefers_configured_source(monkeypatch):
    monkeypatch.setattr("providers.image.image_provider_setting", lambda: "auto")
    p = ImageProvider()
    monkeypatch.setattr(p, "_pexels_key", lambda: "k")
    assert p._chain(None) == ["pexels", "gradient"]


def test_chain_explicit_override(monkeypatch):
    monkeypatch.setattr("providers.image.image_provider_setting", lambda: "auto")
    assert ImageProvider()._chain("pexels")[0] == "pexels"


def test_generate_falls_through_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("providers.image.image_provider_setting", lambda: "pexels")
    p = ImageProvider()
    monkeypatch.setattr(p, "_gen_pexels",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(p, "_gen_gradient", lambda prompt, out: out)
    out = p.generate("a mysterious forest", tmp_path / "x.png")
    assert out == tmp_path / "x.png"
