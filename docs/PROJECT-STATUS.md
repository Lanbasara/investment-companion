# Investment Companion：项目状态与会话交棒

更新时间：2026-08-18
当前发布：主项目为 `v3.0.4`，Plugin 为 `v3.0.1`
当前定位：V3 Core 已发布并完成首批真实个人事实初始化，正在真实使用验证；V4 已完成架构定义，尚未开始代码实现。

用户已确认 V4 定义：**可审计、Codex 驱动、确定性量化内核支撑、飞书协作、人工执行的专业个人投资研究与决策系统**。代码起始点已用本地注释标签 `pre-v4.0.0` 固定在 `b6ec6fe`（V3.0.4）。V4 必须先完成数据资格与 golden cases，再决定 Qlib/QuantRuntime，之后才建设 typed Job、研究内核、组合 Shadow 和人工 Decision 闭环。权威设计见 `V4-DESIGN.md`、`V4-ARCHITECTURE-DECISIONS.md`、`V4-IMPLEMENTATION-PLAN.md` 和 `V4-ACCEPTANCE.md`。

## 1. 新会话从这里开始

维护或继续设计前按顺序执行：

```bash
cd /home/ghk/investment-home
git status -sb
git fetch origin --tags --prune
./bin/companion doctor
./bin/companion agent-check
python3 -m pytest -q
codex plugin list
```

然后阅读：

1. 本文：当前真相、边界和下一步；
2. [V3-DESIGN.md](V3-DESIGN.md)：目标架构和长期不变量；
3. [V3-ACCEPTANCE.md](V3-ACCEPTANCE.md)：已经验证与尚未验证；
4. [V4-DESIGN.md](V4-DESIGN.md) 与 [V4-ARCHITECTURE-DECISIONS.md](V4-ARCHITECTURE-DECISIONS.md)：下一版本的整体架构与外部能力边界；
5. [V4-IMPLEMENTATION-PLAN.md](V4-IMPLEMENTATION-PLAN.md) 与 [V4-ACCEPTANCE.md](V4-ACCEPTANCE.md)：实施顺序和不可跳过的 Gate；
6. [V2-OPERATIONS.md](V2-OPERATIONS.md)：本机运行、恢复和备份；
7. `AGENTS.md` 与已安装 Plugin Skills：Codex 实际行为契约。

可视化文档中心位于 [index.html](index.html)，它直接渲染本目录的权威 Markdown；交棒时仍以本文的状态与验收记录为准。

不要从旧聊天、`memory/*.md` 或 `portfolio/current.md` 推断真实持仓和个人事实。

## 2. 当前架构真相

```text
飞书用户 → cc-connect → Primary Investment Codex
                         ├─ Investment Companion MCP（77 tools）
                         ├─ Financial Kernel
                         ├─ Cognitive Ledger
                         ├─ Attention Engine
                         └─ 短命 Custom Agents

systemd 30 分钟 Tick → Schedule / Run / Outbox → 单一 cc-connect 唤醒桥
SQLite Schema 3 保存精确状态；Markdown 保存可读认知材料。
```

Primary Codex 是唯一最终判断、Agent 派遣、正式认知发布和用户沟通主体。Companion 不包裹 Codex，不自动交易。

## 3. 已实现并验证

- V2：Schedule、Watch、Observation、Event、Run、Case、Patrol、Artifact、Outbox、租约、幂等、重启恢复、在线备份。
- V3 Financial Kernel：Account、Asset、Ledger Draft/Confirm/Reverse、CSV Draft Import、Portfolio As-of、Market Snapshot、Trade Impact、Max Purchase、基础 Exposure、Calculation Record、Reconciliation。
- V3 Context：Investor、Mandate、Attention Policy 的 Draft/Current/Trial/Superseded。
- V3 Cognition：Thesis/Decision/Review 对象与不可变 Revision、Decision Freeze、Execution 分离、Recovery Package。
- V3 Attention：静默时段、每日预算、主题冷却、通知动作、反馈、投递状态。
- Source Health、`workspace-init`、`doctor`、Plugin 安装和 GitHub Private 仓库。
- 21 项自动化测试、MCP 协议检查、5 个 Custom Agent 真实派遣烟测、全新 Codex Readiness 前向验证、在线备份独立恢复。
- 项目级 SessionStart Hook 注入有界 `session-brief`；新 Codex 会话先获得健康与活跃状态，再由 Lifecycle Skill 按问题创建 Recovery Package。
- 项目级 `.codex/config.toml` 统一拥有共享 MCP；Custom Agent 只声明角色差异并继承项目 MCP，避免同名服务覆盖导致 Agent 不可用。
- 2026-08-15 已用 `market_scout` 成功重跑此前失败的收盘巡视；新 Run 成功完成，旧失败记录保留用于审计。

