# Investment Companion：事件系统实施计划

状态：设计与调研基线，尚未开始实现。

## 1. 产品边界

不建设独立管理网站。第一阶段的管理界面就是现有飞书对话：用户用自然语言询问、创建、暂停、恢复、修改或删除 Watch，Primary Codex 通过 Companion 工具执行并返回紧凑列表。美观不是约束，信息完整、可解释、可操作才是约束。

需要稳定呈现的管理问题：

- 现在观察什么，为什么观察？
- 由什么事实触发，数据最近何时成功检查？
- 最近发生了什么，Codex 如何处理，为什么通知或保持安静？
- 哪些数据源或 Watch 已失效？

`memory/now.md` 保存 Codex 的当前认知焦点；SQLite 保存不可模糊的 Watch、采样、事件、运行和投递事实。两者不能互相替代。

## 2. 核心架构

```text
飞书用户
   │ 自然语言管理 / 普通投资问题
   ▼
cc-connect ───────────────► Primary Investment Codex
                                │
                                │ Companion MCP/CLI
                                ▼
                         companiond + SQLite
                         ├─ watches
                         ├─ observations
                         ├─ events
                         ├─ runs
                         └─ outbox
                                ▲
                                │ 标准化 observation
               ┌────────────────┼────────────────┐
               │                │                │
         Tushare adapter   时间/日历 adapter   未来 webhook/file adapter
               └────────────────┼────────────────┘
                                │
                         单一事件泵/调度循环
                                │ 只发送已成立的事件事实
                                ▼
                     cc-connect send / 同一项目会话
                                │
                                ▼
                     Primary Codex 自主判断与研究
```

关键原则：

1. Adapter 只描述世界，不做投资判断。
2. Watch 是用户或 Codex 明确维护的观察意图，不是藏在 Prompt 里的定时任务。
3. Event 是不可变事实；“是否值得研究、是否通知用户”仍由 Primary Codex 判断。
4. 后台只有一个调度循环，不为每个 Watch 建 cron。
5. cc-connect 继续承担通信与会话承接，不另造消息系统。

## 3. 事件如何产生

### 3.1 三种入口

- **拉取型**：事件泵根据 Watch 的 `next_check_at` 调用 Tushare 等数据源，写入标准化 Observation，再由确定性条件生成 Event。
- **推送型**：未来 webhook、文件变化或外部订阅直接写入 Observation；仍走相同的去重和事件生成流程。
- **内部型**：时间到期、复盘日期、数据源连续失败、Watch 长期未成功检查等运行事实生成系统 Event。

第一阶段只实现 Tushare 日线/基金日线和时间到期两类入口。Web/Tavily 不进入后台自动采集：它们适合 Codex 被唤醒后的研究，不适合作为稳定触发源。

### 3.2 Watch 最小模型

每个 Watch 至少包含：

- `id`、`status`、`subject_type`、`subject_id`
- `intent`：为什么值得观察的人类可读说明
- `source`、`metric`、`operator`、`threshold`
- `schedule`、`next_check_at`、`cooldown`
- `created_by`、`created_at`、`updated_at`
- `last_success_at`、`last_observation`、`last_error`

第一阶段支持四种条件：价格越界、单日涨跌越界、相对上次采样变化、指定时间到期。复杂组合条件暂不实现，避免过早创造规则语言。

### 3.3 Observation 与 Event

Observation 是一次带来源、口径和时间戳的采样，可以重复；Event 是条件从“不成立”跨越到“成立”或发生材料性状态变化后的不可变记录。

同一条件持续成立不会反复生成新 Event。只有重新解除后再次跨越、冷却结束后的材料性升级，或用户明确要求重复提醒，才产生新 Event。

## 4. 事件如何维护并保持稳定

使用单进程 SQLite，不引入消息队列、Postgres、Redis 或微服务。稳定性来自明确状态机，而不是组件数量：

```text
Watch: active → paused → archived
Run: queued → leased → succeeded | failed
Event: detected → queued → delivered → handled | dead
Outbox: pending → sending → sent | retry | dead
```

必须具备：

- SQLite WAL、事务和唯一约束；事件指纹保证幂等。
- 租约和超时回收；进程重启后可以继续未完成 Run。
- 指数退避与抖动；限制每个数据源并发和请求频率。
- Outbox 模式；先持久化事件，再尝试通过 cc-connect 唤醒，避免“发了但没记”或“记了但永远没发”。
- 失败计数与数据陈旧阈值；连续失败生成系统 Event，但不把数据缺失误报为价格变化。
- 单 Watch 冷却、全局静默时段和每轮最大唤醒数。
- 所有时间内部使用 UTC，展示时按用户时区转换；交易日历由数据源维护，不能仅按周一至周五猜测。

## 5. 与当前使用体验的融合

### 5.1 飞书即管理面

无需固定命令语法。以下自然语言由 Primary Codex 映射到 Companion 工具：

