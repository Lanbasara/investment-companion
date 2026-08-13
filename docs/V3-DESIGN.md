# Investment Companion V3：可信长期投资伴侣

状态：设计草案，等待评审后冻结；尚未实现。
日期：2026-08-13
前置版本：V2.1 已实现可靠主动调查、短命 Agent、运行恢复与干净工作区初始化。

## 1. 一句话定义

V3 将 V2 的“可靠主动调查底座”升级为一个能够长期维护真实投资事实、精确计算组合影响、版本化保存投资认知，并按个人注意力策略主动工作的 Codex-native 投资伴侣。

```text
V2：世界发生了什么，是否值得调查？
V3：这对我真实拥有的资产、目标、既有判断和下一步意味着什么？
```

V3 仍以 Primary Investment Codex 为唯一语义指挥者；新增确定性内核不是另一个 Agent Harness，也不生成最终投资意见。

## 2. 设计基本法

### 2.1 投资系统生长在 Codex 内

- 飞书与 cc-connect 仍是主要交互面。
- Primary Codex 负责理解、假设、调查组织、综合判断、解释和沟通。
- Companion 保存精确状态、约束、版本、文件句柄和审计记录。
- 专家 Agent 仍是按 Brief 派遣的短命工作者，不拥有长期权力。

### 2.2 认知与计算分离

LLM 不能承担守恒、账本重建和材料性数值计算：

| Primary Codex | Financial Kernel |
|---|---|
| 理解自然语言与歧义 | 校验身份、单位、币种和时间 |
| 提出假设与情景 | 从流水重建持仓与现金 |
| 决定比较哪些方案 | 精确计算收益、暴露和调仓影响 |
| 解释机会成本与不确定性 | 执行 Mandate 硬约束 |
| 形成最终判断 | 返回可重放 Calculation Record |

精确错误必须失败，不得伪精确；缺少确认数据时返回 `insufficient_facts`，而不是让 LLM 补猜。

### 2.3 主动性与个人偏好分离

事件只描述世界变化，Codex 判断意义，Attention Policy 决定执行边界。偏好可配置、可版本化、可试用、可回滚；系统可以提出调整建议，但不能因用户沉默而擅自改变策略。

### 2.4 事实不可覆盖，认知可演化但历史不可改写

- 金融流水只追加；错误使用冲销或更正记录，不原地修改历史。
- Thesis 每次发布形成完整不可变 Revision。
- Decision 发布时冻结当时上下文，之后只能追加 Erratum。
- Execution 只来自用户确认的真实成交，不从建议推断。
- Review 不得利用事后信息改写当时决策。

### 2.5 Markdown 保存可读认知，SQLite 保存精确事实

数据库保存账户、流水、版本指针、约束、状态、链接、哈希、计算输入输出和运行审计；Markdown 保存 Thesis、Decision、Review、调查论证与用户可读摘要。禁止把长推理压进复杂结构化消息。

## 3. V3 不做什么

- 不连接券商下单，不自动交易或自动调仓。
- 不把推荐、意向或委托视为成交。
- 不建设独立网站作为前置条件；飞书自然语言和简明卡片优先。
- 不建立微服务群、消息总线、通用工作流引擎或大而全知识图谱。
- 不使用 LLM 心算组合状态、收益率、目标仓位或费用。
- 不让 Attention Policy 自动漂移。
- 不让园丁自动修改正式 Thesis、Decision、Principle 或历史流水。
- 不在 V3 初版实现税务申报、复杂衍生品、实时风控、VaR 优化器或券商直连。

## 4. 总体架构

```text
                         飞书用户
                对话、确认、反馈、自然语言管理
                             │
                             ▼
                         cc-connect
                             │
                             ▼
                 Primary Investment Codex
       理解 ─ 调查 ─ 情景设计 ─ 综合判断 ─ 最终沟通
          │               │                 │
          │               │                 └─ 短命专家 Agents
          │               │                    Scout / Researcher /
          │               │                    Analyst / Critic / Gardener
          ▼               ▼
  Context Recovery    Companion MCP
  有界恢复包              │
          │       ┌───────┼───────────┐
          │       ▼       ▼           ▼
          │  Financial  Cognitive   Attention
          │    Kernel     Ledger      Policy
          │       │       │           │
          └───────┴───────┴───────────┘
                          │
                    Companion Core
        Schedule / Watch / Event / Run / Case / Outbox
                          │
              SQLite 精确状态 + Markdown 认知材料
                          ▲
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
    Tushare/行情      新闻/公告/文章      用户确认/CSV
```

