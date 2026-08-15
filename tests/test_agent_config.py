from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion.agent_config import validate_agent_config


class AgentConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / ".codex" / "agents").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, relative: str, content: str) -> None:
        (self.root / relative).write_text(content, encoding="utf-8")

    def test_agents_inherit_project_mcp_without_redefinition(self):
        self.write(".codex/config.toml", '[mcp_servers.market]\ncommand = "launcher"\n')
        self.write(
            ".codex/agents/scout.toml",
            'name = "scout"\ndescription = "Scout"\ndeveloper_instructions = "Investigate"\n',
        )
        result = validate_agent_config(self.root)
        self.assertTrue(result["ok"])
        self.assertEqual(result["agents"][0]["inherited_mcp_servers"], ["market"])

    def test_redefining_inherited_mcp_is_rejected(self):
        self.write(".codex/config.toml", '[mcp_servers.market]\ncommand = "launcher"\n')
        self.write(
            ".codex/agents/scout.toml",
            'name = "scout"\ndescription = "Scout"\ndeveloper_instructions = "Investigate"\n'
            '[mcp_servers.market]\ncommand = "other-launcher"\n',
        )
        result = validate_agent_config(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("redefines inherited MCP", result["errors"][0])

    def test_inline_token_is_rejected(self):
        self.write(".codex/config.toml", '[mcp_servers.market]\nurl = "https://example.test/mcp?token=secret"\n')
        self.write(
            ".codex/agents/scout.toml",
            'name = "scout"\ndescription = "Scout"\ndeveloper_instructions = "Investigate"\n',
        )
        result = validate_agent_config(self.root)
        self.assertFalse(result["ok"])
        self.assertIn("inline token", result["errors"][0])


if __name__ == "__main__":
    unittest.main()
