from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

from ..foundation import CompanionError
from .registry import canonical_json, content_digest


RECEIPT_FORMAT = "investment-companion.compatibility-receipt/v1"


def _write_immutable(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise CompanionError(f"immutable receipt collision: {path}")


def _write_current_pointer(state_dir: Path, pointer: dict[str, Any]) -> None:
    payload = (canonical_json(pointer) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=state_dir, prefix="current-", delete=False) as stream:
        stream.write(payload)
        temporary = Path(stream.name)
    os.replace(temporary, state_dir / "current.json")


def issue_compatibility_receipt(
    *,
    state_dir: str | Path,
    environment: str,
    provider: dict[str, Any],
    requirements: dict[str, Any],
    validation: dict[str, Any],
    conformance: dict[str, Any],
    mcp_profile: str,
    core_identity: str,
    plugin_identity: str,
) -> dict[str, Any]:
    """Persist verified release-pair evidence outside the investment database."""
    if environment not in {"non_production", "production"}:
        raise CompanionError("receipt environment must be non_production or production")
    if not core_identity.strip() or not plugin_identity.strip() or not mcp_profile.strip():
        raise CompanionError("receipt identities and MCP profile must be non-empty")
    provider_digest = content_digest(provider)
    requirements_digest = content_digest(requirements)
    if validation.get("provider_digest") != provider_digest:
        raise CompanionError("validation provider digest does not match current provider")
    if validation.get("requirements_digest") != requirements_digest:
        raise CompanionError("validation requirements digest does not match current requirements")
    if not validation.get("compatible"):
        raise CompanionError("static capability validation did not pass")
    if not conformance.get("passed") or conformance.get("profile") != mcp_profile:
        raise CompanionError("MCP conformance did not pass for the requested profile")
    uncontracted = sorted(
        name for name, capability in provider.get("capabilities", {}).items()
        if capability.get("status") == "uncontracted"
    )
    if environment == "production" and uncontracted:
        raise CompanionError(
            "production receipt cannot include uncontracted capabilities: "
            + ", ".join(uncontracted)
        )
    receipt = {
        "format": RECEIPT_FORMAT,
        "environment": environment,
        "provider_digest": provider_digest,
        "requirements_digest": requirements_digest,
        "release_pair": {"core": core_identity, "plugin": plugin_identity},
        "mcp_profile": mcp_profile,
        "validation": {
            "compatible": validation["compatible"],
            "scopes": validation["scopes"],
            "failures": validation["failures"],
        },
        "conformance": conformance,
    }
    receipt_digest = content_digest(receipt)
    directory = Path(state_dir).expanduser().resolve()
    receipts_dir = directory / "receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    path = receipts_dir / f"{receipt_digest.removeprefix('sha256:')}.json"
    _write_immutable(path, (canonical_json(receipt) + "\n").encode("utf-8"))
    _write_current_pointer(
        directory,
        {
            "digest": receipt_digest,
            "environment": environment,
            "path": str(path.relative_to(directory)),
        },
    )
    return {"digest": receipt_digest, "path": path, "receipt": receipt}


def read_current_receipt(state_dir: str | Path) -> dict[str, Any] | None:
    directory = Path(state_dir).expanduser().resolve()
    pointer_path = directory / "current.json"
    if not pointer_path.is_file():
        return None
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        path = (directory / pointer["path"]).resolve()
        if not path.is_relative_to(directory / "receipts") or not path.is_file():
            raise CompanionError("current receipt pointer leaves the receipt directory")
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (KeyError, json.JSONDecodeError, OSError) as exc:
        raise CompanionError(f"invalid current receipt pointer: {exc}") from exc
    digest = content_digest(receipt)
    if digest != pointer.get("digest") or path.name != digest.removeprefix("sha256:") + ".json":
        raise CompanionError("current receipt content digest mismatch")
    if pointer.get("environment") != receipt.get("environment"):
        raise CompanionError("current receipt pointer environment mismatch")
    if receipt.get("format") != RECEIPT_FORMAT:
        raise CompanionError("unsupported current receipt format")
    if not receipt.get("validation", {}).get("compatible") or not receipt.get("conformance", {}).get("passed"):
        raise CompanionError("current receipt does not contain successful verification")
    return {"digest": digest, "path": path, "receipt": receipt}
