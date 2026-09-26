"""Pydantic models (spec §2) — strict JSON validation for LLM outputs."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Idea(BaseModel):
    topic: str
    hook: str
    reason: str = ""
    estimated_interest: int = Field(ge=0, le=100)
    difficulty: int = Field(ge=0, le=100)


class IdeasOutput(BaseModel):
    ideas: list[Idea]


class ResearchFact(BaseModel):
    claim: str
    status: Literal["VERIFIED", "UNVERIFIED"]
    source: str = ""


class ResearchOutput(BaseModel):
    topic: str
    summary: str
    facts: list[ResearchFact]
    sources: list[str]
    confidence: float = Field(ge=0, le=1)


class Hook(BaseModel):
    text: str
    type: Literal["curiosity", "shock", "question", "contradiction", "story", "unexpected_fact"]
    score: int = Field(ge=0, le=100)


class HooksOutput(BaseModel):
    hooks: list[Hook]


class ScriptOutput(BaseModel):
    hook: str
    body: str
    cta: str
    # small local LLMs omit this field — default beats failing the whole run;
    # script_gen recomputes duration from word count right after parsing anyway
    duration_seconds: int = Field(default=45, ge=10, le=90)


class Scene(BaseModel):
    scene_number: int
    duration: float = Field(gt=0)
    narration: str
    visual_prompt: str
    # short 2-4 word searchable query for real-footage providers (Pexels);
    # must name the concrete subject shown in the scene, e.g. "camel desert"
    stock_query: str = ""
    motion_prompt: str = ""
    text_overlay: str = ""
    asset_type: Literal["image", "video", "text", "stock", "generated"] = "image"


class StoryboardOutput(BaseModel):
    scenes: list[Scene]


class LongformSection(BaseModel):
    """One section of a countdown script (see prompts/longform).

    The long-form script is generated one section per LLM call, so the model
    returns a chapter label plus that section's narration only.
    """
    heading: str = ""
    narration: str


class VisualShot(BaseModel):
    """Camera description for one already-written scene (prompts/visuals)."""
    visual_prompt: str
    stock_query: str = ""
    motion_prompt: str = "zoom-in"


class VisualBatch(BaseModel):
    shots: list[VisualShot]


class TitleSet(BaseModel):
    youtube_titles: list[str] = Field(min_length=3, max_length=8)
    short_titles: list[str] = Field(min_length=3, max_length=8)
    description: str
    hashtags: list[str]
    tags: list[str] = []  # SEO keywords for YouTube search


class QualityReview(BaseModel):
    score: int = Field(ge=0, le=100)
    pass_: bool = Field(default=True, alias="pass")
    issues: list[str] = []
    recommendations: list[str] = []


_model_registry = {
    "idea": IdeasOutput, "research": ResearchOutput, "hooks": HooksOutput,
    "script": ScriptOutput, "storyboard": StoryboardOutput,
    "titles": TitleSet, "quality": QualityReview,
    "longform": LongformSection, "visuals": VisualBatch,
}


def parse_model(kind: str, raw: str):
    """Parse LLM JSON into the matching Pydantic model (strict)."""
    return model_class(kind).model_validate_json(raw)


def model_class(kind: str) -> type[BaseModel]:
    return _model_registry[kind]