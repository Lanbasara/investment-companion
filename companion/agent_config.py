from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib


REQUIRED_AGENT_FIELDS = {"name", "description", "developer_instructions"}


def validate_agent_config(root: Path) -> dict[str, Any]:
    """Validate the project-scoped Codex agent configuration without starting Codex."""
    root = root.resolve()
    project_config = root / ".codex" / "config.toml"
    agents_dir = root / ".codex" / "agents"
    errors: list[str] = []
    warnings: list[str] = []
    agents: list[dict[str, Any]] = []

    if not project_config.is_file():
        return {"ok": False, "errors": [f"missing project config: {project_config}"], "warnings": [], "agents": []}
    try:
        project = tomllib.loads(project_config.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return {"ok": False, "errors": [f"invalid project config: {exc}"], "warnings": [], "agents": []}

    project_mcp = set(project.get("mcp_servers", {}))
    if "token=" in project_config.read_text(encoding="utf-8").lower():
        errors.append("project config contains an inline token; use a credential launcher")
    if not agents_dir.is_dir():
        errors.append(f"missing agents directory: {agents_dir}")
        return {"ok": False, "errors": errors, "warnings": warnings, "agents": agents}

    seen_names: set[str] = set()
    for path in sorted(agents_dir.glob("*.toml")):
        try:
            config = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            errors.append(f"{path.name}: invalid TOML: {exc}")
            continue
        missing = sorted(REQUIRED_AGENT_FIELDS - config.keys())
        if missing:
            errors.append(f"{path.name}: missing required fields: {', '.join(missing)}")
        name = config.get("name")
        if isinstance(name, str):
            if name in seen_names:
                errors.append(f"{path.name}: duplicate agent name: {name}")
            seen_names.add(name)
            if path.stem != name:
                warnings.append(f"{path.name}: filename differs from agent name {name}")
        local_mcp = set(config.get("mcp_servers", {}))
        overlap = sorted(project_mcp & local_mcp)
        if overlap:
            errors.append(
                f"{path.name}: redefines inherited MCP server(s): {', '.join(overlap)}; "
                "Codex may reject the agent type"
            )
        agents.append({
            "name": name or path.stem,
            "path": str(path.relative_to(root)),
            "inherited_mcp_servers": sorted(project_mcp),
            "dedicated_mcp_servers": sorted(local_mcp - project_mcp),
        })

    if not agents:
        errors.append("no custom agent files found")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "agents": agents}