### 4.1 三个新增确定性域

1. **Financial Kernel**：账本、状态重建、行情快照、精确计算、情景模拟和约束检查。
2. **Cognitive Ledger**：Context、Thesis、Decision、Execution、Review 的稳定 ID、不可变版本与引用完整性。
3. **Attention Policy**：个性化通知策略、预算、静默时段、主题覆盖、反馈和可解释投递决定。

它们是同一个 Companion 进程内的模块和 SQLite Schema，不拆成独立服务。

## 5. Financial Kernel

### 5.1 权威事实对象

#### Account

保存账户身份、机构、基础币种、税务标签、状态和用户确认来源。不保存登录凭据。

#### Asset Identity

使用稳定 Asset ID 统一股票、ETF、基金、指数、现金和币种；市场代码只是带生效期的外部标识。必须防止同代码跨市场或更名产生错误合并。

#### Ledger Entry

仅追加的金融事实：

- `trade`
- `cash_deposit` / `cash_withdrawal`
- `dividend` / `interest`
- `fee` / `tax`
- `transfer`
- `fx_conversion`
- `corporate_action`
- `opening_balance`
- `reversal` / `correction`

每条记录包含：Account、Asset、交易和结算时间、数量、单价、币种、金额、费用、外部流水号、来源、确认状态及幂等指纹。

```text
draft/imported → needs_confirmation → confirmed → reversed
```

只有 `confirmed` 进入权威状态计算。原始记录不可编辑，错误由 Reversal 与更正记录表达。

### 5.2 状态重建

State Engine 必须能确定性回答：

- 任意时点的持仓、现金与待结算金额；
- 成本基础和已实现/未实现损益口径；
- 入出金与投资损益的分离；
- 多账户、多币种合并；
- 组合快照使用的价格与汇率时间。

派生快照是缓存，不是事实源。删除缓存后必须可从 Ledger 完整重建。

### 5.3 Market Snapshot

保存计算实际使用的价格、汇率、复权方式、来源、观测时间、采集时间和质量状态：

```text
healthy | stale | partial | conflicting | unauthorized | failed | unknown
```

“未取得数据”不得解释成“没有变化”。陈旧或冲突数据必须进入 Calculation Warning。

### 5.4 精确计算与情景模拟

不提供一个无边界的万能 `calculate(json)`，而提供语义窄工具：

- `portfolio_state_as_of`
- `portfolio_performance_calculate`
- `portfolio_exposure_calculate`
- `cash_need_check`
- `trade_impact_simulate`
- `rebalance_simulate`
- `max_purchase_calculate`
- `mandate_check`
- `decision_scenario_compare`
- `calculation_get` / `calculation_explain`

模拟永远不修改 Ledger。Simulation 输出至少包含 `before`、`after`、`delta`、违反的约束、缺失事实和警告。

### 5.5 Calculation Record

材料性计算保存：

```text
Calculation
├─ purpose / engine_version
├─ portfolio_snapshot_id
├─ market_snapshot_ids
├─ inputs / units / currencies
├─ assumptions
├─ formulas
├─ outputs
├─ warnings
└─ reproducibility_hash
```

最终报告引用 Calculation ID。复盘时可以区分数据、口径、假设、计算和推理分别在哪一层出错。

### 5.6 导入与对账

V3 初版支持两种入口：

1. 飞书自然语言录入后，Codex 生成 Draft，向用户回显并确认；
2. 券商 CSV 映射为 Draft Entries，再由确定性校验与用户确认。

每月 Reconciliation 比较账本派生余额与券商 Statement：差异进入待处理项，不自动补平。

## 6. 版本化个人上下文

### 6.1 Context Snapshot

Investor、Mandate、Portfolio 都必须具有版本和生效时间：

```text
draft → confirmed/current → superseded
```