## 4. 当前生产运行状态

- 当前存在日常市场巡视、A 股收盘复盘、周度展望、交易后 Review 与一次性检查等主动任务；精确清单始终通过 Companion 工具读取。
- 基础心跳：每 30 分钟，仅执行本地到期检查；每 Tick 最多投递一个 Run。
- Investor、Mandate 与 Attention Policy 已有确认的当前版本。
- Account、Asset、Ledger、Calculation、Thesis、Decision、Execution 已有首批真实运行记录；具体金额、持仓和版本只能通过 Lifecycle/Financial 工具按需读取，不能从本文推断。
- 2026-08-18 最近一次 `doctor` 全部检查通过，数据库完整，无 failed Run 和 pending Outbox；这些是时点状态，下一会话仍需现场复核。

## 5. 已知限制

- Financial Kernel 是首版：尚无完整现金流调整收益率、复杂成本基础、完整公司行动、税务和通用多币种 FX 转换。
- Portfolio Exposure 仅在同一基准币种下精确工作；缺少 FX 时返回 Warning。
- Cognitive Ledger 已有版本机制，但尚无真实长期 Thesis/Decision 历史验证。
- Attention Engine 已有策略门控，但尚无真实误报、漏报和通知疲劳数据。
- 专用 Tushare Watch、公告和财报 Adapter 尚未完成；当前自动信息源以文件增量摄入为主。
- cc-connect 唤醒依赖一条休眠 Cron 作为唤醒原语；投资任务频率只在 Companion Schedule 中。
- Run 的 claim/lease 元数据尚未完全贯穿 cc-connect 唤醒执行链；当前成功重跑仍显示 `attempt=0`、`started_at=null`，需单独修复。
- 文件信息源首次巡视曾报告覆盖为 0，但重试时已存在可审计材料；Source cursor/coverage 诊断仍需加强，不能把该次结果解读为真实世界没有信息。
- 当前所有到期 Schedule 最终都进入 `codex_turn`；尚无 typed `data_job/quant_job`、Dataset Snapshot、Experiment Registry、QuantRuntime 或 Strategy Shadow Book。
- 当前 Observation/Market Snapshot 不具备完整 PIT、历史 Universe、公司行动和批量列式数据语义，不能被误用为 V4 市场数据库。
- `docs/EVENT-SYSTEM-PLAN.md` 是历史设计，不是当前实施说明。
- `docs/PRD-MARKET-DATA-ADAPTERS-AND-SHADOW-EVALUATION.md` 已废弃，只保留迁移说明；不得按旧 Phase 0 路线实现。

## 6. 下一步，不要提前扩张

1. V3 继续正常运行和复盘，不因 V4 开发暂停账本、主动任务或认知生命周期；
2. 执行 V4 Phase 0：冻结实现基线、引入 ordered migration、修复 Run claim/lease 完成链；
3. 执行 Phase 1：DataCapabilityMatrix 与官方 golden corpus，先决定哪些历史研究有资格；
4. 只有数据资格通过后执行 Qlib/QuantRuntime 生死 Spike；
5. 在 typed Job、PIT Snapshot、研究治理和组合 Shadow 通过前，不向用户开放 V4 自动候选或行动建议。

不得以接口数、模型数、Agent 数、实验数或 Token 消耗代替进展。每个阶段按 V4 Acceptance 的独立 Gate 决定 Go/No-Go。

## 7. 未来版本候选

只有真实使用证明需要时考虑：

- V3.1：账本导入/对账体验、收益率与成本基础、多币种 FX；
- V3.2：Thesis/Decision/Review 的真实生命周期与月末 Close；
- V4：可审计的数据资格、确定性量化研究、组合 Shadow、Codex 判断、飞书人工 Decision/Execution 闭环；
- V5：跨机器安装、加密备份、长期恢复演练和公开发行。

版本号只是建议，不是已批准路线。

## 8. 仓库与敏感信息

- 主项目：https://github.com/Lanbasara/investment-companion
- Plugin：https://github.com/Lanbasara/investment-companion-plugin
- 两者都是 Private。
- Tushare Token：`~/.config/tushare/token`，不得写入 Git。
- Tavily Token：本机受限凭据文件，Launcher 运行时读取。
- `.state/`、调查运行材料、账单和导出默认不进入 Git。

## 9. 交棒验收

下一会话只有在以下检查通过后才能修改生产状态：

- Git 工作树和远端差异已解释；
- `system_doctor` 与 SQLite Integrity 正常；
- `agent-check` 通过；修改 Agent/MCP 后还需运行 `./bin/companion-agent-smoke`；
- 当前 Context、Account、Ledger、Schedule 使用工具读取，而非凭记忆猜测；
- 修改前存在在线备份；
- 不把代码能力、自动化测试和长期真实使用验证混为一谈。
