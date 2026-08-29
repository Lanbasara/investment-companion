from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
from typing import Any

from ..foundation import CompanionError
from ..interfaces.mcp_profiles import INVESTMENT_CAPABILITY_REGISTRY
from .conformance import evaluate_investment_conformance, probe_investment_mcp
from .depth import load_interface_depth_policy, validate_interface_depth
from .receipts import issue_compatibility_receipt
from .validator import validate_compatibility


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _load(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompanionError(f"invalid contract document: {exc}") from exc
    if not isinstance(value, dict):
        raise CompanionError("contract document must be a JSON object")
    return value


def _verified_git_identity(
    checkout: str | Path,
    supplied: str,
    role: str,
) -> str:
    """Resolve a clean checkout identity and reject a caller-supplied mismatch."""
    path = Path(checkout).expanduser().resolve()
    cwd = path.parent if path.is_file() else path
    identity = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    head = identity.stdout.strip()
    if identity.returncode != 0 or len(head) != 40:
        raise CompanionError(f"{role} checkout identity is unavailable: {cwd}")
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    if dirty.returncode != 0:
        raise CompanionError(f"{role} checkout status is unavailable: {cwd}")
    if dirty.stdout.strip():
        raise CompanionError(f"{role} checkout has uncommitted tracked changes: {cwd}")
    if supplied != head:
        raise CompanionError(
            f"{role} identity mismatch: supplied {supplied}, checkout {head}"
        )
    return head


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m companion.capabilities")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("provider")
    sub.add_parser("depth")
    validate = sub.add_parser("validate")
    validate.add_argument("--requirements", required=True)
    receipt = sub.add_parser("receipt")
    receipt.add_argument("--requirements", required=True)
    receipt.add_argument("--state-dir", required=True)
    receipt.add_argument(
        "--environment",
        choices=["non_production", "production"],
        default="non_production",
    )
    receipt.add_argument("--core-identity", required=True)
    receipt.add_argument("--plugin-identity", required=True)
    for command in (validate, receipt):
        command.add_argument(
            "--usage-source",
            action="append",
            default=[],
            help="Plugin prose file to audit for contracted capability usage",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        provider = INVESTMENT_CAPABILITY_REGISTRY.provider_manifest()
        if args.command == "provider":
            result = {"digest": provider.digest, "manifest": provider.document}
        elif args.command == "depth":
            result = validate_interface_depth(
                INVESTMENT_CAPABILITY_REGISTRY,
                load_interface_depth_policy(),
            )
        else:
            requirements = _load(args.requirements)
            validation = validate_compatibility(
                provider.document,
                requirements,
                usage_sources=args.usage_source or None,
            )
            interface_depth = validate_interface_depth(
                INVESTMENT_CAPABILITY_REGISTRY,
                load_interface_depth_policy(),
            )
            validation["interface_depth"] = interface_depth
            if not interface_depth["passed"]:
                failures = [
                    {**failure, "scope": "baseline"}
                    for failure in interface_depth["failures"]
                ]
                validation["failures"].extend(failures)
                validation["scopes"]["baseline"]["failures"].extend(failures)
                validation["scopes"]["baseline"]["status"] = "degraded"
                validation["compatible"] = False
            if args.command == "validate":
                result = validation
            else:
                core_identity = _verified_git_identity(
                    Path(__file__).parents[2], args.core_identity, "core"
                )
                plugin_identity = _verified_git_identity(
                    args.requirements, args.plugin_identity, "plugin"
                )
                with tempfile.TemporaryDirectory(prefix="companion-conformance-") as root:
                    conformance = evaluate_investment_conformance(
                        probe_investment_mcp(root)
                    )
                result = issue_compatibility_receipt(
                    state_dir=args.state_dir,
                    environment=args.environment,
                    provider=provider.document,
                    requirements=requirements,
                    validation=validation,
                    conformance=conformance,
                    interface_depth=interface_depth,
                    mcp_profile="investment",
                    core_identity=core_identity,
                    plugin_identity=plugin_identity,
                )
        _emit(result)
        if args.command == "validate" and not result["compatible"]:
            return 2
        return 0
    except (CompanionError, RuntimeError, ValueError, KeyError) as exc:
        _emit({"ok": False, "error": str(exc)})
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
