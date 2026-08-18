# Investment Companion V4：验收与发布契约

状态：V4 强制验收基线 v1.0；工程实现完成，生产 Gate 未通过
日期：2026-08-18
适用设计：[V4-DESIGN.md](V4-DESIGN.md)
实施顺序：[V4-IMPLEMENTATION-PLAN.md](V4-IMPLEMENTATION-PLAN.md)

## 0. 当前验收记录（2026-08-18）

当前不存在任何 production scope 的 `Go` assessment，所有 V4 feature flags 默认关闭，生产数据库未从 Schema 3 升级。下面的“工程通过”来自隔离 Fixture 或数据库副本，不能替代生产 Gate：

| Gate | 当前结论 | 已有证据 | 尚缺证据 |
|---|---|---|---|
| G0 | Pending / fail closed | 有序 Schema 3→4、重复迁移、失败回滚、备份哈希、V3 恢复与回归测试 | 干净发布 commit 的生产评审与用户 approval ref |
| G1 | **No-Go** | Tushare 白名单 Adapter、状态分类、Raw/Canonical/canary、官方文档调查；隔离临时库 11 端点 smoke healthy，单日 `daily` 5339 行完整 canary ready | 可持久复核的生产 probe、10–20 交易日 canary、官方 golden corpus、许可审查 |
| G2 | **No-Go** | NativeQuantRuntime、Decimal/哈希、下一交易日、现实规则、独立参考计算 Fixture | 基于合格真实 Snapshot 的 official golden 逐日对齐 |
| G3 | Engineering pass | allowlist、资源/网络隔离、租约、原子 Job/Step/Experiment、CAS/Snapshot 篡改失败关闭测试 | 前置 Gate 与生产故障演练/assessment |
| G4 | Fixture only | 精确预注册配置、物理 split、系统派生 walk-forward、预算、holdout access、基准、生产最少 252 观察日规则和 bundle validator | 合格数据上的真实研究、敏感性/容量报告与反方评审 |
| G5 | Fixture only | 未调参前向信号、信号/执行双快照 Shadow、真实 Ledger 隔离、个人组合联合求解、Agent provenance、Decision/ManualAction/Execution 端到端测试 | 真实前向 Shadow、飞书通道 dry-run、用户 beta opt-in 与发布评审；充分决策样本属于 G6 |
| G6 | **No-Go** | 样本门已编码 | 至少 90 个真实日历日、12 次 rebalance、5 个 Decision、2 种 regime，并证明用户价值 |

完整数据结论见 [DATA-QUALIFICATION-v1.md](DATA-QUALIFICATION-v1.md)，运行与迁移边界见 [V4-OPERATIONS.md](V4-OPERATIONS.md)。G6 是时间与真实使用证据，不能通过合成测试、补写历史 Shadow 或 Codex 判断提前完成。

当前自动化基线为 **64 tests + 8 subtests**。该数字只说明工程回归范围，不是 Gate 结论。

## 1. 验收原则

V4 必须分别证明三件事：

1. **数据与运行可信**：事实、时间、修订、产物和恢复正确；
2. **研究方法可信**：没有明显穿越、选择偏差和试验作弊，并相对简单基准有可解释增量；
3. **个人决策有价值**：系统减少研究摩擦，形成更清晰的 Decision，且不越过用户约束和执行权。

三者不能互相替代。代码通过不证明研究有效；回测漂亮不证明数据正确；运行 90 天不证明 Alpha；用户偶然盈利不证明过程专业。

## 2. 发布 Gate

| Gate | 证明内容 | 未通过时 |
|---|---|---|
| G0 V3 Baseline | 当前事实、迁移、Run、备份和回归可靠 | 禁止 Schema 4 |
| G1 Data Qualification | 允许研究的数据族与 PIT 边界已证明 | 缩小策略或更换/购买数据 |
| G2 QuantRuntime | Golden cases、参考计算与候选内核对齐 | Qlib No-Go 或停止量化实现 |
| G3 Deterministic Platform | Job、产物、Snapshot 与实验可恢复重放 | 只能继续离线 Spike |
| G4 Research Method | 未见样本、成本、风险、试验治理合格 | 策略拒绝/修订，不进入 Shadow |
| G5 Shadow & Decision | 组合 Shadow、个人 Gate、飞书与人工执行无串写 | 不得输出用户行动建议 |
| G6 User Value | 足够样本下优于基准和现有人工流程 | 停用策略/Agent 或保持研究工具状态 |