- Investor：人生阶段、家庭责任、收入稳定性、税务辖区、已确认事实。
- Mandate：目标、期限、流动性底线、风险边界、集中度、允许资产和禁区。
- Portfolio Snapshot：由 Ledger 和特定 Market Snapshot 派生，不手工维护为真相。

材料性建议必须引用精确版本，禁止引用会变化的 `current.md` 别名。

### 6.2 硬约束与软偏好

- 硬约束由 Kernel 执行，例如应急金、禁止资产、最高集中度、近期必要现金。
- 软偏好由 Codex 在方案比较时解释，例如偏好低换手、熟悉行业或较少盘中操作。
- 任何突破硬约束的建议必须返回 `blocked` 或明确请求用户修订 Mandate，不能只写一段免责声明。

## 7. Cognitive Ledger

### 7.1 Evidence 与 Claim

Evidence 保存原始材料句柄、来源、发布时间、采集时间、适用口径与哈希。Thesis 内使用稳定 Claim ID 关联证据：

```text
supports | challenges | updates | background
```

原始证据不可变；解释状态可以被后续 Revision 更新。

### 7.2 Thesis

每个 Thesis 具有稳定 ID，Revision 是完整不可变快照：

```text
proposed → active → challenged/review_required → superseded/closed
```

每版包含：

- 当前结论与适用范围；
- 关键 Claims、证据和反证；
- 关键变量、估值假设和 Calculation IDs；
- 置信度及其依据；
- 失效条件；
- Evidence Cutoff；
- 未决问题与下一观察项；
- 相对上一版的 Delta 和变化原因。

```text
theses/<thesis-id>/
├── CURRENT.md
└── revisions/
    ├── R001.md
    └── R002.md
```

`CURRENT.md` 为派生视图；历史 Decision 引用 `R001` 而不是 CURRENT。

### 7.3 Decision、Execution 与 Review

#### Decision

```text
draft → issued/frozen → expired → reviewed
```

发布时冻结：Context Snapshot、Portfolio Snapshot、Thesis Revision、Evidence Cutoff、方案与不行动选项、机会成本、约束检查、Calculation IDs、失效条件和预定复盘方法。发布后只能追加 Erratum。

#### Intent / Order / Execution

```text
proposed → accepted → ordered → partially_filled → filled
                            └→ cancelled / expired
```

这些状态不得混用。只有用户确认的 Execution 产生 Ledger Entry；系统不连接券商执行 Order。

#### Review

```text
due → completed → lesson_candidate → promoted/rejected
```

Review 先评价当时信息下的过程质量，再评价结果；明确区分好过程/坏结果和坏过程/好运气。Lesson 只有跨案例验证并经用户确认后才能升级为 Principle。

### 7.4 原子发布

认知版本发布流程：

```text
写临时 Markdown
→ 计算哈希
→ 验证引用
→ SQLite 注册 Revision
→ 单事务切换 Current Pointer
→ 原子重命名文件
```

园丁可以生成 Delta 草稿、陈旧提醒和归档提案；只有 Primary 可以发布正式版本。

## 8. Context Recovery Package

跨对话恢复不能依赖扫描整个工作区。Companion 根据问题或 Event 组装有界文件句柄包：

```text
Recovery Package
├─ 生效的 Investor / Mandate Revision
├─ 相关 Portfolio Snapshot
├─ 一个或少量当前 Thesis Revision
├─ 最近 Delta
├─ 开放 Case / Watch / Review
├─ 相关冻结 Decision
└─ Evidence Cutoff 之后的新材料
```

恢复包必须具备：主题、知识截止时间、包含与排除理由、最大材料数/字节数、引用哈希和陈旧警告。默认不加载冷归档材料。

目标不是让 Codex“记住所有东西”，而是让它每次确定性恢复正确的最少上下文。

## 9. Attention Policy

### 9.1 四层策略

```text
系统安全底线
    ↓
用户全局策略
    ↓
场景 / 主题策略
    ↓
单 Watch 覆盖项
```

策略字段至少覆盖：

