# Investment Companion V4：实施计划

状态：Developer Handoff v1.0；尚未开始代码实现
日期：2026-08-18
基线：`pre-v4.0.0` / `b6ec6fe`（V3.0.4）
设计依据：[V4-DESIGN.md](V4-DESIGN.md)、[V4-ARCHITECTURE-DECISIONS.md](V4-ARCHITECTURE-DECISIONS.md)
验收契约：[V4-ACCEPTANCE.md](V4-ACCEPTANCE.md)

## 1. 计划目标

本计划用于把 V4 分解成可停、可验、可回滚的增量，不以一次性“做完 AI 炒股平台”为目标。每个阶段先证明前一项假设，再决定是否承担下一项复杂度。

主路径：

```text
现有 V3 稳定基线
→ 数据资格与官方 golden cases
→ QuantRuntime/Qlib 生死 Spike
→ typed Job 与不可变产物底座
→ 生产级 A 股日频数据域
→ 研究内核与实验治理
→ 组合 Shadow
→ Decision/飞书/人工执行闭环
→ 受限 Agent 研究
→ 足够样本后的长期评估
```

禁止跳过数据资格、PIT 和简单基准，直接进入 Agent 选股、模型调参或用户可见建议。

## 2. 交付规则

### 2.1 每个变更单元必须包含

- 明确问题、受影响不变量和不做事项；
- 离线 Fixture、golden case、失败路径和迁移测试；
- 产物/Schema/接口的版本与兼容说明；
- `pytest`、`doctor`、`agent-check` 及阶段专用验收结果；
- 文档、运维、回滚和秘密扫描更新。

### 2.2 默认关闭

所有 V4 能力初始由 feature flag 或 inactive JobDefinition 保护。真实数据流先 `dry-run`，再单 Stream canary，再全市场；研究结果先 file-only，再 Shadow，再有限 Decision Support。任何阶段不得自动开启用户通知。

### 2.3 一次只增加一种风险

同一个变更单元不得同时引入新数据源、新 Schema、新模型、新通知和新 Agent 权限。否则失败时无法定位是数据、计算、研究还是交互的问题。

### 2.4 时间估算

V4.0 的 Phase 0–7 预计需要约 **56–85 个工程工作日**，外加不少于 90 个日历日且满足样本量要求的前向观察。估算包含测试、故障注入、文档和回滚，不包含条件性的 Agent Research Loop、未来购买数据、GPU 或跨机器部署。

这是专业系统的真实工作量，不应以 Codex 能快速生成代码为理由压缩数据验证和前向观察。

## 3. Phase 0：基线冻结与 V3 硬化

预计：3–5 个工作日
目标：在增加 Research Domain 前，让当前状态、迁移和 Run 语义足够可靠。

### 3.1 交付

1. 保存实现前完整基线：Git commit/tag、Schema、Doctor、Agent、Schedule/Run 和备份恢复记录；
2. 修复 PROJECT-STATUS 与真实运行状态的偏差，确认 Investor/Mandate/Financial Facts 只通过工具读取；
3. 引入 ordered `schema_migrations`，停止仅靠 `CREATE IF NOT EXISTS + 覆盖 schema_version` 表达迁移；
4. 修复现有 Run claim/lease/attempt/started_at 与 cc-connect 完成链未贯通的问题；
5. 为后续 V4 约定 feature flag、数据根目录、版本命名和秘密边界。

### 3.2 测试

- 从 V3.0.4 数据库升级、重复升级、升级中断和备份恢复；
- 定时 Run、手工 Run、Codex 唤醒、失败和恢复的父状态准确；
- 现有 21 项测试与真实 `doctor/agent-check` 无回归；
- 不读取或改写用户私人事实来完成测试。

### 3.3 退出门

只有 Run 生命周期与 ordered migration 均通过故障注入，才能建立 V4 下级 Job。Phase 0 不添加市场数据或 Qlib 依赖。

## 4. Phase 1：数据资格与 Golden Corpus

预计：5–8 个工作日
目标：先证明有哪些历史事实能被可信重放，再决定可以研究什么。

### 4.1 DataCapabilityMatrix

对首批端点逐项实测并记录：

- provider、capability、连接器与 Direct API 可用性；
- 权限、频率、单次行数、历史起点和预期延迟；
- 退市证券、历史成分、修订、公告时间和公司行动覆盖；
- 字段定义、单位、复权、空值和业务错误语义；
- 许可、保留、备份和再分发限制。

