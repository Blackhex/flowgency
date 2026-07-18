"""Strict canonical configuration aliases retained for import-path stability."""

from .issues import ValidationFailed, ValidationIssue
from .models import AgencyConfig, parse_config, validate_config
from .store import ConfigConflictError, ConfigSnapshot, ConfigStore

__all__ = [
    "AgencyConfig",
    "ConfigConflictError",
    "ConfigSnapshot",
    "ConfigStore",
    "ValidationFailed",
    "ValidationIssue",
    "parse_config",
    "validate_config",
]