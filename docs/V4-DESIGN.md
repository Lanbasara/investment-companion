# Investment Companion V4：可审计的专业研究与人工决策系统

状态：架构基线 v1.1；工程主链已实现，生产资格 Gate 未通过
日期：2026-08-18
起始代码：`pre-v4.0.0` / `b6ec6fe`（V3.0.4）
决策依据：[V4-ARCHITECTURE-DECISIONS.md](V4-ARCHITECTURE-DECISIONS.md)
实施与验收：[V4-IMPLEMENTATION-PLAN.md](V4-IMPLEMENTATION-PLAN.md)、[V4-ACCEPTANCE.md](V4-ACCEPTANCE.md)

## 0. 当前实现边界

`feature/v4-professional-system` 已实现 Schema 4、有序迁移、内容寻址数据对象、PIT/Snapshot、Tushare 白名单采集、typed Job、NativeQuantRuntime、派生式 walk-forward、研究注册表、前向信号、双快照 Shadow、个人组合联合约束、Agent 评审血缘以及人工 Decision/Execution 闭环。全量 Fixture 使用零模型 Token 完成确定性阶段，并继续证明 Shadow 不写真实 Ledger。

这不是生产发布声明。生产数据库仍为 Schema 3，V4 feature 与 JobDefinition 默认关闭；G1 真实数据资格、G2 official golden 对齐、G4 真实研究、G5 真实飞书 opt-in 和 G6 至少 90 日前向价值均未完成。当前没有 `strategy-eligible` 策略，也不声明高胜率或 Alpha。

## 1. 一句话定义

V4 是一套**可审计、Codex 驱动、确定性量化内核支撑、飞书协作、人工执行的专业个人投资研究与决策系统**。

它不是自动交易机器人，也不是每天让 AI 搜索网络后给出几个股票。它把数据资格、研究假设、实验、候选、组合、个人约束、正式判断、人工成交和复盘连接成一条可重放、可反驳、可停止的证据链。

```text
V2：可靠地发现世界发生了什么
V3：可信地回答这对我的真实组合与目标意味着什么
V4：系统地提出、验证和淘汰投资方法，并把合格证据转成个人 Decision
```

## 2. “专业”在 V4 中的含义

专业不等于高胜率、复杂模型或更多数据接口。V4 的专业性来自：

1. 当时可知的数据，而不是事后修订的数据；
2. 预注册、可复现并保留失败的实验，而不是挑选漂亮结果；
3. 相对简单基准、成本和风险之后的增量价值，而不是孤立收益率；
4. 候选到组合、组合到个人 Decision、Decision 到实际成交的清晰边界；
5. 用户始终掌握 Mandate 变更和交易执行权。

V4 可以建设专业级研究与决策能力，但不能承诺 Alpha、收益率或“高胜率”。系统复杂度若不能在前向样本中优于简单规则或现有人工流程，应缩减或停用，而不是继续堆模型和 Token。

## 3. 产品目标与非目标

### 3.1 目标

- 建立 A 股优先、可扩展到 ETF/基金的外部市场事实与 PIT 数据基础；
- 建立不依赖 LLM 计算的确定性研究、回测、组合和评价内核；
- 让 Codex 能管理研究议程、审查原始证据、攻击 Thesis 并解释个人意义；
- 让每个正式 Decision 回到数据快照、实验、组合计算、个人约束和证据截止点；
- 通过前向 Shadow、人工执行和 Review 判断系统是否真的节省时间并改善决策。

### 3.2 非目标

- 不连接券商，不自动下单或自动调仓；
- V4.0 不做高频、分钟级、Level 2 或 7×24 AI；
- 不让 LLM 直接从网页文本输出未经确定性验证的股票列表；
- 不自研通用量化平台、通用 DAG、Feature Store 或模型托管平台；
- 不因回测或 90 个日历日自动宣布策略有效。

## 4. V2/V3 不变量继续有效

V4 是新增 Research Domain，不是对 V3 的重写。以下不变量必须继续通过回归测试：