首批范围优先：Asset Identity/股票历史列表、交易日历、日线、复权因子、停复牌、涨跌停、ST/板块状态、指数历史成分和必要公司行动。财务数据单独做 PIT 资格，不因单标的调用成功而默认全市场可用。

### 4.2 Golden Corpus

人工选择 20–50 个证券和一组官方事实案例，刻意覆盖：

- 上市、退市、代码/简称变化和历史指数调入调出；
- 停牌、复牌、ST、不同板块及不同时期涨跌停；
- 分红、送配、拆并股、复权因子变化；
- 财报首次披露、修订、更正和公告日；
- 正常交易日、节假日、缺失和合法空集。

每个案例保存原始官方/Tushare 响应、手工期望、知识截止点和数据质量说明。Golden Corpus 进入测试 Fixture；受许可限制的原文用脱敏最小样本或哈希引用。

### 4.3 决策输出

形成 `DATA-QUALIFICATION-v1.md`，明确：

- 历史研究允许的数据族；
- 只能从当前开始前向积累的数据族；
- 必须购买或寻找第二供应商才能研究的数据族；
- 暂时禁止的策略类型；
- 初始 Universe 与 Reality Model 可证明到什么程度。

### 4.4 Exit / No-Go

- 历史价格与股票池资格通过：进入价格类 Spike；
- 历史基本面 PIT 失败：V4.0 禁止历史基本面 Alpha 声明，只做前向积累；
- 历史 Universe/退市覆盖失败：禁止全市场历史选股回测，缩到可证明的固定测试集；
- 关键价格与公司行动也无法重放：停止 QuantRuntime Spike，先解决数据源。

## 5. Phase 2：QuantRuntime / Qlib 生死 Spike

预计：5–8 个工作日
目标：验证 Qlib 是否真正降低 V4 研究成本，不在生产架构中预设它必然入选。

### 5.1 隔离环境

- 建立独立量化环境和精确锁文件，不修改 Companion Core 运行依赖；
- 定义临时 `QuantRuntime` JSON/Parquet 输入输出契约；
- 禁止读取 Companion SQLite、Ledger、Mandate、凭据和飞书；
- 固定 CPU、内存、超时、日志和产物目录；
- 记录 Qlib、Python、依赖、OS 与代码哈希。

### 5.2 Spike 数据与策略

使用 Phase 1 Golden Corpus 及约两年日频数据，先运行：

1. 现金/指数或等权基准；
2. 一个简单动量/流动性排序；
3. 一个明确的定期 target_weights 组合；
4. 费用、整手、停牌和涨跌停的有限现实模型；
5. 同一输入在小型独立参考计算器和 Qlib 中运行。

Spike 只验证工程与语义，不宣称 Alpha。Codex 不参与信号计算。

### 5.3 必须逐日对齐

- 输入 Universe、eligibility 和 exclusion；
- 特征与信号可得时间；
- 目标权重、订单意图和未成交原因；
- 现金、持仓、费用和 NAV；
- 公司行动、停牌和恢复；
- 重跑的产物哈希。

### 5.4 Qlib Go

仅在以下全部成立时采用：

- Golden cases 通过，差异可以逐项解释；
- 不需要大面积 fork；
- Qlib 类型被限制在 Adapter 内；
- 运行、缓存和产物可以从项目 Snapshot 重建；
- 相比窄参考内核显著减少特征、训练、回测和报告代码；
- 锁版本后的安装、升级和诊断可维护。

### 5.5 Qlib No-Go 与替代

若关键 A 股规则只能近似、依赖不可维护或适配成本过高，采用 Polars/Pandas + DuckDB/Parquet 构建窄的 `NativeQuantRuntime`。仍保留相同项目契约，且不扩张为通用量化平台。

Phase 2 结束后必须提交正式 ADR，不能长期维持“两套都可能”的悬空状态。

## 6. Phase 3：Schema 4、Typed Job 与不可变产物

预计：7–10 个工作日
目标：把确定性计算接入现有 Schedule/Run，同时保持 V3 行为不变。

### 6.1 生产元数据

建议最小新增：

