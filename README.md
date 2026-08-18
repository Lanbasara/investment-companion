# Investment Companion

一个生长在 Codex 内的个人全生命周期投资伴侣。Primary Codex 负责理解、调查组织、投资判断和用户沟通；Companion Core 只保存不可模糊的运行事实、调度状态和 Markdown 工作材料句柄。

## 初始化

```bash
git clone https://github.com/Lanbasara/investment-companion.git
cd investment-companion
./bin/companion workspace-init --finance-source /absolute/path/to/finance-feed
./bin/companion status
```

初始化是幂等的，不会清空已有数据。省略 `--finance-source` 可以稍后通过 Companion MCP 或自然语言接入信息源。

Tushare 凭据放在本机的 `~/.config/tushare/token`（权限必须屏蔽 group/other，推荐 `0600`），不得提交到仓库。Custom Agents 通过 `bin/launch-tushare-mcp` 在运行时只读查询；它们使用只读沙箱，并在各自配置中显式禁用可写的 Investment Companion MCP，由 Primary Codex 审阅并落库。

## 架构

```text
systemd timer → Companion Schedule/Run/Outbox → cc-connect
              → Primary Codex → 短命 Scout/Gardener
              → Markdown 文件句柄与 SQLite 精确状态
```

从 [项目状态与交棒入口](docs/PROJECT-STATUS.md) 开始阅读。当前生产架构见 [V3 设计](docs/V3-DESIGN.md)；V4 工程见 [总体设计](docs/V4-DESIGN.md)、[架构决策](docs/V4-ARCHITECTURE-DECISIONS.md)、[实施计划](docs/V4-IMPLEMENTATION-PLAN.md)、[验收契约](docs/V4-ACCEPTANCE.md)、[数据资格](docs/DATA-QUALIFICATION-v1.md)与[V4 运维](docs/V4-OPERATIONS.md)。V4 代码已实现，但数据与策略 Gate 尚未通过，生产仍运行 V3，不能把工程能力当成有效策略。历史决策见 [V2 设计](docs/V2-DESIGN.md)。
浏览器阅读入口为 [docs/index.html](docs/index.html)；它直接渲染上述权威 Markdown，不维护第二份易过期的文档副本。

## 验证

```bash
python3 -m pytest -q
./bin/companion agent-check
./bin/companion recover
./bin/companion doctor
```

修改 Custom Agent 或 MCP 配置后，运行 `./bin/companion-agent-smoke` 完成一次真实 Codex 派遣验收；该检查会产生模型调用，不放入普通单元测试。

新 Codex 会话由项目级 `SessionStart` Hook 注入一个有界 `session-brief`。它只包含数据库健康、Context 初始化状态和活跃对象计数；具体投资材料仍由 Lifecycle Skill 按问题创建 Recovery Package 后加载。

项目不连接券商、不自动交易、不维持常驻 Subagent。