- 时区与静默时段；
- 每日主动通知预算；
- 即时、摘要、仅落盘三级通道；
- 普通事件的最低材料性与置信要求；
- 同主题聚合窗口和冷却期；
- 持仓、候选、财报期、现金安全垫、系统故障等场景差异；
- 消息长度和解释深度偏好；
- 临时强化模式及到期时间。

### 9.2 版本与适应

```text
draft → confirmed/current → trial → superseded
```

系统可以从“有用、无用、误报、太晚、太频繁”等反馈生成 Policy Change Proposal，但必须由用户选择：永久修改、限时试用、仅覆盖当前 Watch 或拒绝。用户沉默不构成同意。

每次主动决定引用生效的 Attention Policy Revision，因此可以解释“为什么通知”和“为什么静默”，并支持恢复旧版本。

### 9.3 混合材料性判断

```text
确定性门控
  去重 / 冷却 / 静默 / 预算 / TTL / 数据健康
        ↓
Codex 语义判断
  持仓与 Thesis 相关性 / 新颖性 / 影响 / 紧迫性
        ↓
确定性执行
  notify_now / queue_digest / file_only /
  suppress_duplicate / request_user_decision
```

LLM 不能越过系统底线，也不能自行发送未经过 Policy Engine 的主动消息。

### 9.4 主动消息协议

默认先发简明消息，而不是突然发送长报告：

```text
风险升级｜为什么现在联系你
发生了什么：...
与你有关：持仓 / 目标 / Thesis Claim ...
证据与置信度：...
系统下一步：...
操作：深入研究 / 继续观察 / 降低频率 / 暂不关注 / 这是误报
```

用户回复必须回到同一 Event、Thesis、Decision 或 Watch 链，而不是开始一段无关联的新聊天。

## 10. 主动投资循环

```text
Source Item / Observation
          ↓
Event：只说明世界发生了什么
          ↓
Recovery Package：恢复相关个人与投资上下文
          ↓
Primary 判断是否值得研究
          ↓
必要时派遣短命专家并调用 Financial Kernel
          ↓
形成 Thesis Revision / Case / Watch / Decision 草稿
          ↓
Attention Policy 决定立即通知、摘要、落盘或抑制
          ↓
飞书交互与用户确认
          ↓
保存处理结论、Policy Decision 和下一观察条件
```

每次循环必须保存：触发原因、恢复包、使用工具和 Agent、相对上次的新变化、计算记录、认知变化、通知/静默理由、下一观察点和最终状态。

## 11. Source 与 Event 治理

### 11.1 Adapter 边界

Adapter 只标准化事实，不做投资判断。逐步支持：

- Tushare 行情、财务、ETF、基金和指数；
- 官方公告、财报和业绩预告；
- 已有新闻与深度文章信息源；
- 用户文件、交易确认和人生事件；
- Webhook 与文件变化。

### 11.2 Source Health

按来源保存最近成功、覆盖区间、游标、延迟、权限、错误和连续失败次数。来源异常产生系统 Event，但遵守故障通知策略。

### 11.3 事件质量

- 内容指纹与语义主题聚合；
- 同类弱信号在时间窗内积累，不立即升级；
- 只有状态跨越或材料性 Delta 才形成新提醒；
- 用户误报反馈关联 Event、Watch、来源与 Policy；
- 禁止同一 Event 自循环唤醒。

## 12. 用户管理体验

V3 初版仍不需要网站。飞书必须能回答并执行：

- 我的真实持仓和现金基于哪些确认流水？
- 上月是否与券商账单对上？
- 当前有哪些 Thesis、失效条件和待复核问题？
- 这个建议引用的是哪版 Mandate 和持仓？
- 为什么今天联系我，为什么昨天没有？
- 当前静默时段、通知预算和主题覆盖是什么？
- 试用一个月“只即时提醒核心持仓 Thesis 变化”。
- 暂停、恢复或修改 Watch；撤销 Attention Policy 试用。
- 导出全部账本、决策、认知版本和审计信息。

只有在飞书无法清晰表达高密度对账差异时，未来才考虑生成只读 HTML/文件报告；不建设第二套管理系统。

## 13. 数据与目录

建议增加：

```text
portfolio/
├── exports/
└── statements/
theses/<thesis-id>/revisions/
decisions/<decision-id>.md
reviews/<review-id>.md
policies/
├── mandate/
└── attention/
calculations/
reconciliations/
```

