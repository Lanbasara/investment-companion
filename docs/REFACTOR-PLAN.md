# Investment Companion：架构升级实施计划

状态：Batch A–C 代码收口已完成；Batch D 开发态工具面已完成，待 Plugin/Profile 发布验收
日期：2026-08-23
生产影响：当前无；生产切换需要独立变更窗口

## 1. 改造目标

把现有 V2–V7 能力收敛为一个版本无关、Codex 原生、可扩展的模块化单体，使系统真正围绕以下主链路运行：

```text
市场与用户事实
  → 研究和预测
  → 独立验证
  → 组合决策
  → 确定性风险闸门
  → 用户行动卡
  → 手工执行与确认账本
  → 现金流调整绩效
  → 复盘、修订或淘汰
```

## 2. 当前工程基线

截至 2026-08-23：

| 指标 | 当前值 | 含义 |
|---|---:|---|
| Python 模块 | 49 | application/bootstrap/interfaces/platform 与专业投资服务边界已建立 |
| `companion/` 代码行 | 约 19,400 | 保留旧能力的同时，大型协调类已明显缩小 |
| SQLite 业务表 | 60 | 历史事实丰富，不适合推倒重建 |
| MCP 工具 | 22 个 Investment + 157 个兼容/Admin | 生产默认仍为兼容模式 |
| V4/V5/V6 前缀工具 | 71 | 版本名称已进入公共架构 |
| 最新开发验收 | 156 passed、8 subtests passed | 发布前工具面补齐后的全量回归基线 |

主要结构债务：

| 位置 | 当前规模 | 问题 |
|---|---:|---|
| `core.py` | 约 513 行 | 缩为兼容 Facade 与调查/哨骑协调；Workflow、Outbox、System Operations 已抽离 |
| `operating.py` | 748 行 | 缩为 Brief、Scorecard、Today 投影；Program 与 Portfolio Decision 已抽离 |
| `v5_quant_experiment.py` | 1,607 行 | 数据准备、扫描、任务注册和评价混合 |
| `v6_predictive_recommendations.py` | 1,260 行 | 股票、ETF、预测、反馈和任务编排混合 |
| `db.py` | 1,102 行 | 所有历史 Schema 和数据库基础设施集中 |

生产中已经有真实扫描、预测、Schedule、Run 和 Delivery；专业研究内核中的 Strategy、Experiment、Shadow 和 ManualAction 尚未形成常态主链路。因此重构必须先连接已有能力，暂停继续横向增加新版本功能。

## 3. 迁移原则

1. 使用 Strangler 迁移：先加版本无关门面，再逐模块替换内部实现。
2. 第一阶段不删除表、不改历史 ID、不重建账本、不批量重建 Schedule。
3. 新旧接口对同一输入做结果对照；兼容别名在插件和生产完成迁移后才退役。
4. 每个任务只改变一个职责，必须带测试、迁移说明和回滚办法。
5. Feature、Plugin、systemd、cc-connect 和生产数据库切换仍需独立批准。

## 4. 目标代码结构

这是物理拆分方向，不要求一次创建全部文件：

```text
companion/
  bootstrap/          # Composition Root、配置、Feature 装配
  domain/
    policy/           # Investor、Mandate、Investment Policy
    portfolio/        # Ledger、Accounting、Valuation
    research/         # Evidence、Thesis、Strategy、Validation
    decision/         # Portfolio Construction、Decision
    risk/             # 确定性 Risk Gate
    execution/        # Manual Action、Execution、Reconciliation
    performance/      # Return、Benchmark、Cost、Attribution
  application/
    investment_home.py
    contexts.py       # portfolio/research/decision/evaluation context
    commands.py       # 窄写命令
  platform/
    data/             # PIT、CAS、Manifest、Provider Adapter
    time/             # Market Calendar & Clock
    workflow/         # Schedule、Run、Job、Wake
    delivery/         # Attention、Delivery、cc-connect envelope
    governance/       # Audit、Gate、Version、Change Proposal
  infrastructure/
    sqlite/           # Repository 与 Migration
    tushare/          # 数据供应商适配
    cc_connect/       # 通信适配
  interfaces/
    investment_mcp.py # 默认 Codex 语义工具
    admin_mcp.py      # 运维和兼容工具
    cli.py
```