- “我现在观察什么？”
- “159101 跌破 0.72 时提醒我，至少间隔一天。”
- “暂停所有港股科技相关观察。”
- “这条提醒为什么发给我？”
- “过去七天有哪些事件被忽略了？”

工具返回机器可读结果；Primary Codex 负责解释和确认。材料性修改必须在飞书回复中回显对象、条件和状态，避免隐式创建。

### 5.2 唤醒协议

后台发送给 Codex 的不是预制分析 Prompt，而是受信任边界清晰的事件信封：事件 ID、Watch 意图、已核验 Observation、来源时间、前值、触发原因和数据质量。事件载荷明确标记为数据而非指令。

Primary Codex 收到后：

1. 读取 `memory/now.md` 和相关 Thesis，不灌入全部历史。
2. 判断事件是否材料性；不材料时记录 handled，不打扰用户。
3. 必要时调用 Tushare、Web、Tavily 和专业 Agent 深入研究。
4. 更新 Thesis/当前注意力，决定即时通知、摘要收纳或继续观察。
5. 通过 Companion 工具确认处理结果和下一观察条件。

第一阶段复用 cc-connect 的现有投资项目和会话。若目标会话不存在，事件留在 Outbox，待用户首次建立会话后投递；不擅自发到其他机器人。

## 6. Companion 工具最小接口

优先实现 MCP；同时保留同一核心库驱动的 CLI 作为运维和测试入口。MCP 对 Codex 好用，CLI 对系统恢复好用，不复制业务逻辑。

- `watch_create`
- `watch_list`
- `watch_update`
- `watch_pause`
- `watch_resume`
- `watch_archive`
- `event_list`
- `event_get`
- `event_acknowledge`
- `system_status`

事件泵内部命令：`companion tick`。它只执行到期采样、条件判断和 Outbox 投递，不生成投资报告。

## 7. 实施与进一步调研计划

### Phase 0：契约验证（0.5–1 天）

- 用 `cc-connect send` 验证指定项目/会话的主动消息投递和无活动会话行为。
- 验证 cc-connect 重启后 session key、Codex thread resume 和 `/new` 的边界。
- 实测 Tushare 交易日历、ETF 日线的更新时间、空值和停牌行为。
- 决定事件信封的最大尺寸以及飞书卡片对主动消息的实际展示。

验收：形成一份测试记录，不修改真实 Watch，不产生自动投资通知。

### Phase 1：事实核心与 CLI（1–2 天）

- 建立单包 companion 核心、SQLite schema 和 migration。
- 实现 Watch/Event/Run/Outbox 状态机及 CLI。
- 用假 Adapter 完成幂等、跨越触发、冷却、失败重试和重启恢复测试。

验收：同一输入重复执行不重复建事件；杀死进程后可恢复；所有状态可以 CLI 查询。

### Phase 2：Tushare 与单一事件泵（1–2 天）

- 接入交易日历、股票/ETF 日线 Adapter。
- 实现到期调度、限流、数据陈旧和连续失败事件。
- 先以 systemd timer 或 cc-connect 单个 cron 每分钟调用 `companion tick`；调度器只负责心跳，不承载每项 Watch 语义。

验收：用沙盒 Watch 完成价格跨越、解除、再次跨越；非交易日和停牌不误报。

### Phase 3：Companion MCP 与飞书管理（1–2 天）

- 将最小接口暴露为项目 MCP。
- 更新 Skill，使 Codex 用自然语言可靠创建、列出和维护 Watch。
- 在飞书完成创建、回显、暂停、查询原因和事件历史的端到端测试。

验收：用户不接触数据库、配置文件或网站即可完整管理 Watch。

### Phase 4：主动唤醒闭环（1–2 天）

- 实现 Outbox → `cc-connect send` 的可靠投递。
- 定义 Codex 的事件处理 Skill：恢复相关上下文、判断材料性、研究、记录处理结果。
- 加入静默、摘要和最大唤醒预算。

验收：事件发生后恢复同一投资项目；低价值事件不通知，高价值事件包含“为何现在联系你”和下一步观察点。

### Phase 5：持续 Thesis 与复盘事件（2–3 天）

- 给 Thesis 建立稳定身份、证据、反证、失效条件和相关 Watch。
- 增加财报日期、Thesis 复核日期、Decision 到期复盘等内部事件。
- 验证跨 `/new` 会话只加载相关材料并能延续判断。

验收：一次研究能够自然产生 Watch；新事件能增量更新原 Thesis，而不是重新生成孤立报告。

## 8. 暂缓项

- 独立网站或移动端管理后台。
- 通用规则 DSL、复杂 CEP、Kafka/Redis/Postgres。
- 后台自动 Web 搜索和全网新闻抓取。
- 券商连接和自动下单。
- 未经用户确认的人生事实、持仓或交易写入。

这些能力只有在最小闭环的实际使用证明需要时再增加。
