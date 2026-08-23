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
用户 ↔ 飞书 / cc-connect ↔ Primary Investment Codex
                         → Investment Home
                         → 研究与验证 → 组合决策 → 风险闸门
                         → 人工行动 → 确认账本 → 绩效与复盘

横向平台：PIT 数据与证据、市场日历、Schedule/Run/Delivery、审计与版本
```

从 [项目状态与交棒入口](docs/PROJECT-STATUS.md) 开始阅读。新的长期架构以[版本无关目标架构](docs/ARCHITECTURE.md)、[领域词典](docs/DOMAIN-GLOSSARY.md)、[架构升级实施计划](docs/REFACTOR-PLAN.md)和[生产灰度验收清单](docs/PRODUCTION-ROLLOUT-CHECKLIST.md)为准；V2–V7 文档继续保存历史决策、兼容契约与生产运行事实。系统不自动交易或承诺盈利。
浏览器阅读入口为 [docs/index.html](docs/index.html)；它直接渲染上述权威 Markdown，不维护第二份易过期的文档副本。

开发验收可用 `COMPANION_MCP_PROFILE=investment ./bin/companion-mcp` 启动 22 个版本无关语义接口；`admin` 只提供历史管理工具，`all` 保持迁移期兼容。固定生产 Runtime 当前仍使用 `all`，切换按[生产灰度验收清单](docs/PRODUCTION-ROLLOUT-CHECKLIST.md)执行。

行动型 Decision 有两道独立硬门槛：研究必须先形成 `eligible_for_decision` 的不可变验证 Calculation，交易方案还必须通过当前 Mandate、确认账本和市场现实驱动的 Risk Gate。扫描榜单、`unvalidated` 预测和研究文字都不能直接授权行动。

人工执行明确分为：接受 Action Card → 准备 Execution → 用户报告已下单 → 登记待确认成交 → 用户确认 Ledger → Execution 对账完成。前四步都不能改变真实持仓。飞书正式交互使用自然语言，Primary Codex 将用户意图映射到这些版本无关动作；不额外开发业务交互卡片。

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