- `job_definitions`：allowlist handler、版本、输入/输出契约和资源预算；
- `job_runs`：父 Run、状态、幂等键、租约、attempt、取消和最终错误；
- `job_steps`：固定 pipeline 的阶段、依赖、输入输出哈希和重试；
- `data_objects`：data_root、相对路径、内容哈希、类型、大小和发布状态；
- `artifact_manifests`：不可变产物集合与 supersedes 关系。

不在本阶段建立 Strategy、Signal 和 Shadow 全部原生表。

### 6.2 ExecutionRouter

- Schedule 明确 `dispatch_type=codex_turn|deterministic_pipeline`；
- 旧 Schedule 默认保持 `codex_turn`；
- `tick()` 创建顶层 Run 后交给 Router；
- Job Worker 使用独立租约执行 allowlist handler；
- 成功原子发布 Manifest 后回写父 Run；
- 产物就绪或材料性失败才创建 Event/Outbox。

### 6.3 故障注入

覆盖：排队后死亡、Step 执行中死亡、产物写一半、Manifest 已写但 DB 未注册、DB 已注册但 Event 未发、Lease 过期、重复 Tick、父 Run 取消和重试耗尽。

### 6.4 退出门

同一 Job 重放不得重复副作用；任何中断都不能出现 ready 指向半份文件；旧 Codex Schedule 和飞书投递必须保持原行为。

## 7. Phase 4：生产级 A 股日频数据域

预计：10–15 个工作日
目标：建立 V4.0 可用的 Raw → PIT → Snapshot 数据闭环。

### 7.1 代码边界

建议目录：

```text
companion/
├─ jobs/            # Job contracts, router, worker
├─ data_domain/     # capability, adapters, normalization, manifests
├─ research/        # project-owned research contracts and registry
└─ quant_gateway/   # QuantRuntime port; no Qlib imports outside adapter

tests/fixtures/v4/
schemas/v4/
```

真实数据根目录位于仓库外并由配置引用；测试只使用受许可的最小 Fixtures。

### 7.2 数据治理对象

- `source_capabilities`：连接器、账号、限制和 probe 证据；
- `adapter_streams`：每条采集流的配置、cursor 与 watermark；
- `adapter_batches`：输入范围、原始对象、行数、cursor before/after 和错误；
- `dataset_snapshots`：knowledge cutoff、完整 denominator、分区/版本哈希和质量；
- `data_quality_issues`：范围、严重性、阻断与修复关系；
- Asset Identity 扩展：provider identifiers 的生效期和代码变更。

批量行不写入 Companion SQLite。

### 7.3 首批 Streams

按资格结果确定，默认优先：

1. Asset Identity / 历史上市状态；
2. 交易日历、A 股日线和复权因子；
3. 停复牌、涨跌停、ST/板块和可交易性；
4. 一个可证明的历史 Universe；
5. daily_basic 等非修订或可明确时点字段。

财务、ETF、基金和公告不与首批日线同时接入，避免一次增加多种时间语义。

### 7.4 质量与恢复

- 同批幂等、重叠窗口、分页重复和乱序；
- 非交易日、源端延迟、合法空集和部分覆盖；
- Raw 成功但 Normalize 失败时 cursor 不前进；
- 修订追加，不原地覆盖；
- Snapshot 不包含 blocked partition；
- 每日生成覆盖、延迟、修订和异常报告。

### 7.5 运行预算

Phase 4 先以当前主机实测冻结 SLO。初始 guardrail：源端数据可用后 30 分钟内完成单日全市场增量、质量检查和 Snapshot 发布；若无法达到，先优化批量与增量，不提高 Agent 或通知频率。

## 8. Phase 5：Research Kernel 与实验治理

预计：10–15 个工作日
目标：把一次性 Spike 升级为有版本、可拒绝、可重放的研究系统。

### 8.1 稳定对象

- `research_hypotheses`：假设族、经济逻辑、反证、基准和试验预算；
- `strategy_versions`：不可变 StrategySpec 与代码/环境引用；
- `experiment_runs`：Dataset Snapshot、运行状态、ExperimentBundle hash；
- `promotion_decisions`：reject、revise、research_passed、shadow、retire；
- Qlib Recorder/MLflow run ID 仅作外部引用。

SignalSet、TargetPortfolio、预测、指标和报告先作为 Manifest 内不可变产物；出现独立查询/事务需求后再原生化。

### 8.2 第一批研究方法