领域包不能依赖 `interfaces`、`infrastructure` 或版本化 Pipeline；基础设施通过窄接口实现领域需要的端口。

## 5. 当前模块到目标模块

| 当前模块 | 目标归属 | 迁移方式 |
|---|---|---|
| `core.py` | `bootstrap` + `platform/workflow` + 调查应用服务 | 逐方法搬迁，最终只保留 Composition Root 和兼容代理 |
| `financial.py` | `portfolio` + `risk` + `performance` | 先保持 Calculation 兼容，再拆确定性引擎 |
| `cognition.py` | `policy` + `research` + `decision` + `execution` | 保留不可变 Revision 和现有 ID |
| `data_domain.py`、`v4_data.py` | `platform/data` | 合并公共 PIT/CAS 契约，不重建数据对象 |
| `research.py`、`shadow.py`、`quant_runtime.py` | `domain/research` | 形成 Research Validation Pipeline |
| `operating.py` | `application` + `policy` + `decision` + `performance` | 拆 Program、Research Case、Action Card、Briefing |
| `v5_quant_experiment.py` | Research Pipeline 实现 | 去除版本和“试验”产品身份 |
| `v6_predictive_recommendations.py` | 股票/ETF Strategy 实现 | Forecast 和反馈迁移到统一 Strategy Evaluation |
| `attention.py`、`delivery.py` | `platform/delivery` | 保留送达回执语义 |
| `jobs.py`、Schedule/Run/Wake | `platform/workflow` | 接入统一 Market Calendar，不改变现有运行历史 |
| `mcp_server.py` | `interfaces/investment_mcp` + `admin_mcp` | 先建工具 Profile，再保留兼容别名 |
| `db.py` | `infrastructure/sqlite` | Migration 与 Repository 分离，Schema 追加式演进 |

## 6. 五批任务与退出门

### Batch A：架构基线与保护网

任务：发布目标架构、领域词典、迁移映射；登记黄金场景；添加依赖边界、旧大型模块增长预算和版本前缀测试。

退出门：全量测试、doctor、Git 差异审查通过；无生产文件和数据库变化。

完成记录（2026-08-23）：96 项测试、8 个子测试、Agent 配置检查和生产只读 doctor 全部通过；未修改生产数据库、Feature、Schedule、Plugin、systemd、cc-connect 或飞书配置。

### Batch B：模块化地基

任务：建立 Composition Root；提取共享错误、ID、Canonical JSON 和审计端口；引入 Repository 接口；建立兼容 Facade；把 `core.py` 的调查、调度和系统运维职责逐块搬出。

退出门：旧 CLI/MCP 和数据库结果保持兼容；`core.py` 明显缩小；架构测试阻止反向依赖。

完成记录（2026-08-23）：已提取 Foundation、Audit Trail、Composition Root、确定性 Job Handler，并将 Schedule/Run、Outbox/Wake 和 System Operations 搬入 `platform` 服务；`core.py` 由 1,101 行缩至约 513 行，旧 CLI/MCP 调用保持兼容。本阶段不为了“形式完整”再包一层无语义 Repository；当前 SQLite transaction 仍是真实持久化边界，聚合专属 Repository 只在替换存储或演进 Schema 时按需抽取，不作为本次收口的伪依赖。

### Batch C：专业投资核心

任务：分离 Policy 与 Ledger；统一 Research/Forecast/Strategy Validation；实现 Portfolio Decision；建立确定性 Risk Gate；分离 Performance Measurement 与 Review/Change Proposal。

Risk Gate 首批必须覆盖：当前 Mandate、确认账本、最低现金、单标的集中度、允许/禁止范围、流动性参与率、数据新鲜度、行动有效期、重复行动和 A 股交易现实约束。

Performance 首批必须覆盖：期初期末估值、期间现金流、费用和税、时间加权或资金加权收益、Program 基准、最大回撤，以及 Decision/Strategy 到结果的稳定引用。

退出门：黄金场景贯通“研究→建议→风控→人工成交→真实收益→修订提案”；Review 无权修改当前 Strategy。

