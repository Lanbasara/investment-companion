# Investment Companion V4：架构决策与开源借鉴边界

状态：V4 架构基线，实施前必须遵守
日期：2026-08-18
适用基线：`v3.0.4` / Schema 3
关联文档：[V4-DESIGN.md](V4-DESIGN.md)、[V4-IMPLEMENTATION-PLAN.md](V4-IMPLEMENTATION-PLAN.md)、[V4-ACCEPTANCE.md](V4-ACCEPTANCE.md)

## 1. 本文解决什么问题

V4 会学习 Qlib、LEAN、RD-Agent(Q)、FinRL-X 等项目，但不会把它们拼成另一个平台。本文固定每项外部能力为何进入、进入哪一层、谁拥有事实、如何移除，以及什么情况下拒绝采用。

任何未来实现如果只能回答“这个开源项目有这个模块”，却不能回答“它解决了 Investment Companion 的哪个既有问题”，均不得合并。

## 2. 先从当前项目出发

V2/V3 已经建立五个不能被外部框架取代的核心：

1. Primary Investment Codex 是唯一语义指挥、最终判断和用户沟通主体；
2. Companion 保存 Schedule、Run、事件、审计、个人事实和认知生命周期；
3. Financial Kernel 负责真实组合与材料性精确计算；
4. Cognitive Ledger 保存不可改写的 Thesis、Decision、Execution、Review；
5. 用户通过飞书协作并手工执行，只有 confirmed Ledger Entry 改变真实组合。

V4 新增的是可审计的数据与量化研究能力，不是替换上述主体关系。

## 3. 外部能力进入项目的六项检查

引入任何框架、库、服务或模型前必须回答：

| 检查 | 必须回答的问题 | 不合格表现 |
|---|---|---|
| 领域归属 | 它解决哪个项目内问题？ | 因为流行、Star 多或功能列表长而接入 |
| 真相归属 | 谁是权威事实源？ | Qlib、MLflow 与 Companion 各保存一份互相冲突的“真相” |
| 契约边界 | 项目对象是否独立于外部类型？ | 业务代码到处出现某框架的 Dataset、Order 或 Model 类型 |
| 可替换性 | 删除它后哪些稳定契约仍成立？ | 一旦移除就无法读取历史实验或 Decision |
| 失败边界 | 它失败时系统如何降级？ | 框架异常被误报成“无候选”或“策略无效” |
| 维护成本 | 减少的代码是否多于新增的适配、运维和升级成本？ | 为使用框架而长期维护大面积 fork |

## 4. 决策总览

```text
项目自有、不可替换
  Companion Control & Governance
  Canonical Market/PIT Contracts
  Personal Ledger / Context / Cognition
  StrategySpec / ExperimentBundle contracts
  Manual Decision / Execution / Review lifecycle

隔离、可替换
  Qlib QuantRuntime adapter
  Qlib Recorder / 可选 MLflow 投影

只学习设计，不进入运行依赖
  LEAN：Time Frontier、模块接口、Reality Model 分类
  FinRL-X：target_weights 作为层间契约
  RD-Agent(Q)：假设—实现—验证—反馈与因子去重循环
```

## 5. ADR-001：V4 继续是 Codex-native，而不是 Quant-platform-native

**决策**：Primary Codex 继续驱动研究议程、证据审查、个人意义判断、Decision 发布和飞书沟通。确定性内核只返回事实、计算、候选、组合与验证结果。

**原因**：当前项目的价值来自将市场研究与个人目标、流动性、真实组合、既有 Thesis 和机会成本连接起来。量化平台不拥有这些上下文，也不应成为另一个最终决策者。

**禁止**：

- 让模型或扫描器直接向飞书推荐股票；
- 让外部量化框架发布 Thesis、Decision 或 Execution；
- 让 Agent 绕过 Financial Kernel 或 Mandate 硬约束；
- 让 Codex 用自然语言计算替代确定性数据作业。

## 6. ADR-002：采用逻辑分层的本地模块，不建设微服务群

**决策**：V4 保持单用户、本地主机优先。Companion 仍是一个项目内控制与治理核心；批量数据和量化运行通过受控子进程或隔离环境执行。

**原因**：当前负载是日频/周频研究，不需要 Kafka、Kubernetes、分布式 Feature Store 或 7×24 Agent。真正需要隔离的是依赖、资源、失败与生成代码，而不是组织上的微服务数量。

**边界**：

- SQLite：控制、治理、个人事实、审计和小型元数据；
- 内容寻址文件：原始响应、不可变 Manifest、研究产物；
- Parquet + DuckDB：批量规范化市场数据和查询；
- Qlib 数据目录：由 Canonical Snapshot 可重建的缓存；
- 独立量化环境：固定依赖、无默认外网和无生产写权限。

## 7. ADR-003：Canonical 数据与 PIT 语义由项目拥有

**决策**：Tushare、交易所和其他来源只提供观测；项目自己的 Canonical Data Contract 决定身份、时间、修订、可得性、股票池和质量语义。Qlib 数据不得成为权威事实源。

