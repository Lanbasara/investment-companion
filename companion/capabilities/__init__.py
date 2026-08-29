"""Executable Core–Plugin capability contracts."""

from .registry import (
    CONTRACTED_CAPABILITY_NAMES,
    CapabilityRegistry,
    HOME_DESCRIPTION,
    HOME_INPUT_SCHEMA,
    ProviderManifest,
    RECONCILIATION_STATEMENT_SCHEMA,
    investment_capability_registry,
)
from .depth import (
    interface_surface,
    load_interface_depth_policy,
    validate_interface_depth,
)
from .validator import validate_compatibility

__all__ = [
    "CONTRACTED_CAPABILITY_NAMES",
    "CapabilityRegistry",
    "HOME_DESCRIPTION",
    "HOME_INPUT_SCHEMA",
    "ProviderManifest",
    "RECONCILIATION_STATEMENT_SCHEMA",
    "investment_capability_registry",
    "validate_compatibility",
    "interface_surface",
    "load_interface_depth_policy",
    "validate_interface_depth",
]
