"""Strict canonical configuration aliases retained for import-path stability."""

from .issues import ValidationFailed, ValidationIssue
from .models import AgencyConfigcanonical, parse_config_canonical, validate_config_canonical
from .store import ConfigConflictError, ConfigSnapshot, ConfigStore

__all__ = [
    "AgencyConfigcanonical",
    "ConfigConflictError",
    "ConfigSnapshot",
    "ConfigStore",
    "ValidationFailed",
    "ValidationIssue",
    "parse_config_canonical",
    "validate_config_canonical",
]