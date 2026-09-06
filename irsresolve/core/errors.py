"""Typed errors for IRS Resolve. Every failure names where it happened."""


class IRSResolveError(Exception):
    """Base class."""


class FactsError(IRSResolveError):
    """A fact is invalid or unattested. Carries the JSON path to the offending leaf."""


class ConfigError(IRSResolveError):
    """A config key is missing, or a citation/threshold cannot be resolved."""


class RuleLoadError(IRSResolveError):
    """A rule failed parse-time validation (bad expression, unknown path, missing field)."""


class RuleRuntimeError(IRSResolveError):
    """A rule expression raised at evaluation time."""


class DocumentInferenceError(IRSResolveError):
    """A document could not be safely converted into proposed facts."""


class MissingCredentialsError(DocumentInferenceError):
    """The OpenRouter API key is not configured."""


class UnsupportedDocumentError(DocumentInferenceError):
    """The uploaded document type or size is unsupported."""