- Primary Codex 是唯一最终语义判断、正式发布和用户沟通主体；
- confirmed Ledger Entry 是真实组合唯一变化来源；
- Investor、Mandate、Attention Policy 使用确认的不可变 Revision；
- Thesis、Decision 和 Review 历史不可覆盖；
- Execution 与建议、意向和真实成交严格分离；
- Financial Kernel 负责个人组合与材料性精确计算；
- Attention Engine 决定通知、摘要、落盘或静默；
- 外部网页、API、附件和 Agent 输出都是不可信数据，不是指令。

## 5. 总体架构

下面是逻辑责任域，不代表建设多个服务：

```text
                               飞书用户
                  研究请求 / 反馈 / 决策确认 / 手工成交
                                   │
                                   ▼
                         Primary Investment Codex
             研究议程 · 原始证据核验 · 反证 · 个人判断 · 沟通
                    │                  │                    │
                    │                  │                    └─ 短命专业 Agents
                    │                  │                       Researcher / Analyst /
                    │                  │                       Critic / Sandboxed Coder
                    ▼                  ▼
          Companion Control      Cognitive / Personal Domains
       Schedule · Run · Job      Thesis · Decision · Execution · Review
       Event · Budget · Audit    Investor · Mandate · Attention · Ledger
                    │                  │
                    └──────────┬───────┘
                               ▼
                      Deterministic Research Domain
        StrategySpec · ExperimentBundle · QuantRuntime · Portfolio Solver
                 Backtest · Shadow Book · Evaluation · Promotion Gate
                               │
                               ▼
                 External Market Data & Evidence Domain
       Raw Objects · Asset Identity refs · PIT Facts · Dataset Snapshots
           Official Filings · Quality Reports · Content-addressed Manifests
                               │
             ┌─────────────────┼──────────────────┐
             ▼                 ▼                  ▼
        Tushare Direct     交易所/发行人       Web/Tavily/本地信息源
        结构化采集         权威原始证据        仅发现与上下文
```

Companion、Research Kernel 和数据存储可以部署在同一台机器，但必须通过稳定契约交互，不能共享隐含内存状态或让外部框架类型渗透整个项目。

## 6. 权威事实与派生数据

V4 不宣称存在一个包办一切的“全局数据真相”。不同领域各自有唯一权威：

| 领域 | 权威来源 | 不是权威 |
|---|---|---|
| 真实账户、现金、持仓和成交 | confirmed Ledger + Financial Kernel 重建 | 飞书消息、券商截图推断、Shadow 账户 |
| 个人目标与硬约束 | confirmed Investor/Mandate Revision | Prompt、Agent 记忆、历史聊天 |
| 证券身份 | Companion Asset Identity 及带生效期的 provider identifier | Tushare symbol 或 Qlib instrument 自行另建身份 |
| 原始市场/公告事实 | 内容寻址 Raw Object + 来源元数据 | 聚合摘要、解析后的单一字段 |
| 规范化外部市场研究事实 | Append-only PIT store | Observation、Market Snapshot、Qlib cache |
| 某次计算实际采用的数据 | Dataset Snapshot / Market Snapshot 引用 | “当前最新数据” |
| 研究运行与晋级 | 项目 Experiment Registry / Promotion Decision | MLflow UI、Qlib Recorder 单独状态 |
| 长期投资认知 | Cognitive Ledger Revision | Shadow Trial、模型说明或 Agent 输出 |

Qlib 数据目录、DuckDB 索引、特征缓存和 MLflow 页面全部可重建。Feishu 是交互与传输面，不是事实存储。

## 7. 从调度到研究的运行模型

### 7.1 顶层 Run 与确定性 Job 分离

现有 Schedule 表达使命和节奏，Run 表达一次到期执行。V4 为 Schedule 增加明确 dispatch target：

```text
codex_turn
  需要语义判断、调查、园丁或用户沟通

deterministic_pipeline
  只执行 allowlist handler，不调用模型
  ├─ data_job
  ├─ dataset_job
  ├─ quant_job
  └─ evaluation_job
```

现有 Run 仍是顶层审计对象；新的 JobRun/JobStep 保存下级执行、输入输出、租约和资源。不得把依赖图藏进 Prompt 或 payload。

### 7.2 Job 状态与父子完成

```text
JobRun: queued → leased → running
                         ├→ succeeded
                         ├→ recoverable → queued
                         ├→ blocked / failed
                         └→ cancelled
```