敏感原始账单默认不进入 Git；使用 `.gitignore`、本地受限权限和可选加密备份。Git 保存可公开的代码与非敏感模板，不代替金融账本备份。

## 14. MCP 工具面原则

工具应按明确意图设计，不暴露通用 SQL 或任意状态写入。关键工具族：

1. `ledger_*`：导入、列出、确认、冲销、导出；
2. `portfolio_*`：时点状态、表现、暴露、对账；
3. `calculation_*` / `simulate_*`：精确计算和情景；
4. `context_*`：创建、确认、版本和恢复包；
5. `thesis_*` / `decision_*` / `review_*`：认知生命周期；
6. `attention_policy_*` / `attention_decision_*`：偏好与通知治理；
7. 保留 V2 的 `schedule_*`、`watch_*`、`event_*`、`case_*` 和 `patrol_*`。

所有材料性写操作使用 `expected_version`、用户确认来源、审计原因和幂等键。

## 15. 可靠性与安全不变量

- Ledger 守恒校验，数量、金额、币种和账户不得凭空出现或消失。
- Ledger Entry、Decision Revision 和 Thesis Revision 不允许物理覆盖。
- 任何 Simulation 不得产生真实 Execution。
- 未确认 Execution 不得改变 Portfolio。
- Decision 必须引用冻结版本和 Calculation ID。
- Policy Engine 必须为每次主动投递生成可审计决定。
- Outbox 至少一次投递，用户侧依靠通知幂等键只展示一次。
- Run、Agent 和传播链具有时间、来源、工具、Token、深度与扇出预算。
- 数据源失败降低置信度，不伪装为无变化。
- 导出必须是人类可读、机器可重建且不依赖 Codex 的格式。
- 定期执行真实恢复演练，而不仅检查备份文件存在。

## 16. 迁移策略

V3 为向前兼容迁移：

1. V2 Schedule、Watch、Run、Event、Case、Patrol、Artifact 和 Outbox 保留。
2. 当前 `portfolio/current.md` 未初始化，不迁移为账本事实。
3. `memory/investor.md` 与 `memory/mandate.md` 作为 Draft 输入，必须经用户确认后生成首个 Context Revision。
4. 现有 Thesis/Decision/Review 若未来有内容，导入为 `legacy_unverified`，不自动成为 current。
5. 默认三个 Schedule 保留，但在真实 Portfolio 和 Attention Policy 建立前只做一般信息巡视，不生成个性化行动建议。

每次 Schema Migration 必须先在线备份、在事务内升级、校验版本和完整性，并支持从备份恢复；不要求数据库向下兼容旧程序写入。

## 17. 分阶段实施计划

### Phase 0：契约冻结与测试基座

目标：在写表和工具前冻结不可变规则。

实现：

- 对象状态机、时间语义、币种/数量精度和稳定 ID；
- Ledger 守恒、Revision 不可变、Execution 分离、Policy 不漂移契约；
- Fixture：分批成交、分红、费用、出金、外汇、拆分和月结单；
- Property Tests：流水重放、冲销、幂等、崩溃恢复；
- V2 数据库迁移与回滚演练。

验收：同一流水重复导入不重复；任意顺序重放得到一致状态；历史不可原地修改；旧 V2 全部测试继续通过。

### Phase 1：Personal Financial Kernel

目标：建立个人投资真相和精确计算。

实现：

- Account、Asset、Ledger Entry、Market Snapshot；
- CSV Draft Import 与飞书逐批确认；
- Portfolio As-of、现金、成本、收益和基础暴露；
- 调仓/交易影响模拟与 Mandate Check；
- Calculation Record；
- 月末 Reconciliation 与可读导出。

验收：任意日期组合可由流水重建；未确认记录不影响持仓；入出金不计为投资收益；月结单差异可解释；计算结果可用 ID 重放。

### Phase 2：Versioned Personal Context

目标：让所有建议使用正确的人生目标、约束和真实组合。

实现：

- Investor、Mandate、Attention Policy Draft/Confirm/Revision；
- 生效时间、版本指针、试用与回滚；
- 硬约束工具与材料性建议前置检查；
- 飞书自然语言配置和影响预览。

