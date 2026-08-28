"""Executable Core–Plugin capability contracts."""

from .registry import (
    CapabilityRegistry,
    HOME_DESCRIPTION,
    HOME_INPUT_SCHEMA,
    ProviderManifest,
    investment_capability_registry,
)
from .validator import validate_compatibility

__all__ = [
    "CapabilityRegistry",
    "HOME_DESCRIPTION",
    "HOME_INPUT_SCHEMA",
    "ProviderManifest",
    "investment_capability_registry",
    "validate_compatibility",
]
