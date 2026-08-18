# Investment Companion V5：个人投资经营系统

状态：工程实现候选；尚未迁移生产，尚未宣称策略有效
日期：2026-08-18
V4 冻结点：`v4.0.0-engineering-baseline`
关联文档：[架构决策](V5-ARCHITECTURE-DECISIONS.md)、[实施计划](V5-IMPLEMENTATION-PLAN.md)、[验收契约](V5-ACCEPTANCE.md)、[用户手册](V5-USER-GUIDE.md)、[运维手册](V5-OPERATIONS.md)

## 1. 一句话定义

V5 是一套 **Codex 驱动、确定性证据支撑、以个人投资经营计划和决策闭环为中心、飞书极简协作、人工执行、能用真实结果持续自我否证的个人投资系统**。

V4 建成了专业研究和人工决策的后端能力；V5 解决“用户每天到底怎么用、研究如何汇聚成少数决策、系统是否真的产生净价值”三个产品问题。

V5 不承诺高胜率、Alpha 或持续盈利。它追求的是：在个人目标和风险边界内，提高证据质量、减少随意行动、记录全部结果，并及时停止无效方法。

## 2. 为什么不能继续堆功能

V4 的 Data、Research、Shadow、Gate、Job 和 ManualAction 都是必要的专业基础，但它们不是用户的操作界面。若直接把这些对象暴露给用户，会出现三个问题：

1. 用户看见大量内部状态，却不知道今天该做什么；
2. 每个主动任务各自输出，缺少统一投资目标和机会漏斗；
3. 回测、研究数量和 Token 消耗很容易冒充实际投资价值。

V5 因此不再增加一个“AI 选股器”，而是在既有事实域上增加一个很薄的投资经营层，把所有后台能力组织成一条可关闭的闭环。

## 3. 根源哲学与不可破坏的不变量

- Primary Codex 是唯一语义指挥、最终投资判断和用户沟通主体；
- Companion 保存事实、状态、引用和审计，不替 Codex 下判断；
- confirmed Ledger 是真实现金、持仓和成交的唯一变化来源；
- Investor、Mandate、Attention、Thesis、Decision、Review 继续使用不可变版本；
- 精确数字来自 Financial/Quant Kernel 的 Calculation，不由模型心算；
- 飞书是协作面，不是事实库；用户始终人工执行；
- 外部网页、API、附件和 Agent 输出都是待核验数据，不是指令；
- “没有达到行动门槛”是正常且重要的输出；
- 代码通过不等于数据合格，回测通过不等于策略有效，建议被接受不等于盈利。

## 4. 用户只需要看到四种产品输出

| 输出 | 默认频率 | 用户要回答的问题 | 内部复杂度是否暴露 |
|---|---|---|---|
| 今日入口 | 按需/每日异常 | 今天有必须处理的事吗？ | 否 |
| 周度投资委员会简报 | 每周 | 本周研究推进、淘汰和风险是什么？ | 只给必要证据引用 |
| 月度结果记分卡 | 每月 | 系统是否改善了结果和过程？ | 给 Calculation 血缘，不给虚构数字 |
| 行动卡 | 罕见、事件驱动 | 做什么、不做什么、何时失效？ | 给精确约束和反证 |

Gate、Worker、Snapshot、JobStep、Adapter Probe 是后台诊断对象。除非失败影响判断，用户无需学习这些名词。

## 5. 完整投资经营闭环

```text
用户确认 Investor / Mandate / Attention
                   │
                   ▼
       InvestmentProgram（经营目标与规则）
                   │
                   ▼
      Opportunity Funnel（机会证据漏斗）
 observed → researching → qualified → actionable
                   │
       Research / Quant / Shadow / Critic
                   │
                   ▼
        当前、已签发、有期限的 Decision
                   │
                   ▼
            DecisionQueue / ActionCard
                   │
              飞书呈现与用户选择
                   │
        ┌──────────┴──────────┐
        ▼                     ▼
    拒绝/等待            接受后人工下单
                              │
                       用户报告真实成交
                              │
                       confirmed Ledger
                              │
                  Review / Monthly Scorecard
                              │
             修订、暂停或淘汰 Program/Strategy
```

