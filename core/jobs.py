"""Explicit video state machine + job orchestration core (spec §6, §26-27)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    IDEA = "IDEA"
    RESEARCHING = "RESEARCHING"
    RESEARCHED = "RESEARCHED"
    SCRIPTING = "SCRIPTING"
    SCRIPTED = "SCRIPTED"
    STORYBOARDING = "STORYBOARDING"
    STORYBOARDED = "STORYBOARDED"
    GENERATING_ASSETS = "GENERATING_ASSETS"
    ASSETS_READY = "ASSETS_READY"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    AUDIO_READY = "AUDIO_READY"
    GENERATING_SUBTITLES = "GENERATING_SUBTITLES"
    SUBTITLES_READY = "SUBTITLES_READY"
    EDITING = "EDITING"
    RENDERED = "RENDERED"
    QUALITY_CHECK = "QUALITY_CHECK"
    APPROVAL = "APPROVAL"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    ANALYTICS = "ANALYTICS"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Linear pipeline order (FAILED is a terminal state that supports retry)
_PIPELINE = [
    State.IDEA, State.RESEARCHING, State.RESEARCHED,
    State.SCRIPTING, State.SCRIPTED, State.STORYBOARDING, State.STORYBOARDED,
    State.GENERATING_ASSETS, State.ASSETS_READY,
    State.GENERATING_AUDIO, State.AUDIO_READY,
    State.GENERATING_SUBTITLES, State.SUBTITLES_READY,
    State.EDITING, State.RENDERED, State.QUALITY_CHECK,
    State.APPROVAL, State.UPLOADING, State.UPLOADED, State.ANALYTICS, State.COMPLETE,
]

_ORDER = {s: i for i, s in enumerate(_PIPELINE)}


def next_state(current: State) -> State | None:
    """Return next pipeline state, or None if current is terminal.

    APPROVAL is a human gate: the pipeline pauses there and only an explicit
    `avf approve` moves it to UPLOADING.
    """
    if current == State.APPROVAL:
        return None
    i = _ORDER.get(current)
    if i is None or i + 1 >= len(_PIPELINE):
        return None
    return _PIPELINE[i + 1]


def can_transition(current: State, target: State) -> bool:
    if target == State.FAILED:
        return True  # any state can fail
    if target == State.CANCELLED:
        # finished work can't be cancelled; failed projects can be laid to rest
        return current not in (State.COMPLETE, State.CANCELLED)
    if current == State.FAILED:
        return _ORDER.get(target, 0) >= _ORDER.get(State.RESEARCHING, 1)  # retry resumes pipeline
    if current == State.CANCELLED:
        return _ORDER.get(target, 0) >= _ORDER.get(State.RESEARCHING, 1)  # re-run after cancel
    return _ORDER.get(target, -1) >= _ORDER.get(current, 0)


# ── cooperative cancellation ────────────────────────────────
_cancel_requested: set[int] = set()


def request_cancel(project_id: int) -> None:
    """Ask a running pipeline to stop at the next stage boundary."""
    _cancel_requested.add(project_id)


def clear_cancel(project_id: int) -> None:
    """Drop a stale cancel request (e.g. retry of a cancelled project —
    without this the next run honors the old flag and instantly re-cancels)."""
    _cancel_requested.discard(project_id)


def is_cancel_requested(project_id: int) -> bool:
    return project_id in _cancel_requested


# ── Job retry policy (spec §27) ─────────────────────────────
@dataclass
class RetryPolicy:
    max_attempts: int = 4
    backoff_seconds: tuple = (0, 5, 30, 120)  # attempt index → wait

    def wait_for(self, attempt: int) -> int:
        """Wait before retrying attempt number (1-based)."""
        idx = min(attempt - 1, len(self.backoff_seconds) - 1)
        return self.backoff_seconds[idx]


RETRY_POLICY = RetryPolicy()


@dataclass
class StageResult:
    """Output of one pipeline stage."""
    stage: State
    success: bool
    data: dict = field(default_factory=dict)
    error: str | None = None
    next: State | None = None


class Orchestrator:
    """Runs project through pipeline stages, persists state, supports resume.

    project_id: DB projects.id. Stage functions are injected to keep this
    decoupled from heavy providers (testable without models).
    """

    def __init__(self, db, stages: dict[State, callable]):
        self.db = db
        self.stages = stages
        from core.logging import get_logger
        self.log = get_logger("orchestrator")

    def _project_state(self, project_id: int) -> State:
        p = self.db.get("projects", project_id)
        return State(p["status"]) if p else State.FAILED

    def run(self, project_id: int, start_at: State | None = None, dry_run: bool = False) -> State:
        """Advance project from its current state to COMPLETE (or FAILED)."""
        current = start_at or self._project_state(project_id)
        if current == State.COMPLETE:
            return current

        while True:
            if is_cancel_requested(project_id):
                _cancel_requested.discard(project_id)
                self._set_state(project_id, State.CANCELLED)
                return State.CANCELLED

            handler = self.stages.get(current)
            if handler is None:
                # No handler for this state → auto-advance past it.
                # Resume-safe: projects stuck at a state without a handler
                # (e.g. future phases not yet wired) move forward when the
                # handler for a later state gets registered.
                # APPROVAL is a human gate: pause there even without handler.
                nxt = next_state(current)
                if nxt is None:
                    # never resurrect a terminal state into COMPLETE
                    if current not in (State.APPROVAL, State.COMPLETE,
                                       State.CANCELLED, State.FAILED):
                        self._set_state(project_id, State.COMPLETE)
                    return current
                self._set_state(project_id, nxt)
                current = nxt
                continue

            if dry_run:
                print(f"[DRY-RUN] would run stage: {current.value}")
                nxt = next_state(current)
                if nxt is None:
                    return current
                current = nxt
                continue

            try:
                result = handler(project_id, current)
            except Exception as e:
                # A retryable provider error (API timeout, quota, 5xx) must NOT
                # mark the project FAILED: FAILED is skipped by `avf produce all`
                # and purged by retention, so the project would be lost. Leave
                # the state as-is and return — the next resume re-runs the stage.
                from core.errors import ProviderError
                if isinstance(e, ProviderError):
                    self.log.warning("stage %s hit retryable %s; leaving state %s "
                                     "for the next resume: %s",
                                     current.value, type(e).__name__, current.value, e)
                    return current
                self._set_state(project_id, State.FAILED)
                raise

            if not result.success:
                # Some failures are transient and request a return to a previous
                # state (e.g. upload 429 → APPROVAL). Honor that instead of
                # marking FAILED so the next cron run can retry them.
                fallback = result.next if result.next and result.next != current else None
                if fallback:
                    self._set_state(project_id, fallback)
                    self.log.warning("stage %s failed (%s); returning to %s for retry",
                                     current.value, result.error, fallback.value)
                    return fallback
                self._set_state(project_id, State.FAILED)
                raise RuntimeError(f"Stage {current.value} failed: {result.error}")

            nxt = result.next or next_state(current)
            if nxt is None:
                # APPROVAL is a pause, not an end: keep its status so
                # `avf approve` can pick it up. Only true terminal → COMPLETE.
                if current != State.APPROVAL:
                    self._set_state(project_id, State.COMPLETE)
                return current
            self._set_state(project_id, nxt)
            current = nxt

    def _set_state(self, project_id: int, state: State) -> None:
        self.db.update("projects", project_id, status=state.value)
        # single choke point for live progress: UI listens via SSE bridge
        from core import events
        events.emit("project_state", project_id=project_id, state=state.value)
