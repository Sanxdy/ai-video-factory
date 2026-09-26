"""Prompt loader (spec §32) — versioned prompt files, never prompts in code."""
from __future__ import annotations

from pathlib import Path

from core.config import ROOT

_PROMPT_DIR = ROOT / "prompts"


def load_prompt(kind: str, name: str = "v001") -> str:
    """Load a prompt template by kind (ideas/hooks/scripts/...) and version.

    Latest version is auto-selected when name resolves to a version prefix.
    """
    d = _PROMPT_DIR / kind
    if not d.exists():
        raise FileNotFoundError(f"no prompt dir for kind '{kind}'")

    if name.startswith("v"):
        path = d / f"{name}.txt"
    else:
        # resolve latest vXXX for the named prompt
        matches = sorted(d.glob(f"{name}_v*.txt"))
        path = matches[-1] if matches else d / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text()


def render_prompt(kind: str, **kwargs) -> str:
    """Load template (latest version) and substitute {placeholders}.

    Uses simple replace (not str.format) so literal JSON braces in templates
    like {"ideas": [...]} are preserved untouched.
    """
    tpl = load_prompt(kind, "v001")
    for key, val in kwargs.items():
        tpl = tpl.replace("{" + key + "}", str(val))
    return tpl