- 无 AI 基准；
- 透明价格/流动性排序；
- 一个多因子基线；
- 数据资格与透明基线通过后，才加入 LightGBM 等监督模型；
- 不引入 RL、LLM 直接信号和文本情绪交易。

### 8.3 实验治理

- 预注册 split、holdout、指标、成本和 pass/fail；
- 限制 hypothesis family 的试验次数；
- 测试集访问记账；
- 保留失败、拒绝和空结果；
- 冻结完整 denominator、全部分数/排名和 exclusions；
- 自动生成 leakage、turnover、capacity、regime 和敏感性诊断。

### 8.4 退出门

任何 ExperimentBundle 可在新临时目录中仅凭 Manifest、代码和锁文件重放；不同 run 的差异可定位到数据、代码、环境或参数，而不是“模型可能不一样”。

## 9. Phase 6：组合求解与 Strategy Shadow Book

预计：8–12 个工作日
目标：评价完整可投资组合，而不是只看几个入选股票后来涨没涨。

### 9.1 Portfolio Contract

实现项目自有 target_weights 产物与联合约束求解：信号、风险、成本、换手、现金、流动性、整手和可交易性共同参与。无可行解返回冲突，不靠裁剪掩盖。

### 9.2 A 股 Reality Model v1

至少覆盖：

- T+1、100 股整手和现金；
- 停牌、涨跌停和不可成交；
- 佣金、印花税的版本化假设；
- 成交价格时点、信号延迟和滑点；
- 分红/复权与退市处理。

所有近似写入 RealitySpec 和 Warning，不能用“行业默认值”隐藏。

### 9.3 Shadow Ledger

只有在本阶段才增加需要生命周期查询的原生对象，例如：

- `shadow_books`；
- `shadow_rebalances`；
- `shadow_fills`；
- `shadow_metrics`。

Shadow Ledger 与真实 Ledger 使用不同表、ID 前缀、工具和权限。任何代码路径都不能把 Shadow Fill 转成 confirmed Ledger Entry。

### 9.4 消融

同时保存：确定性基线、模型 target_weights、Codex rerank/否决、最终 Decision 和实际执行。评价各层增量，而不是把全部结果归因给“AI”。

## 10. Phase 7：Decision、飞书与人工执行闭环

预计：8–12 个工作日
目标：把合格研究证据接入 V3 已有个人决策生命周期。

### 10.1 决策 Gate

Primary Codex 只有在以下引用齐全时才能发布 Decision：

- confirmed Investor/Mandate 与最新 Portfolio；
- 合格 Dataset Snapshot、StrategyVersion 和 ExperimentBundle；
- 原始官方证据与 Thesis/critic 结果；
- 联合组合求解和 Financial Kernel Calculation；
- 不行动方案、失效条件、有效期和 Attention Decision。

### 10.2 ManualActionSpec 与 Execution

扩展现有 Execution 状态和 details schema，增加精确 `decision_revision_id` 与 `manual_action_spec_hash`，并覆盖 presented、rejected、superseded、deviated、有效期、报价时间、数量/整手、价格区间和重新校验条件。不能只引用 Cognitive Object 的可变 current pointer。

用户报告成交后继续沿用：Draft Ledger → 回显 → Confirmed Ledger → Execution link。不得从“我准备买”“我下单了”推断“已经成交”。

### 10.3 飞书逐级开放

```text
file_only
→ 仅发送 Research Ready 摘要
→ 用户主动请求时显示 Candidate Review
→ 明确 opt-in 的 Decision Proposal
→ Execution Follow-up / Review
```

每种消息有幂等键、展开入口、数据截止时间、有效期和静默策略。全量排名不发送到飞书。

### 10.4 退出门

完成一条端到端 Fixture：Snapshot → Experiment → Shadow → issued Decision → 飞书呈现 → 用户拒绝/接受 → 手工成交 Draft/Confirm → Review；每一层都能证明没有串写真实事实。

## 11. Phase 8：受治理的 Codex Research Loop

预计：6–10 个工作日；条件阶段
进入条件：Phase 5–7 稳定，且透明研究已形成可用基准。

### 11.1 交付

- Hypothesis Proposal 与实验预算审批；
- 生成代码的独立 Sandbox；
- Prompt/model/token/输入/输出 Provenance；
- 因子和实验语义去重；
- 静态检查、测试、golden cases 和确定性 validator；
- 预算耗尽、数据失败、无增量价值时自动停止。

### 11.2 明确不做