父 Run 只有在所有必要 JobStep 原子发布成功后才能 `succeeded`。任何 `failed_terminal`、取消或不满足就绪屏障都会产生可审计结果，不能被包装成“没有候选”。取消父 Run 必须阻止未启动子步骤；已发布的不可变产物保留但标记为 orphaned/not-promoted。

### 7.3 就绪屏障

Quant Job 启动前必须验证：

- 所需 Adapter Stream 已覆盖目标交易日；
- Dataset Snapshot 已原子发布并通过质量门；
- Universe、公司行动、复权和交易日历版本齐全；
- StrategySpec、代码和环境版本可解析；
- 资源、实验次数和 Token 预算未超限。

缺失输入时 `waiting_inputs` 或 `blocked`，绝不能悄悄读取昨日缓存。

### 7.4 唤醒策略

数据更新、特征计算和回测默认零 Codex Token。只有以下情况才唤醒 Primary Codex：

- 新 CandidateSet/TargetPortfolio 通过固定研究门；
- 数据质量或策略漂移会影响已发布 Decision；
- 实验需要语义审查、反证或用户批准；
- 人工行动即将过期、发生偏离或需要 Review。

## 8. 外部市场数据与证据域

### 8.1 信息源职责

| 用途 | 首选来源 | 约束 |
|---|---|---|
| A 股/ETF/基金日频、财务、指数 | Tushare Direct API | MCP 用于交互研究，不作为批量生产采集通道 |
| 公告、财报、规则、产品文件 | 交易所、发行人、基金管理人、监管机构 | 原始发布者最高权威 |
| 宏观与行业 | 人民银行、国家统计局、外管局、部委、协会 | 只有 StrategySpec 声明需要时接入 |
| 新闻、文章和社交信息 | Web、Tavily、本地订阅 | 仅发现、背景和待核验线索 |
| 个人事实 | 用户确认、券商 Statement、confirmed Ledger | 不能由市场数据覆盖 |