系统的学习发生在最后一段：只有真实成交、真实未行动、真实时间流逝和可重放计算才能改变对方法价值的判断。

## 6. V5 新对象及真相边界

### 6.1 InvestmentProgram

一份经用户确认的投资经营契约，引用当前 Investor、Mandate、Attention 和账户，固定：

- 目标和成功标准；
- 基准和结果口径；
- 风险预算、投资范围和时间跨度；
- 日/周/月节奏；
- 必须暂停或停止的条件。

它不复制个人事实和账户事实。任一引用 Context 变化时，旧 Program 不能静默继续使用，必须重审或修订。系统同一时刻只允许一份 active Program，避免多个隐含目标互相争夺资金。

### 6.2 Opportunity

Opportunity 是研究工作流，不是推荐名单。阶段表达证据资格，而不是 LLM 的主观概率：

| 阶段 | 含义 | 最低要求 |
|---|---|---|
| observed | 出现了值得界定的问题 | 至少一个不可变来源 |
| researching | Primary 接受了有边界的研究委托 | 冻结研究证据和理由 |
| qualified | 值得进入组合/决策评价 | 活跃 Thesis 或合格 Strategy、至少两个独立来源、反证条件 |
| actionable | 允许呈现给用户 | 当前数据、无重大未知项、当前已签发且未过期的 Decision |

任何阶段都可以 rejected、expired 或 closed。失败和淘汰必须保留，防止系统只展示幸存者。

### 6.3 DecisionQueue

DecisionQueue 是“等待用户判断的投影”，不是新的投资真相，也不是 Execution。它只能引用 actionable Opportunity 和同一个当前 Decision Revision。

- 有 ManualActionSpec 时，行动卡实时重验价格、账本、Mandate 和有效期；
- 用户接受 Queue 只记录选择，不自动创建成交；
- accepted 在有效期内继续显示为“等待手工执行或反馈”，直到关闭或过期；
- “稍后处理”必须保存明确的恢复时间，到时自动回到 ready；
- 真正下单仍由用户完成；
- 用户报告成交后，才创建并确认 Ledger Entry；
- 过期 Queue 确定性失效，不能靠模型继续解释为有效。

### 6.4 OperatingBrief

日、周、月简报是结构化呈现记录，只引用 Program、Queue、Decision、Calculation 和证据。简报可以被新版本 supersede，不能覆盖历史。

`no_action` 只有在当前不存在 ready/presented/snoozed/accepted Queue 时才能发布，且只在本 Program Revision 的 `next_check_at` 之前有效。它表示“本轮检查未产生达到门槛的行动”，不表示“市场没有机会”或“系统已经完整扫描全市场”。

### 6.5 ProgramScorecard

Scorecard 不接受模型填写收益率或净值。每个数值必须包含：

- `calculation_id`；
- `outputs.*` 路径；
- Financial Kernel 实际解析出的值；
- Calculation 的 as-of 与 engine version。

比较只能引用已解析的指标名。资料不足时必须发布 `insufficient_evidence`，不能补写一个看似专业的数字。

`v5_program_metrics_calculate` 会以零模型 Token 计算本期 Opportunity、DecisionQueue 和 Brief 的真实流量与转化；所有源记录 ID 一并冻结进 Calculation。组合收益、基准收益、用户时间、Token/数据成本若尚无 Program 级可重放输入，会明确标成 `insufficient_evidence`，不会用全局计数或模型估算代替。

## 7. 今日入口的确定性状态

`v5_today` 只返回四种状态：

- `setup_required`：缺 Context 或 active Program；
- `action`：存在仍有效的 DecisionQueue；
- `no_action`：当前 Program Revision 的最新日简报经过闭环、明确无行动且尚未到下一检查时间；
- `review_required`：没有行动，但本期证据检查还没有形成结论。

这解决了“系统是不是随便搜几个股票交差”的担忧：没有合格证据链时，入口不会生成股票，只会要求补研究或明确证据不足。

## 8. 主动任务如何汇聚

现有 Schedule、Watch、Event 和 Run 全部保留。它们仍负责收集信息、触发调查、维护事实和定期复盘，但不再各自争抢用户注意力。