完成记录（2026-08-23）：首版 Risk Gate、Performance Engine、Review/Change Proposal、Research Validation、Actionability、Execution Lifecycle 和 Briefing 均已实现。Program/Policy 生命周期已搬入 `application/programs.py`，Opportunity/Portfolio Decision/DecisionQueue 已搬入 `application/portfolio_decisions.py`，`operating.py` 仅保留经营投影职责。新增 Research Catalog 把持续扫描、旧预测、前向复核和正式验证映射为稳定 `ResearchRecord`；旧 V5/V6 标签只是 `research_method_only`，不会被伪装成 StrategyVersion。只有真实 Strategy Registry 与 Validation Calculation 可以授予决策资格。Ledger 仍由 Financial Kernel 单一掌握，没有为达成文件数量而重复封装。

新增 Execution Lifecycle Service 后，Action Card 接受、Execution 准备、用户报告订单、待确认成交、确认 Ledger 和部分/全部/偏离成交已经成为可追溯的不同状态。默认 Codex 工具面使用 `investment_action_plan` 构建带确定性风险结果的单项行动方案，以统一 Transaction 命令登记/确认/对账，并通过统一 Execution 命令处理人工执行；真实成交偏离建议时保存事实并标记偏离，不回写或美化原 Decision。

新增 Investment Briefing Service 后，Today 与 Investment Home 已能把待确认成交、在途订单、执行偏离和最新对账差异提升为用户待办；日/周/月 Brief 自动引用冻结的 Execution/Ledger/Reconciliation Calculation。Brief 不能自行声明成交或改写事实；已复盘的历史执行结果也不会永久阻塞新的无行动结论。

### Batch D：Codex 与用户使用面

任务：实现 `investment_home` 与四个 Context Workbench；增加 Market Calendar；拆 Investment/Admin MCP；更新 Plugin Skills；保持 cc-connect 和飞书交付路径。

退出门：默认 Codex 工具面保持有界、版本无关且覆盖完整用户闭环；用户自然语言无需出现 Program、Opportunity、Manifest、Gate 或版本号。

当前完成：五个 Context Workbench、统一 China Market Calendar 和 22 工具 Investment MCP release profile 已实现。发布前审计发现原 15 工具无法建立 Program、推进 Opportunity/Action、冻结来源、生成 Brief 或管理 Schedule/Wake/Delivery，因此补为 22 个聚合语义入口，而不把功能不完整伪装成“工具少”。Research Context 只暴露版本无关 Research Catalog，Transaction、Execution、Workflow 和 Delivery 均收敛为稳定命令。用户已选择飞书自然语言为正式交互，不开发业务交互卡片。Capability Registry 已成为工具发现、验证、分派与 provider manifest 的唯一机器权威，旧 `all`/`admin` 双轨已退出。

### Batch E：兼容迁移和生产切换

任务：双读对照、历史重放、备份恢复演练、固定 Runtime、Plugin 更新、灰度切换、监控和旧别名退役计划。

退出门：零历史丢失、零虚假成交、零重复必报通知；生产切换经用户单独批准并有可恢复备份。

## 7. 黄金工作流

自动化验收至少覆盖五条跨模块不变量，正式目录位于 `tests/fixtures/architecture/golden-workflows.json`：

1. 未确认流水不改变组合，确认后才改变。
2. 数据扫描和未验证预测只能产生研究材料，不能授权 Decision、Execution 或 Ledger。
3. 行动型 Decision 必须同时通过研究验证和风险闸门；接受建议仍不创建成交。
4. 客观绩效只能来自 Calculation；反馈复盘不能修改当前策略。
5. Schedule/Run 完成与用户结果送达是两个独立事实。

## 8. 每个任务的完成定义

- 代码：单一职责、版本无关命名、无越层访问。
- 数据：保留 ID、血缘、时间语义、审计和幂等性。
- 测试：相关单测、黄金场景和全量回归通过。
- 运维：说明 Feature、迁移、回滚和生产影响。
- 用户：明确改善了哪一个“发现、决策、执行、盈利衡量”环节。

## 9. 停机线

出现确认账本被非 Ledger 路径修改、研究任务生成真实成交、数据血缘断裂、Required Delivery 被静默、历史 Revision 被覆盖或新旧结果无法解释地分叉时，立即停止对应迁移单元，保留审计并回到上一兼容实现。
