"""Error hierarchy (spec §2, §27): permanent vs retryable."""
from __future__ import annotations


class AVFError(Exception):
    """Base error."""


class ConfigError(AVFError):
    pass


class ProviderError(AVFError):
    """Provider failed — retryable (network, timeout, OOM)."""
    retryable = True


class PermanentError(AVFError):
    """Permanent failure — do NOT retry (spec §27)."""
    retryable = False


class ModelNotAvailableError(PermanentError):
    pass


class ValidationError(AVFError):
    pass


class QuotaError(ProviderError):
    """Third-party quota exhausted (e.g. YouTube API). Retry after reset."""
    retryable = True


class ResourceError(ProviderError):
    """Out of memory / disk — retryable after waiting."""
    retryable = True