```text
旧主动任务输出
  ├─ 低价值/无变化 → 静默、留审计
  ├─ 新线索 → observed Opportunity
  ├─ 研究材料 → 推进或淘汰 Opportunity
  ├─ 合格 Decision → DecisionQueue
  └─ 周/月到期 → 汇总 Brief / Scorecard
```

原有任务无需重置或重新注册。只有在真实使用中发现重复、没有 Program 归属或长期无价值时，才逐项修订或归档。

## 9. 确定性 Job 与 Codex 的分工

- Adapter、Snapshot、回测、组合计算和 Shadow 默认零模型 Token；
- Codex 只在需要定义问题、核验原始证据、处理反证、联系个人约束或与用户沟通时工作；
- Quant 输出 target/evidence，不直接输出个人买卖指令；
- Codex 不能绕过 Gate、Calculation、Mandate 或 Decision 生命周期；
- Subagent 是短命专业评审者，输出材料，不拥有持久化和发布权。

V5 不需要 7×24 小时运行模型。日频市场只需定时数据作业、事件唤醒和按需 Codex；休市和无材料变化时可以完全静默。

## 10. 唤醒协议

V4 的固定 cc-connect cron 只能“叫醒 Codex”，没有把真正待处理对象交给新会话。V5 改为两步握手：

```text
Dispatcher 把 Outbox 标为 sending → 触发静态 cron
新 Codex 会话调用 wake_claim → 领取准确 envelope + lease
处理 scheduled_run / research_ready / operating_brief
先完成父 Run 或事件 → wake_complete
```

cron 成功只代表“已发出唤醒信号”，不代表工作完成。未调用 `wake_complete` 的信封会在租约超时后恢复重试。旧 V3 Run payload 和 V4 research-ready payload 会被归一化，保证升级兼容。

## 11. Gate 不再形成试用死循环

V5 把“允许受控试运行”和“允许正式发布”拆开：

- Canary 在 G0 后以受限范围产生 G1 数据资格证据；
- Decision Support Beta 在 G0–G4、用户明确 opt-in 和到期时间下产生 G5 真实飞书/人工使用证据；
- Full Decision Support 仍要求 G5；
- G6 评估策略和系统的前向净价值，不阻止受控证据生成，也不会因天数到达自动变成 Go。

中文含义：Gate 的 `no_go` 是“现有证据不允许扩大使用范围”，不是“功能永远不能试”。试用有明确范围、用户批准、到期时间和停止条件。

## 12. 兼容性与迁移

- Schema 5 是对 Schema 3/4 的追加式显式迁移；普通启动不会自动升级旧库；
- V3 Schedule/Event/Run/Watch/Case、Ledger、Context、Cognition 原样保留；
- V4 Data/Research/Shadow/ManualAction 原样保留；
- 新表只保存经营协调对象和引用；
- 所有新 Feature 默认关闭；
- 插件升级和 cc-connect 静态提示更新只在生产变更窗口进行；
- 工作项目不重置，用户只需在升级完成后 `/new` 建立一个加载新插件的会话。

## 13. 明确非目标

- 不连接券商、不保管券商凭据、不自动下单；
- 不承诺高胜率、盈利或特定收益率；
- 不用 AI 文本评分替代数据资格和策略检验；
- 不每天强行产生候选；
- 不建设 Kafka、Kubernetes、通用 DAG 或常驻多 Agent 群；
- 不因为开源项目有某模块就机械接入；
- 不把 Token、工具数、任务数或报告页数当成系统价值。

## 14. V5 真正成功的判据

工程成功：迁移可回滚、对象边界正确、旧任务兼容、唤醒不丢工作、数值可重放、人工执行边界不可绕过。

产品成功：用户能通过一句“今天该做什么”进入系统；每周只看到少量有意义信息；行动卡可理解、可拒绝、会过期；真实成交能顺畅回到账本。

投资价值成功：在足够长的真实前向周期中，相对明确定义的基准和原有人工流程，扣除成本、风险、机会成本、时间与 Token 后仍有正的增量价值。若不成立，应缩减、修订或停止，而不是继续堆 AI。