- 不直接接入 RD-Agent 常驻循环；
- 不允许 Agent 读取最终 holdout 后继续无登记调参；
- 不允许 Agent promote、发布 Decision、修改 Mandate 或发送飞书；
- 不允许生成代码直接访问生产数据根目录或 Companion DB；
- 不以实验数量或 Token 消耗作为进展。

### 11.3 Token 结果契约

按 hypothesis family 统计 tokens、运行次数、有效新假设、研究通过率、对基准的增量和人工节省时间。若 Agent Loop 无法持续优于“Primary Codex 手工提出少量假设”，停用自动探索。

## 12. Phase 9：数据/资产扩展与长期验证

预计：每个数据族 5–15 个工程工作日；前向观察至少 90 个日历日且满足样本门
目标：只根据已经批准的策略需求扩展，而不是为了数据面完整。

候选顺序：

1. ETF/指数与可投资基准；
2. 场外基金净值、确认日和申赎费用；
3. 经 PIT 资格验证的财务数据；
4. 官方公告和公司文件证据链；
5. 文本/事件特征，仅在确定性抽取与前向验证方案成立后。

每个数据族独立 Capability、Adapter、Golden Corpus、Snapshot Schema、质量 Gate 和回滚。不能沿用股票日线的时间语义猜测基金 NAV 或财报可得性。

## 13. 发布级别

| 级别 | 所需阶段 | 能力声明 |
|---|---|---|
| `v4-spike` | Phase 1–2 | 数据与 Qlib 适配结论；不可用于建议 |
| `v4-alpha` | Phase 3–5 | 可重放数据与实验；仅内部/file-only |
| `v4-beta` | Phase 6–7 | 组合 Shadow 与人工 Decision 闭环；需明确 opt-in |
| `v4.0.0` | Phase 0–7 全验收 + 足够前向运行证据 | 专业研究骨架可用；不声明 Alpha 已证明 |
| `strategy-eligible` | 单策略独立晋级门 | 该 StrategyVersion 可作为 Decision 证据之一 |

项目版本与单策略资格分开。发布 V4.0 不代表每个策略有效；某策略退役也不意味着系统架构失败。

## 14. 并行边界

可以并行：

- 文档/Schema 评审与 Golden Corpus 收集；
- 不同官方案例的人工核验；
- 在接口冻结后开发独立 offline Fixtures；
- Phase 6 后的 Shadow 运行与非侵入文档完善。

不得并行：

- 在数据资格未定时开发多个模型；
- 在 QuantRuntime 契约未定时同时接 Qlib、LEAN 和 FinRL；
- 在 Shadow 未稳定时开发用户可见自动候选；
- 在核心实验治理未完成时启动 Agent 自主研究；
- 在同一 Schema 迁移上由多个分支并行改表。

## 15. 回滚与停机线

### 15.1 工程回滚

- 暂停 V4 JobDefinition，不删除 Run、Raw、Manifest 或审计；
- 恢复迁移前数据库备份并保留失败升级副本；
- Qlib/Agent 环境可整体移除，不影响 V3；
- Derived/Cache 可删除重建，Raw/Manifest/DB 不可静默删除；
- 飞书 Decision Support 可单独关闭，V3 研究和生命周期继续工作。

### 15.2 产品停机线

以下任一长期成立，应停止对应能力：

- 数据无法证明 PIT 或许可不允许所需用途；
- Qlib 需要重度 fork 或无法通过现实规则；
- 策略无法优于简单基准或成本后为负；
- Agent 只增加试验和 Token，不产生可验证增量；
- 过期/错误建议、通知负担或人工摩擦高于现有流程；
- 维护成本超过个人系统的实际价值。

停止策略、框架或 Agent 不是项目失败，而是 V4 研究治理正常工作。

## 16. 每阶段统一验收命令

基础命令：

```bash
git status -sb
python3 -m pytest -q
./bin/companion doctor
./bin/companion agent-check
```

涉及 Agent/MCP 时再运行真实 `./bin/companion-agent-smoke`；涉及 Schema 时先备份并执行独立恢复；涉及真实数据时使用显式环境开关，默认测试不得访问网络或消耗模型 Token。

每阶段完成后更新 PROJECT-STATUS、V4-ACCEPTANCE、运维文档、数据口径和最新 Go/No-Go。阶段完成不代表整个 V4 完成。