禁止通过项目版本号、管理员 override 或 Codex 文字解释跳过 Gate。

## 3. V3 回归不变量

每个 V4 阶段都必须继续满足：

- 未确认 Ledger Entry 不改变 Portfolio；
- Reversal 不删除或覆盖原事实；
- Simulation、Backtest 和 Shadow 不产生真实 Ledger Entry；
- Decision 发布引用冻结 Context/Portfolio/Thesis/Calculation；
- Execution 的成交状态只能关联 confirmed Ledger；
- Attention 反馈不自动修改 Policy；
- V2 Schedule/Watch/Event/Case/Patrol/Outbox 与崩溃恢复无回归；
- Primary Codex 仍是唯一正式认知发布与飞书沟通主体。

任一回归失败即阻止 V4 合并和发布。

## 4. G0：基线、迁移与 Run

### 4.1 基线

- 实施前记录 Git、Schema、Doctor、Agent、Schedule/Run、备份和恢复状态；
- PROJECT-STATUS 与工具读取的当前业务准备度一致；
- 用户私人数据不进入测试 Fixture、Git diff 或日志。

### 4.2 Ordered Migration

- 从 V3.0.4/Schema 3 复制库升级成功；
- 重复运行 migration 无额外副作用；
- 每个 migration 有唯一 ID、checksum 和应用时间；
- 在每一步强制终止后可恢复或从备份回滚；
- 新程序拒绝未知更高 Schema，不擅自覆盖版本号。

### 4.3 Run 生命周期

- 定时、手工和恢复 Run 的 `attempt/started_at/finished_at` 正确；
- claim、lease 过期、重试和 complete 贯穿实际 cc-connect 路径；
- 一个 Run 只能有一个可见最终状态；
- 重复 Tick 不创建重复父 Run 或 Outbox；
- 取消和失败不会被显示为成功或“无变化”。

## 5. G1：数据资格与来源

### 5.1 Capability

每个生产 Stream 必须有近期实测的：连接器、账号权限、频率、历史、延迟、行数、字段、修订、许可和失败语义。`connector_missing`、`unauthorized`、`invalid_request`、`rate_limited`、`stale`、`partial`、`empty_valid` 和 `failed` 有独立 Fixture。

5000 积分不得被解释为拥有所有 Tushare 能力；未 probe 的端点状态为 `unknown`。

### 5.2 来源权威

- 结构化字段可回到 provider endpoint、请求范围和 Raw Object；
- 公告结论可回到交易所/发行人原文、发布时间和内容哈希；
- Web、Tavily、新闻或 Agent 摘要不能成为最终公告权威；
- 材料性冲突保留两种口径和处理理由，不静默选值；
- 数据许可与留存规则有记录并被备份/导出策略遵守。

### 5.3 Golden Corpus

至少覆盖：代码/简称变化、上市/退市、历史成分、停复牌、ST/板块、涨跌停、公司行动、复权、财报修订、交易日和合法空集。每个案例有原始证据、知识截止点和人工期望。

Raw → Identity → Canonical → Snapshot 的结果必须与人工期望一致。双引擎一致但 golden case 失败视为失败。

## 6. G1：PIT 与 Dataset Snapshot

### 6.1 PIT 字段

可修订事实必须保存：`effective_at/effective_to`、`first_known_at`、`ingested_at`、`revision_id/supersedes`、raw hash、parser version 和质量。修订追加，旧版本可查询且不可覆盖。

### 6.2 Knowledge Cutoff

- 任意历史日期查询只返回 `first_known_at <= knowledge_cutoff` 的版本；
- 人为注入未来修订不会改变更早 Snapshot；
- 当前股票池、当前 ST 状态或未来公司行动不能进入历史 Universe/特征；
- 财务 PIT 无法证明时，系统阻止相应 StrategySpec；
- 时区、收盘后可得性和信号/交易延迟有明确测试。