验收：每个材料性建议引用精确 Context Revision；旧版本可恢复；违反硬约束的方案被确定性阻止；偏好不因沉默改变。

### Phase 3：Cognitive Lifecycle

目标：建立可重放的长期 Thesis—Decision—Execution—Review。

实现：

- Evidence/Claim 链接；
- Thesis 完整 Revision 与 Delta；
- Decision Freeze、Intent/Execution 分离；
- Review Schedule 与 Lesson Candidate；
- 原子发布和引用完整性校验；
- 有界 Context Recovery Package。

验收：任意历史 Decision 能恢复当时 Portfolio、Mandate、Thesis、Evidence Cutoff 和 Calculations；CURRENT 变化不影响历史；默认恢复包有固定上限。

### Phase 4：Attention & Active Decision Loop

目标：让主动性对个人有用、可解释且不会骚扰。

实现：

- 确定性门控、主题聚合、冷却、静默和通知预算；
- Codex 材料性判断记录；
- Attention Decision 与通知/静默理由；
- 飞书主动消息协议与用户反馈动作；
- Policy Change Proposal 和限时试用；
- 递归调查断路器和用户侧通知去重。

验收：每条通知和静默均可解释；同一事件只展示一次；预算与静默时段严格执行；反馈能形成待确认策略提案而不自动生效。

### Phase 5：现实世界 Adapter

目标：扩大感知范围，但不扩大 Agent 权力。

实现顺序：

1. Tushare 结构化 Watch 采样；
2. 官方公告、财报与业绩预告；
3. ETF 份额、净值、跟踪与指数规则；
4. 用户人生事件与文件/Webhook；
5. 更多新闻、宏观与政策源。

验收：每个 Adapter 有游标、幂等、覆盖区间、Source Health、降级路径和 Fixture；来源失败不生成“无变化”结论。

### Phase 6：长期运行与开源化

目标：证明可维护，而不是仅能演示。

实现：

- `companion doctor`、Schema Migration、导入模板和隐私检查；
- 月末 Close、备份恢复演练和数据完整性报告；
- 90 天 Shadow Mode：记录本应通知/静默，不立即改变真实策略；
- 示例数据集、安装向导、凭据隔离和跨机器恢复文档。

验收：在全新机器由 Repo、导出和备份恢复；连续 90 天无状态丢失、重复成交或循环唤醒；通知质量可用用户反馈审计。

## 18. 实施优先级和停机线

严格顺序：

```text
P0 Financial Kernel
→ P1 Versioned Context
→ P2 Cognitive Lifecycle
→ P3 Attention Loop
→ P4 更多 Adapter
```

不得因“主动效果看起来更明显”而提前大量接入事件源。没有真实持仓、版本化约束和注意力治理时，更多事件只会更快制造噪声。

每个 Phase 独立提交、打 Tag、迁移和验收；验收未通过，不进入下一阶段。V2 服务保持可运行，V3 新能力默认 Feature Flag 关闭，直到对应数据由用户确认。

## 19. V3 完成定义

V3 只有同时满足以下条件才算完成：

1. 任意日期的持仓与现金能由确认流水重建，并可与券商 Statement 对账。
2. 任意材料性计算可通过 Calculation ID 重放，LLM 不承担组合算术。
3. 任意历史 Decision 能恢复当时的 Portfolio、Mandate、Thesis、Evidence 和替代方案。
4. 未经用户确认的建议、意向或成交永远不改变金融事实。
5. 新 Event 能在有界上下文中增量更新既有 Thesis，而不是重新写孤立报告。
6. 每次主动通知或静默都有可查询理由，并引用生效的 Attention Policy Revision。
7. 用户能自然语言修改、试用、撤销通知偏好，系统不会静默漂移。
8. 数据源失败、冲突和陈旧被明确表达，不被解释为无变化。
9. 备份经过真实恢复验证，导出不依赖 Codex 即可读取和重建。
10. Primary Codex 始终是唯一最终判断与沟通主体，所有子 Agent 权限保持有界。

达到这些条件后，系统才从 V2 的“可审计主动调查系统”进入真正可以长期托付的个人投资伴侣。
