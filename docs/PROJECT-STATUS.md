# Investment Companion：项目状态与会话交棒

更新时间：2026-08-18
当前发布：主项目为 `v3.0.4`，Plugin 为 `v3.0.1`
当前定位：V3 Core 继续承载生产事实与主动任务；V4 工程实现已在 `feature/v4-professional-system` 完成，尚未迁移生产、尚未通过数据/策略发布 Gate。

用户已确认 V4 定义：**可审计、Codex 驱动、确定性量化内核支撑、飞书协作、人工执行的专业个人投资研究与决策系统**。代码起始点以 `pre-v4.0.0` / `b6ec6fe`（V3.0.4）冻结。当前已选择窄 NativeQuantRuntime，并完成 typed Job、数据/研究域、系统派生 walk-forward、未调参前向信号、双快照组合 Shadow、个人联合约束、Agent 反证血缘和人工 Decision 闭环；但 G1 数据资格、G2 official golden 对齐、真实前向样本和 G6 用户价值仍为 No-Go。工程完成不能被写成“高胜率选股已经有效”。

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
6. [DATA-QUALIFICATION-v1.md](DATA-QUALIFICATION-v1.md)、[V4-QUANT-RUNTIME-ADR.md](V4-QUANT-RUNTIME-ADR.md) 与 [V4-OPERATIONS.md](V4-OPERATIONS.md)：当前 No-Go、内核决策和迁移/回滚；
7. [V2-OPERATIONS.md](V2-OPERATIONS.md)、`AGENTS.md` 与已安装 Plugin Skills：生产 V3 与 Codex 行为契约。

可视化文档中心位于 [index.html](index.html)，它直接渲染本目录的权威 Markdown；交棒时仍以本文的状态与验收记录为准。

不要从旧聊天、`memory/*.md` 或 `portfolio/current.md` 推断真实持仓和个人事实。

## 2. 当前架构真相

```text
飞书用户 → cc-connect → Primary Investment Codex
                         ├─ Investment Companion MCP（108 tools）
                         ├─ Financial Kernel
                         ├─ Cognitive Ledger
                         ├─ Attention Engine
                         └─ 短命 Custom Agents

systemd 30 分钟 Tick → Schedule / Run / Outbox → 单一 cc-connect 唤醒桥
SQLite Schema 3 保存精确状态；Markdown 保存可读认知材料。
```

Primary Codex 是唯一最终判断、Agent 派遣、正式认知发布和用户沟通主体。Companion 不包裹 Codex，不自动交易。

V4 分支的目标架构已经落地，但当前生产仍是上图的 Schema 3。V3 systemd 服务已固定到 `/home/ghk/.local/share/investment-companion/runtime-v3` 稳定工作树，避免开发分支隐式打开生产库；Schema 4 只允许显式 `migrate`。

## 3. 已实现并验证