### 6.3 Snapshot 完整性

每个 Snapshot Manifest 固定：分区/行版本哈希、完整 denominator、Universe、exclusions、交易日历、公司行动、质量和代码版本。相同输入生成相同 Snapshot ID；任一输入变化生成新 ID。

`ready` Snapshot 不得包含 blocked partition。读取时复核 Manifest 和对象哈希；修改、缺失或路径逃逸立即失败关闭。

## 7. G2：QuantRuntime / Qlib

### 7.1 隔离

- Native stdlib 内核须记录精确 Python/OS 且零第三方依赖；任何第三方运行时须有可从零安装的独立锁版本环境；
- DB-aware 编排与纯计算边界分离；内核计算区间无 Companion DB、Ledger、飞书、生产凭据或外部 I/O 权限；
- 业务代码不导入 Qlib 类型；
- 输入输出通过项目契约，运行目录可整体删除重建；
- `health()` 能报告版本、依赖和必要数据状态。

### 7.2 语义对齐

在 Golden Corpus 和简单策略上逐日核对：Universe、eligibility、feature availability、target weights、交易意图、未成交、现金、持仓、费用和 NAV。

离散对象必须完全一致；浮点指标只允许在 Phase 2 预注册的数值容差内差异。任何差异都要有 Fixture 和解释，不能用“框架实现不同”关闭问题。

### 7.3 Go / No-Go

Qlib 只有在显著减少研究代码、无需重度 fork、通过关键 A 股语义且升级可维护时通过。否则采用相同契约下的窄 NativeQuantRuntime；不能为了保留已投入成本降低标准。

## 8. G3：Typed Job 与产物原子性

### 8.1 Job 契约

- 只执行 allowlist handler，不执行数据库中的任意命令；
- 父 Run、JobRun 和 JobStep 的完成/失败/取消传播已定义；
- 输入只引用不可变 DataObject/Snapshot；
- 输出只通过 Manifest 发布；
- 每步记录 handler/code/env、输入输出、耗时、资源、attempt 和错误；
- 重试使用稳定幂等键，不重复推进 cursor、发布产物或生成事件。

### 8.2 故障注入

在以下位置强制终止并重启：

- Fetch 前/后；
- Raw 写入后、Normalize 前；
- Canonical 写入中；
- Manifest 校验前/后；
- DB 注册前/后；
- Event/Outbox 投递前/后。

恢复后必须达到一个明确状态：继续、重试、blocked 或 terminal failure；不得出现 DB 指向半份文件、游标越过未发布事实、重复 Event 或孤立临时文件被当成正式数据。

### 8.3 资源与零 Token

Data、Dataset、Quant 和 Evaluation Job 默认不调用模型；CPU、内存、wall time、输入/输出字节预算超限时终止并记录。日频增量的 SLO 在 Phase 4 基准测试后冻结，初始目标为源端可用后 30 分钟内发布可消费 Snapshot。

## 9. G4：研究方法与实验治理

### 9.1 预注册

每个 Hypothesis/StrategyVersion 在运行前冻结：经济逻辑、Universe、数据、特征、标签、时间对齐、组合、成本、基准、split、主要指标、通过/失败阈值、最大试验数和停止条件。

### 9.2 可重放

同一 Dataset Snapshot、代码、环境、参数和种子重复运行，确定性产物哈希相同；允许非确定性算法时必须说明来源、固定容差并保存随机状态。任意 ExperimentBundle 可在空临时目录重建。

### 9.3 全部试验留痕

- 失败、拒绝、异常和空结果均登记；
- 测试集每次访问可查询；
- 修改原因与父 StrategyVersion 明确；
- 超出试验预算被系统阻止；
- Agent 看过的 holdout 不再标记为未见样本；
- validation 与 final holdout 均通过同一份冻结 pass/fail，development 结果不能抵消任一后续阶段失败；
- 不允许只保留表现最好的 seed、窗口或参数。

### 9.4 评价最低集合