**最小时间语义**：

- `effective_at`：事实描述的经济或交易时期；
- `first_known_at`：该版本最早可被市场参与者取得的时间；
- `ingested_at`：本系统取得它的时间；
- `revision_id`：同一事实后续修订的稳定版本；
- `knowledge_cutoff`：实验或判断允许读取的最晚知识时间。

历史回填若无法证明 `first_known_at`，必须标记 `historical_pit_unproven`，不得用于声称无偏的基本面回测。Qlib 的 PIT 查询能力只能消费可信 PIT，不能制造可信 PIT。[Qlib PIT 说明](https://github.com/microsoft/qlib/blob/main/docs/advanced/PIT.rst)

## 8. ADR-004：现有 Schedule/Run 是顶层控制面，确定性 Job 是下级执行面

**决策**：保留 V2/V3 Schedule、Run、租约、恢复和审计；新增显式 `deterministic_job` 路径，而不是把数据计算继续包装成 `codex_turn`。

```text
Schedule Run
├─ codex_turn：需要语义判断、调查或维护
└─ deterministic_pipeline
   ├─ data_job
   ├─ dataset_job
   ├─ quant_job
   └─ evaluation_job
```

**必须定义**：父子完成、依赖就绪、租约、阶段重试、取消传播、资源预算、幂等产物和失败状态。V4.0 不提供用户可编程 DAG，也不允许在数据库中存任意 Shell 命令；只执行代码仓库内 allowlist 的 handler。

## 9. ADR-005：项目拥有稳定研究契约，外部类型不得外泄

Spike 前只冻结三类稳定契约：

1. `StrategySpec`：研究问题、数据、特征、标签、组合、成本、基准和验证协议；
2. `ExperimentBundle`：输入 Snapshot、代码/环境版本、运行结果、失败和全部产物清单；
3. `ManualActionSpec`：Decision 面向用户的结构化行动说明与有效性条件。

`SignalSet`、`TargetPortfolio`、风险求解结果和各类指标首先作为内容寻址的版本化产物。只有真实实现证明它们需要独立查询、状态机或事务时，才升级为原生实体。这样既保持接口清晰，也避免在 Spike 前把错误理解固化成十几张表。

## 10. ADR-006：Qlib 是候选 QuantRuntime，不是 V4 架构

**学习并可能直接复用**：

- DataHandler/Dataset 和表达式特征；
- 模型训练、预测、回测与分析工作流；
- Qlib Recorder 的 Experiment → Recorder 结构；
- 日频横截面研究所需的模型与报告能力。[Qlib 工作流](https://github.com/microsoft/qlib)

**项目适配方式**：

- Qlib 运行在独立、锁版本的环境；
- 输入只能是项目发布的 `dataset_snapshot_id`；
- 输出只能通过项目定义的 Manifest/JSON/Parquet 契约返回；
- Companion 只保存 Qlib run ID、内容哈希和项目级治理状态；
- Qlib `.bin` 数据、缓存和 Recorder 数据均可从 Canonical 数据与 ExperimentBundle 重建。

**Qlib No-Go**：

- 需要大面积 fork 才能表达 A 股关键规则；
- 停牌、T+1、整手、分时期涨跌停、退市或公司行动只能不可解释地近似；
- 升级频繁破坏适配层；
- 与官方 golden cases 和独立参考计算无法对齐；
- 运维复杂度超过其减少的研究代码量。

No-Go 后使用 Polars/Pandas + DuckDB/Parquet 建设窄的确定性研究内核，但仍不自研大而全量化平台。

## 11. ADR-007：MLflow 是可选实验投影，不是治理真相

Qlib Recorder 提供实验管理，并可由 MLflow 实现。[Qlib Recorder](https://github.com/microsoft/qlib/blob/main/docs/component/recorder.rst)

**决策**：项目的 ExperimentBundle、Promotion Decision 和审计记录是权威；MLflow 只提供搜索和可视化。初期可先使用本地 Recorder，只有多实验比较确有操作价值时才启用 MLflow UI。

删除 MLflow 后，历史 StrategySpec、输入 Snapshot、指标、产物和晋级决定仍必须完整可读。

## 12. ADR-008：LEAN 只提供架构语言与交叉验证思想

**学习**：

- Universe → Alpha/Insight → Portfolio Construction → Risk → Execution 的职责分离；
- Time Frontier 阻止算法访问未来数据；
- 费用、滑点、成交、结算和券商规则属于 Reality Model，而不是策略解释的一部分。[LEAN Algorithm Engine](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/algorithm-engine)

**不采用**：不把 C#/.NET 事件引擎嵌入 V4 主路径，不复制其券商/实时运行结构，也不假定默认 Reality Model 等于中国市场现实。只有未来高风险策略需要第二种独立事件引擎验证时，才单独评估 LEAN。

## 13. ADR-009：借用 target_weights 思想，但组合契约归项目所有

FinRL-X 将策略输出统一为目标权重，能隔离选股、组合和执行。[FinRL-X](https://github.com/AI4Finance-Foundation/FinRL-Trading)

**决策**：V4 的量化层不输出“买入 100 股”的最终指令，而输出带时间、现金和约束语义的目标组合产物。Financial Kernel 使用真实账户、整手、价格与 Mandate 联合求解可行方案；不能简单先生成权重、再由“风险层”事后裁剪。

FinRL-X 的数据源、回测器、Alpaca 执行与宣传结果不进入 V4 依赖。

## 14. ADR-010：RD-Agent(Q) 只作为后期受限研究实验室

**学习**：假设 → 代码 → 无 LLM 的确定性验证 → 反馈、失败保留和因子去重。[RD-Agent](https://github.com/microsoft/RD-Agent)

**拒绝**：V4.0 不引入 RD-Agent 常驻循环，不允许 Agent 自动扩张实验、不自动晋级策略，也不让生成代码访问生产数据库或凭据。

在确定性基线、实验登记、多重试验预算和 Sandbox 已稳定后，才可以实现一个更窄的 Codex Research Loop。每个假设族必须预注册最大试验次数；测试集一旦被模型或 Agent 看过，就不再是未见样本。

## 15. ADR-011：不自研通用 Backtester，但保留独立参考计算

**决策**：V4 不建设第二套完整通用回测平台。为验证 Qlib Adapter 和 A 股语义，只实现一个小型、可人工核对的参考计算器，覆盖 golden cases 和一两个透明策略。

验证顺序必须是：

```text
官方事实与手工期望结果
→ Raw-to-Canonical 转换
→ 小型参考计算器
→ Qlib 对照
→ 扩大样本
```

双引擎相同只能证明实现一致；只有先通过独立官方事实夹具，才能增加正确性的可信度。

## 16. ADR-012：人工行动复用 Decision/Execution，不创建第二套投资真相

**决策**：量化研究结果不能直接成为操作建议。Primary Codex 审查后，仍通过 V3 Cognitive Ledger 发布冻结的 Decision Revision；结构化 `ManualActionSpec` 作为该 Revision 的内容寻址子产物。现有 Execution 生命周期扩展为人工协作协议，并引用精确 `decision_revision_id + manual_action_spec_hash`；只有用户确认的成交关联 confirmed Ledger。

`ManualActionSpec` 至少包含：

- 来源 Strategy/Experiment/Dataset 与 Decision Revision；
- 当前 Portfolio/Market Snapshot 和 Calculation IDs；
- 操作、数量/整手、目标区间、优先级与替代方案；
- `valid_from`、`valid_until`、价格/现金/持仓失效条件；
- supersedes 链、用户确认状态和偏离原因。

执行前事实变化时必须重新计算；系统不得假定用户按建议价格、数量或时间成交。

## 17. ADR-013：影子评价以组合为主、候选为辅

旧 PRD 以单只候选的 MFE/MAE 为中心，无法证明组合可投资性。V4 改为三种明确分离的 Shadow：

| 类型 | 验证对象 | 权威输出 |
|---|---|---|
| Strategy Shadow Book | StrategyVersion 的前向目标组合、成交假设与净值 | 组合收益、回撤、换手、成本、容量和基准差 |
| Decision/Execution Shadow | Decision 与用户人工执行之间的选择、延迟和偏离 | 决策过程、实际成交与机会成本 |
| Attention Shadow Mode | 本应通知或静默的事件 | 误报、漏报和通知负担 |

候选的 selected/rejected/deferred 仍可用于覆盖率和假阴性分析，但不作为策略有效性的主要证据。

## 18. ADR-014：结果契约先于“专业级”称号

V4 的结果契约同时比较：

- 简单可投资基准与无 AI 基线；
- 扣除成本后的净收益期望、回撤和尾部风险；
- 换手、容量、稳定性和过期建议率；
- 错误建议、漏掉机会和主动 abstain 的质量；
- 用户研究时间、解释清晰度和人工执行摩擦。

如果复杂系统在足够的前向样本后不能优于简单规则或现有人工流程，应停用相应策略或研究自动化，而不是继续增加模型、数据和 Token。

## 19. 依赖和许可治理

每个运行依赖必须记录：精确版本、许可证、上游仓库、锁文件哈希、已知补丁、数据许可和升级验收结果。升级 Qlib 或模型库必须重跑 Adapter 合约、golden cases、基准回测和一个冻结 ExperimentBundle。

第三方代码许可不代表第三方数据可以复制、提交 Git 或长期保存。数据留存和备份策略必须遵守来源条款。

## 20. 架构完整性检查

未来设计评审必须能够清楚回答：

1. 这个数据在当时何时可知，哪一版被实验使用？
2. 同一输入、代码和环境能否产生同一产物哈希？
3. Qlib、MLflow 或 Agent 被移除后，哪些项目事实仍然完整？
4. 为什么这个结果足以进入 Shadow，为什么仍不足以形成真实 Decision？
5. 用户最后实际做了什么，系统是否把建议、意向和成交混淆？

答不清其中任何一项，说明当前实现仍是机械拼接或不可审计流水线，不满足 V4。