- V2：Schedule、Watch、Observation、Event、Run、Case、Patrol、Artifact、Outbox、租约、幂等、重启恢复、在线备份。
- V3 Financial Kernel：Account、Asset、Ledger Draft/Confirm/Reverse、CSV Draft Import、Portfolio As-of、Market Snapshot、Trade Impact、Max Purchase、基础 Exposure、Calculation Record、Reconciliation。
- V3 Context：Investor、Mandate、Attention Policy 的 Draft/Current/Trial/Superseded。
- V3 Cognition：Thesis/Decision/Review 对象与不可变 Revision、Decision Freeze、Execution 分离、Recovery Package。
- V3 Attention：静默时段、每日预算、主题冷却、通知动作、反馈、投递状态。
- Source Health、`workspace-init`、`doctor`、Plugin 安装和 GitHub Private 仓库。
- 21 项自动化测试、MCP 协议检查、5 个 Custom Agent 真实派遣烟测、全新 Codex Readiness 前向验证、在线备份独立恢复。
- 项目级 SessionStart Hook 注入有界 `session-brief`；新 Codex 会话先获得健康与活跃状态，再由 Lifecycle Skill 按问题创建 Recovery Package。
- 项目级 `.codex/config.toml` 统一拥有 Primary 的共享 MCP；5 个 Custom Agent 使用只读沙箱，并以完整同身份配置显式禁用可写的 `investmentCompanion` MCP。禁用配置已经真实 Codex 派遣验证，Agent 只返回提案，由 Primary 审阅和落库。
- 2026-08-15 已用 `market_scout` 成功重跑此前失败的收盘巡视；新 Run 成功完成，旧失败记录保留用于审计。
- V4 Schema 4 使用有序、带 checksum 的显式迁移；在生产数据库副本上完成升级、重复升级、备份哈希和 V3 独立恢复演练，未迁移生产库。
- V4 Data Domain 实现仓库外 CAS、Raw/PIT/语义分区验证、不可变 Snapshot、完整 denominator/Universe/exclusions、hash/路径篡改失败关闭和 Tushare canary Adapter。
- V4 deterministic pipeline 实现 Run→Job→Step、代码内 handler allowlist、lease/recover、资源预算、零模型 Token、网络/进程创建拒绝和原子终态。
- V4 研究与量化实现精确预注册、物理 split、系统派生 walk-forward、holdout access、试验预算、NativeQuantRuntime、独立参考计算、A 股 RealitySpec、未调参 `forward_shadow` 信号和 promotion Gate。
- V4 Shadow 把信号快照与执行快照分开，校验前向时序、连续状态、样本门和真实 Ledger 物理隔离；禁止生产回填。
- V4 决策闭环实现 confirmed Context、冻结 target、真实账户联合组合求解、单笔重新校验、不可变 Agent critique provenance、ManualActionSpec、手工 Execution 与 confirmed Ledger 精确关联；不存在券商连接。
- V4 MCP/CLI 已提供受限入口；generic Manifest 自证、外部直接完成 Experiment 和自主 Agent Research 均不开放；Gate 报告/证据/assessment 有独立 CLI 且不能自行授予 Go。
- 当前全量自动化测试为 64 项 Python tests + 8 个 subtests；最终发布仍需以现场命令复核。

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
- Tushare 白名单日频 Adapter 已完成工程实现，11 端点隔离 smoke 均 healthy，但没有生产 capability assessment；公告和财务 PIT Adapter 未实现。
- cc-connect 唤醒依赖一条休眠 Cron 作为唤醒原语；投资任务频率只在 Companion Schedule 中。
- 旧 `codex_turn` 仍由 cc-connect 唤醒；新 deterministic pipeline 有完整 claim/lease/attempt/终态，但尚未启用生产 worker。
- 文件信息源首次巡视曾报告覆盖为 0，但重试时已存在可审计材料；Source cursor/coverage 诊断仍需加强，不能把该次结果解读为真实世界没有信息。
- Schema 4 已实现 typed Job、Snapshot、Experiment Registry、Agent Invocation、QuantRuntime、Shadow Book 和 ManualAction，但生产仍为 Schema 3，任何普通启动都会拒绝隐式升级。
- 尚无通过 G1 的真实 Dataset Snapshot；当前 Observation/Market Snapshot 仍不能被误用为 V4 市场数据库。
- 当前没有 strategy-eligible 策略，不声明高胜率或 Alpha；G6 至少需要 90 个真实日历日且样本充分。
- Qlib/FinRL/LEAN 未接入；这是明确的 NativeQuantRuntime 决策，不是遗漏。若 G1 通过且出现模型训练需求，再做隔离 Qlib Spike。
- `docs/EVENT-SYSTEM-PLAN.md` 是历史设计，不是当前实施说明。
- `docs/PRD-MARKET-DATA-ADAPTERS-AND-SHADOW-EVALUATION.md` 已废弃，只保留迁移说明；不得按旧 Phase 0 路线实现。

## 6. 下一步，不要提前扩张

1. 继续用隔离的 V3 稳定工作树运行真实账本和主动任务，不迁移生产库；
2. 对 Tushare 白名单端点完成真实账号 probe、10–20 个交易日 canary、官方 golden corpus 和许可评审；
3. 用合格 Snapshot 完成 NativeQuantRuntime 与独立参考计算的逐日 G2 对齐；
4. 按 Gate 分段启用：G0 后只开放确定性 Job 底座，G1 后才开放 active data，G4 后开放 Shadow，G5 后且用户明确 opt-in 才开放 Decision Support；
5. 前向运行至少 90 个日历日且达到样本门，再评审单策略 G6；失败策略 reject/retire，不用模型覆盖结论。

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