- point-in-time 与 survivorship 测试；
- walk-forward/rolling OOS 与未见最终样本；
- 简单可投资基准和无 AI 基线；
- 成本后净收益、回撤、尾部、换手和容量；
- 参数、延迟、成本、缺失和市场状态敏感性；
- 完整 denominator、所有 scores/ranks/exclusions；
- 明确失败条件和反方解释。

胜率高但净期望差、尾部损失大或成本后无增量必须失败。

## 10. G5：组合与 A 股 Reality Model

- target_weights 产物包含 as-of、现金、Universe、Strategy/Snapshot 和完整权重和；
- 组合求解同时处理信号、风险、成本、换手、现金、流动性、整手和 Mandate；
- 无可行解返回 `infeasible` 与冲突，不静默删资产或缩权；
- T+1、停牌、涨跌停、100 股、费用/税、信号延迟、分红和退市有 Fixture；
- 费用、滑点和冲击假设版本化，可做敏感性测试；
- 回测、Shadow 和 Decision 使用同一 RealitySpec 接口，但明确人工实际成交不等于模拟成交。

## 11. G5：Shadow

### 11.1 隔离

Shadow Book、Fill、Position 和 NAV 与真实 Account/Ledger 使用不同对象、工具和权限。任何 Shadow API 都不能创建 confirmed Ledger；数据库层和应用层都要有测试。

### 11.2 前向完整性

每次 rebalance 分别冻结信号 Snapshot 与执行 Snapshot、完整 denominator、target weights、RealitySpec、可成交结果、费用和失败。信号必须在执行日开盘前完成；执行日数据只能进入执行快照和后续评价，不能改写当时信号。生产模式禁止两个快照复用和历史回填。

### 11.3 样本门

每个策略在进入 Shadow 前预注册最小时间、独立信号/再平衡数、市场状态覆盖和人工决策样本。90 天到期但样本不足时状态仍为 `insufficient_evidence`。

### 11.4 结果比较

至少比较：简单基准、确定性策略、模型策略、Codex rerank/否决、issued Decision 与实际用户执行。报告净收益、回撤、换手、成本、过期率、abstain、数据故障和执行偏离，不能把所有变化归因于 AI。

## 12. G5：Decision、ManualActionSpec 与 Execution

### 12.1 Decision 发布

缺少任一项必须拒绝发布：confirmed Investor/Mandate、Portfolio/Market Snapshot、Thesis/Evidence Cutoff、Strategy、最终 holdout、当前 forward signal、Dataset/target、组合联合求解、单笔 Calculation、不行动方案、失效条件和带不可变 provenance 的反证审查。

### 12.2 ManualActionSpec

必须包含：资产身份、方向、数量/整手或目标、报价时间、有效期、价格区间、优先级、替代条件、精确 Decision Revision 与来源引用、重新校验条件、supersedes 和通知幂等键。

数据陈旧、价格越界、现金/持仓变化、Mandate 修订或上游 Decision 被 supersede 时，旧 Spec 自动变为不可执行；不能只发一条补充文字。

### 12.3 人工执行

- `presented` 不等于 accepted；
- accepted 不等于 ordered；
- ordered 不等于 filled；
- 每个 Execution 引用精确 issued Decision Revision 和 ManualActionSpec hash，而不是可变 current pointer；
- partially_filled/filled 必须关联用户确认的 Ledger Entry；
- 拒绝、过期、取消、superseded 和 deviated 均保留理由；
- 用户重复回复或飞书重投不产生重复 Execution/Ledger。

## 13. G5：Codex、Agent 与 Token 治理

### 13.1 权限

- Agent 不能修改 Raw/PIT、Experiment 指标、Promotion、Mandate、Decision、Execution 或 Ledger；
- 当前 Custom Agent 必须是 `read-only` 沙箱，并以完整同身份配置显式禁用 `investmentCompanion` MCP；`agent-check` 与真实派遣 smoke 必须证明角色仍可加载；
- 生成代码不能访问生产 DB、凭据、飞书或未经允许的网络；
- Primary Codex 不能绕过确定性 Gate 或伪造 Calculation；
- 所有模型输出作为 proposal/artifact，必须经过 Schema 校验和项目规则。