Tushare 5000 积分可以支撑常规日频数据，但分钟、实时、新闻和公告属于独立权限；每个端点仍需现场 probe。[Tushare 权限说明](https://tushare.pro/document/2?doc_id=290)

### 8.2 数据资格门

任何策略开发前先形成 `DataCapabilityMatrix`：端点、账户权限、历史起点、更新延迟、最大行数、修订保留、退市覆盖、历史成分、公司行动、许可和实测证据。

若历史基本面无法恢复当时版本，V4 只能：

1. 做经验证的价格/成交量类历史研究；或
2. 从系统开始采集之日起做基本面前向 Shadow；或
3. 另行评估能够提供合格 PIT 历史的付费数据。

“不购买数据”不是原则；“没有明确需求与资格证据前不购买”才是原则。

### 8.3 Canonical 数据生命周期

```text
Capability Probe
→ 有界 Fetch
→ Raw Object 原子保存与哈希
→ Parse/Normalize
→ Identity Resolve
→ Append-only PIT Facts
→ Data Quality Rules
→ Dataset Snapshot Manifest
→ 可重建的 Qlib/Feature Cache
```

游标只在 Raw Object 与规范化事实均成功发布后推进。合法空集必须有交易日、范围、响应和规则证据；权限不足、解析失败、陈旧和部分覆盖不得变成“无变化”。

### 8.4 PIT 与修订

每条可修订事实至少保留：

- `asset_id` 与 provider identifier 生效期；
- `effective_at` / `effective_to`；
- `first_known_at`；
- `ingested_at`；
- `revision_id` / `supersedes_revision_id`；
- 原始值、规范化值、单位、币种和调整口径；
- raw object hash、adapter/parser version 和质量状态。

知识截止时间之后才公布的修订不得进入历史特征。财务数据、股票池、上市/退市、ST/板块、停复牌、指数成分和公司行动都必须遵守 PIT，而不只是财报字段。

### 8.5 Dataset Snapshot

每个实验只读取不可变 Snapshot。Manifest 至少包含：

- `snapshot_id`、`knowledge_cutoff` 和创建时间；
- 数据 Schema、分区、行版本及内容哈希；
- Universe 定义和完整 denominator；
- 交易日历、复权、公司行动与可交易性版本；
- 来源能力、覆盖率、质量问题和排除项；
- Adapter/Parser 代码版本与数据许可；
- 父 Snapshot 和修订 Delta。

Snapshot 发布使用临时目录 → 校验 → 内容寻址 Manifest → 原子重命名 → Companion 注册的顺序。数据库不得先宣称 ready 再等待文件写完。

### 8.6 存储边界

建议使用受限的外部数据根目录，而不是 Git 仓库：

```text
market-data/
├─ raw/          # 不可变、内容寻址，关键备份
├─ canonical/    # Append-only Parquet，可由 raw 重建但保留版本
├─ manifests/    # 不可变 Dataset Snapshot，关键备份
├─ derived/      # 特征与标签，可重建
├─ cache/qlib/   # Qlib 格式，可删除重建
└─ experiments/  # 运行产物与日志，按 Manifest 引用
```

Companion 保存受控 `data_root_id + relative_path + hash`，不得保存任意外部绝对路径。现有 workspace Artifact 继续承载人类可读认知材料；批量数据使用独立 DataObjectRef。

## 9. 确定性研究域

### 9.1 StrategySpec

一个策略在运行前必须冻结：

- 研究问题、经济直觉和明确反证；
- 资产类别、Universe 和可交易性规则；
- 特征、标签、时间对齐和缺失处理；
- 调仓频率、信号延迟和持有期；
- 组合构建、现金、集中度与流动性约束；
- 费用、滑点、冲击、税费和容量假设；
- train/validation/test、walk-forward、purge/embargo 与随机种子；
- 简单基准、主要指标、失败阈值和最大试验次数；
- 代码、环境、依赖和方法版本。

修改任何材料性字段都生成新 StrategyVersion，不能覆盖旧版本。

### 9.2 ExperimentBundle

每次实验完整保存：

```text
ExperimentBundle
├─ hypothesis_family / registered_question
├─ strategy_version
├─ dataset_snapshot_id
├─ code_commit / code_hash / environment_lock_hash
├─ split / seed / cost_and_reality_spec
├─ job_runs / resource_usage / agent_provenance
├─ predictions / denominator / exclusions
├─ target_portfolios / fills / positions / NAV
├─ metrics / diagnostics / quality_report
├─ artifact_manifest / logs / failures
└─ promotion_decision
```

失败、空结果和被拒绝实验同样登记。不得删除失败记录后重复试验直到出现漂亮结果。

### 9.3 研究生命周期

```text
idea
→ registered_hypothesis
→ data_qualified
→ experiment_running
→ rejected | revise_with_reason | research_passed
→ forward_shadow
→ eligible_for_decision_support | retired
```

系统永不自动晋级。确定性门只能阻止不合格结果；Primary Codex 负责研究解释和反证，用户批准会改变长期研究政策的决定。

### 9.4 防止 AI 式 p-hacking

- 每个 hypothesis family 预注册最大试验次数；
- 每次修改说明新证据和与上一实验的 Delta；
- 最终 holdout 一旦被 Codex、Agent 或开发者读取即视为已消耗；
- 策略晋级时 validation 与 final holdout 都必须通过同一份预注册机器阈值；development 只用于研发诊断，不能抵消后续阶段失败；
- 保留全部试验、失败和被拒绝特征；
- 多重检验、换手、容量和成本纳入晋级门；
- 允许 `abstain`，禁止为完成 Schedule 强行生成候选。

### 9.5 第一批策略范围

V4.0 只允许透明、可核验的日频策略：

1. 无 AI 的可投资基准；
2. 简单价格/流动性排序；
3. 一个有明确经济逻辑的多因子横截面基线；
4. 在数据资格通过后再评估 LightGBM 等监督模型；
5. 基本面、文本、事件或 Agent 生成因子按独立 Gate 后置。

强化学习、LLM 直接情绪交易、分钟级择时和自主策略工厂不属于 V4.0。

### 9.6 评价协议

至少覆盖：

- point-in-time 与历史 Universe 泄漏测试；
- walk-forward / rolling out-of-sample 与未见最终样本；
- IC/RankIC、分层单调性和预测稳定性；
- 扣费净收益、相对基准、回撤、尾部损失和换手；
- 流动性、容量、停牌/涨跌停、T+1 与整手现实；
- 市场状态、行业、规模和时间分段稳定性；
- 对参数、延迟、成本和缺失数据的敏感性；
- 与简单基准及 Codex 人工 rerank 的消融比较。

胜率只是一项描述统计，不能作为主要通过标准。

## 10. Qlib 与项目研究契约

V4 定义 `QuantRuntime` 端口，Qlib 只是第一个候选 Adapter：

```text
prepare(dataset_snapshot_id, strategy_version)
run(experiment_spec_id)
export_bundle(experiment_run_id)
health()
```

DB-aware Job 编排器只负责校验 Snapshot、物化 JSON 友好输入和发布产物；NativeQuantRuntime 的 `run_isolated` 计算区间由 audit guard 禁止文件、SQLite、网络和子进程，也不持有 Ledger、Mandate、飞书或凭据引用。未来第三方量化环境仍须通过文件/JSON/Parquet 契约独立运行。业务代码不得依赖 Qlib 内部类型。

首版已按 [QuantRuntime ADR](V4-QUANT-RUNTIME-ADR.md) 选择窄 `NativeQuantRuntime`，Qlib 当前为 No-Go/未引入。以后只有在 G1 数据通过、出现真实模型训练需求且同一 golden cases 对齐时才重评估；MLflow 若启用，也只能是 Experiment Registry 的可重建投影。

## 11. 从全量扫描到个人 Decision

### 11.1 必须保存完整 denominator

每次扫描必须冻结：

- as-of Universe 的全部资产；
- eligibility 与每项 exclusion reason；
- 每个合格资产的特征、分数、排名和数据质量；
- top-K 或组合构建结果；
- 方法、Snapshot 与代码版本。

只保存最终被 Codex 看到的几个候选会产生选择性记录，无法评价漏选、假阴性或 Codex rerank 的增量价值。

### 11.2 领域链路

```text
Dataset Snapshot
→ 预注册 deterministic Experiment / 派生式 walk-forward
→ 未调参 forward_shadow signal / target_weights
→ target_weights / portfolio solution
→ Primary Codex 核验原始证据并调用专业反证
→ Thesis / Decision Draft
→ Financial Kernel 读取真实 Portfolio + Mandate 联合求解
→ issued Decision + ManualActionSpec
→ Attention Engine
→ 飞书
→ 用户手工执行并报告真实成交
→ confirmed Ledger + Execution link
→ Review
```

量化层的模型风险、流动性和组合构建，与个人 Mandate、现金和真实持仓是两个连续 Gate；不能照搬交易平台的自动执行链。

### 11.3 组合求解

策略产物使用目标权重作为稳定层间语言。已实现的 `portfolio_rebalance_plan` 以真实 Ledger、冻结报价、RealitySpec 和当前 Mandate 做确定性投影；目标是软目标，个人与交易约束是硬约束。最终可行组合必须联合考虑：

- 预期信号、风险与相关性；
- 交易成本、换手和容量；
- 现金、整手、停牌和可成交价格；
- 个人集中度、流动性和禁区；
- 不行动、部分执行和替代标的。

如果约束无可行解，返回 `infeasible` 和明确的冲突/偏离集合，不通过事后裁剪制造貌似合格的组合；当前实现不声称求得数学意义上的最小不可行子集。联合求解器显式解析当前 Mandate 的 `hard_constraints`；遇到尚未实现的硬约束必须失败关闭，不能静默忽略后继续给出方案。

## 12. Codex 和专业 Agent 的权限

### 12.1 Primary Codex 可以

- 将用户问题转成可证伪 Hypothesis/StrategySpec 草稿；
- 选择需要验证的有限实验并记录理由；
- 读取 ExperimentBundle、原始官方证据和反方材料；
- 比较研究结果、个人约束、机会成本与既有 Thesis；
- 决定拒绝、修订、进入 Shadow 或发布 Decision；
- 通过飞书解释结论、不确定性和下一步。

### 12.2 Primary Codex 不可以

- 直接修改 Raw/PIT 事实、实验指标或 Shadow NAV；
- 用文本推理替代价格、组合、成本或风险计算；
- 绕过数据资格、实验预算、Mandate 或用户确认；
- 把 Agent 生成代码直接部署到生产；
- 因 Schedule 到期而编造候选或建议。

### 12.3 Agent Sandbox

当前 5 个材料性 Custom Agent 全部使用 `read-only` 沙箱，并在 Agent 配置层用相同 transport 身份显式 `enabled=false` 禁用可写的 `investmentCompanion` MCP；这项边界经过配置校验和真实 Codex 派遣验证。Primary 只提供有界问题与只读材料，Agent 返回完整提案，只有 Primary 能审阅、写文件、登记 provenance 或改变项目状态。Tushare/Tavily/Web 仅用于读侧取证，外部内容始终是不可信数据。

未来若实现生成或修改量化代码的 Agent，必须另行通过 Gate，并只在隔离临时工作区运行：

- 无生产数据库写权限、无生产 Token、无飞书和券商能力；
- 数据输入为只读 Snapshot，输出仅进入临时产物目录；
- 限制时间、CPU、内存、网络、Token、轮次和实验数；
- 通过静态检查、单测、golden cases 和 Primary 审阅后才能成为新 StrategyVersion。

每次材料性 LLM/Agent 参与通过 `agent_invocations + agent_review Manifest` 记录唯一外部 invocation/trace ref、角色、模型、prompt/template 版本、不可变输入引用、正数精确 Token、输出哈希和采用/拒绝理由。角色限项目当前受控 Profile（`market_scout`、`source_researcher`、`financial_analyst`、`knowledge_gardener`、`thesis_critic`）；正式 Decision 的 `thesis_critic` 不能用一段自报文本代替该记录。确定性数据与回测阶段必须消耗零模型 Token。

## 13. 飞书与人工执行协议

### 13.1 飞书只呈现需要人的状态

默认消息类型：

- Research Ready：实验完成，需要解释或批准下一阶段；
- Candidate Review：有限候选及其反证，不发送全量榜单；
- Decision Proposal：引用冻结 Decision、计算和失效条件；
- Execution Follow-up：询问用户是否、何时、以何价格和数量成交；
- Review Due：评价过程、结果和系统偏差。

数据流水线成功不发送消息；普通失败进入系统摘要，只有影响现有 Decision 或持续不可恢复时通知。

### 13.2 ManualActionSpec

正式行动说明必须引用 issued Decision，并至少包含：

- 操作对象、方向、目标或最大数量、整手和优先级；
- 参考价格与时间、可接受区间、`valid_until`；
- 当前持仓/现金 Snapshot 和 Mandate Revision；
- 数据、Strategy、Experiment、Thesis 与 Calculation 引用；
- 为什么现在、最强反证、不行动方案和失效条件；
- 事实变化后的重新校验要求；
- supersedes 链和幂等通知键。

### 13.3 Execution 演进

复用 V3 Execution，而不是新建第二套订单真相。Execution 必须引用精确的 issued Decision Revision 和 `ManualActionSpec` hash，不能只引用可能继续变化的 Decision object。未来状态需要覆盖：

```text
proposed → presented → accepted → ordered → partially_filled → filled
             ├→ rejected
             ├→ expired
             ├→ superseded
             └→ cancelled / deviated
```

用户报告成交后先建立待确认 Ledger Draft，回显并确认；只有 confirmed Ledger 与 Execution 关联后才改变 Portfolio。价格、现金或持仓在执行前发生材料性变化时，旧提案失效并重新计算。可执行性重验证只接受当前时刻（允许 5 秒调用偏差），不能用历史或未来 `as_of` 绕过最新账本、Context、报价与有效期。

## 14. Shadow 与学习闭环

### 14.1 三种 Shadow 不得混名

- Strategy Shadow Book：前向记录模型目标组合、假设成交、持仓、费用与 NAV；
- Decision/Execution Shadow：记录量化目标、Codex Decision、用户选择和实际成交之间的差异；
- Attention Shadow Mode：记录本应通知/静默及用户反馈。

### 14.2 Strategy Shadow Book

Shadow 使用与历史研究相同的 target-portfolio 和现实模型契约。信号只消费执行日前已经发布的 `signal_snapshot_id`；成交仿真另读执行日收盘后发布的 `execution_snapshot_id`。生产模式禁止两者复用、历史回填或开盘后才完成的信号。每个 rebalance 冻结完整 denominator、目标权重、未成交原因和成本。

前向评价包括：基准差、净收益、回撤、换手、容量、信号数、过期率、数据故障、模型 abstain 和状态分段。单只候选 MFE/MAE 仅用于诊断。

### 14.3 晋级与退出

90 天只是最低运营观察窗，不是 Alpha 证明。只有同时达到最小信号数、跨状态样本、数据可靠性、人工决策样本和预注册结果标准，才允许讨论 `eligible_for_decision_support`；样本不足则延长，不因日期到期自动晋级。

策略失效、数据资格下降、行为漂移或维护成本过高时可退役。退役保留完整历史，不删除失败。

## 15. 审计与可解释性

任何正式 Decision 必须可追踪：

```text
Decision Revision
├─ Investor / Mandate / Attention Revision
├─ Portfolio / Market Snapshot
├─ Thesis Revision / Evidence Cutoff
├─ StrategyVersion / ExperimentBundle
├─ Dataset Snapshot / Raw Object hashes
├─ Target Portfolio / Constraint solution
├─ Calculation IDs / Warnings
├─ Agent provenance / Critique
├─ ManualActionSpec / Notification key
└─ Execution / Ledger / Review（随后追加）
```

所有材料性状态变化使用稳定 ID、版本、内容哈希、创建者、时间、原因和 supersedes 链。人类可读报告与机器可重建 Manifest 同时存在，任何一方不能代替另一方。

## 16. 可靠性、安全与运维

- 采用明确的 ordered migration；Schema 升级前在线备份并做恢复演练；
- 产物使用临时写入、校验、原子发布和内容地址，禁止按路径 upsert 覆盖血缘；
- 对 Raw 保存后崩溃、Normalize 中断、Manifest 发布中断、Job lease 过期、Event 重投和用户重复确认做故障注入；
- Tushare Token、数据许可受限内容、用户账单和真实组合不进入 Git、模型 Prompt 或错误回显；
- Qlib/Agent 环境默认无生产写权限和无外网；
- 备份关键对象：Companion DB、Raw、Manifest、认知材料和用户导出；Derived/Qlib cache 可重建；
- V3 调度与飞书继续运行，V4 每个能力通过 feature flag、dry-run、canary 和 Shadow 逐步启用。

## 17. 成功指标与停机线

V4 分三组验收，不能混成一个“跑了 90 天”：

### 17.1 数据与运行

覆盖、时效、修订、PIT、幂等、恢复、可追溯、资源和秘密安全达到预注册标准。

### 17.2 研究方法

相对简单基准在未见样本和前向 Shadow 中体现成本后增量价值；失败、过拟合、容量和不稳定性被如实保留。

### 17.3 用户决策价值

用户获得更少但更清晰的候选；研究时间下降；错误建议、过期建议、执行摩擦和通知负担可衡量；系统不诱导越过 Mandate。

任一研究自动化长期只增加复杂度、Token 或通知，而不能改善相应基准，就停止该能力。

## 18. V4 能力边界

### V4.0：可信研究骨架

- 数据资格与 golden cases；
- typed deterministic jobs；
- A 股日频 Canonical/PIT 基础；
- 一个可替换 QuantRuntime 与透明策略；
- Experiment Registry、完整 denominator 和组合 Shadow；
- Decision/Execution/Feishu 人工闭环；
- 不宣称策略已证明有效。

### V4.1：研究覆盖扩展

- ETF/基金/指数；
- 经资格验证的基本面与官方公告；
- 多策略比较和组合；
- 更完整的 A 股 Reality Model。

### V4.2：受治理的 AI 研究

- Agent Sandbox；
- 有预算的假设与因子探索；
- 自动去重和确定性验证；
- 仍无自动晋级、自动建议或自动交易。

版本号表示能力边界，不表示投资收益已经得到证明。

## 19. 本轮设计的明确取舍

- 保留旧 PRD 的权限感知 Adapter、不可变证据和前向评价思想；
- 废弃“先让 AI 广搜候选再补数据平台”的阶段顺序；
- 废弃以单候选成绩为中心的 V4 定义，改为完整 denominator 与组合 Shadow；
- 不把影子对象做成第二套 Thesis/Decision；
- 不决定 Qlib 必然入选，先通过数据资格与适配 Spike；
- 不新增网站、券商连接、常驻 Agent 或 7×24 LLM。

V4 的实现只有在保持这些取舍时，才是当前项目自然生长出的系统，而不是开源组件的机械拼接。