### 13.2 Provenance

材料性模型调用记录 role、model、prompt/template、不可变研究/个人/Calculation 输入引用、时间、Token、输出 Manifest/hash 和采用/拒绝理由。正式 Decision 的 critic 只能引用已校验 invocation；Prompt 或模型变化若影响研究逻辑，生成新方法版本。

### 13.3 Token 价值

按假设族统计 Token、实验、有效新假设、研究通过率、相对基准增量和人工时间。无明显增量时，自动实验保持 disabled；不得以“已经花了 Token”为继续理由。

## 14. G5：飞书与 Attention

- 数据流水线成功默认静默；
- 用户不收到未经 Primary Codex 审阅的 Scanner 排名；
- 每条主动消息经过当前 Attention Policy，有 notification key 和理由；
- 消息清楚标记研究、Shadow、Decision、待确认和已成交的差别；
- 候选/Decision 显示数据截至时间、有效期、主要反证和下一操作；
- 同一事件或 Decision 重投在用户侧只展示一次；
- 用户可自然语言查询为什么运行、为什么联系、引用什么数据、如何暂停和如何拒绝。

## 15. 安全、许可、备份与恢复

- Token、账号、账单、真实持仓和许可受限原始数据不进入 Git、Prompt 或普通日志；
- 外部内容始终按不可信数据处理，不能触发命令或改变权限；
- DataObject 路径限制在配置 root，拒绝 `..`、绝对路径和符号链接逃逸；
- 第三方依赖记录精确版本、许可证、来源、补丁和锁文件 hash；
- 关键备份包含 Companion DB、Raw、Snapshot Manifest、Cognitive Markdown 与必要配置；
- 在空目录完成一次独立恢复，证明 Derived/Qlib cache 可以重建；
- 恢复不产生重复 Run、Experiment、Shadow Fill、Decision 消息或 Ledger Entry。

## 16. G6：用户价值与策略资格

单个 StrategyVersion 只有在预注册样本充足且同时满足以下要求时，才可成为 Decision 的证据之一：

- 成本后相对简单基准存在稳定、可解释的增量；
- 回撤、尾部、换手、容量和过期率在 Mandate 可接受范围；
- 结果不是由单一时期、行业、少数股票或少数试验驱动；
- 数据故障、abstain 和反例均已解释；
- 用户能理解为何出现、何时失效和为什么可能不行动。

V4 系统层还需证明：研究时间或重复劳动下降、通知负担可接受、真实/影子/建议/成交没有混淆、用户认为解释有助于决策。

若策略无增量，可以保留 V4 的数据、研究和审计能力，但该策略必须 reject/retire；不得用新模型覆盖失败结论。

## 17. 完成定义

### 17.1 `v4-spike`

G0–G2 通过，已经知道哪些数据和 QuantRuntime 可用；不能向用户产生操作建议。

### 17.2 `v4-alpha`

G0–G3 通过，数据、Job、Snapshot 和 Experiment 可重放；仅内部/file-only。

### 17.3 `v4-beta`

G0–G5 工程与隔离验收通过，组合 Shadow 和人工闭环可以明确 opt-in 试用；仍不宣称策略有效。

### 17.4 `v4.0.0`

所有工程 Gate、V3 回归、备份恢复和至少一个完整前向策略审查通过；文档与真实状态一致。发布只表示专业研究骨架可用。

### 17.5 `strategy-eligible`

某个 StrategyVersion 独立满足 G4、G5、G6 的预注册方法与样本门，才能作为正式 Decision 的证据。系统发布与策略资格永远分开。

## 18. 验收证据格式

每次 Gate 评审必须留下：

```text
Gate / 日期 / 代码版本 / Schema
输入 Snapshot 与 Fixture
执行命令与环境
通过项 / 失败项 / 未知项
产物与日志哈希
反方证据与差异解释
Go / Conditional Go / No-Go
批准者与下一复核条件
```

“测试通过”“效果不错”“运行了 90 天”都不是独立可接受的验收记录。
