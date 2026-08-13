# Investment Companion V2：主动调查与认知维护系统

状态：V2 已实现并通过本机端到端验收。
日期：2026-08-13

## 1. V2 目标

V2 将当前“专业但被动的 Codex 投资助手”升级为“可管理、可恢复、会从真实世界发现变化的个人投资伴侣”。

V2 不建设另一个包裹 Codex 的投资分析系统。Primary Investment Codex 仍是唯一指挥者、理解者、Thesis 维护者和最终作者；后台只负责不可模糊的事实、调度、运行状态、可靠投递和工作材料定位。

V2 必须解决：

1. 用户通过飞书自然语言精确管理所有主动任务。
2. 定时巡视能够从新闻、公告、深度文章和既有假设中发现新线索。
3. Primary Codex 可以派出短命哨骑和专家调查，而不维持常驻 Agent。
4. 调查可以有机派生，但不能递归失控。
5. 材料增长后能够按时间和线索归纳、归档并保持可检索。
6. 机器死机、断电或服务重启后不丢任务、不重复通知，并能解释恢复过程。

## 2. 不做什么

- 不做独立网站；飞书对话是第一管理面。
- 不为每项观察创建一条 Cron。
- 不常驻任何 Subagent。
- 不让 Scout 自行建立更多 Scout、正式 Watch、Thesis 或用户通知。
- 不后台无限搜索全网；巡视只处理已接入信息源的增量、用户材料和明确调查范围。
- 不建立微服务群、Kafka、Redis、Postgres 或通用工作流引擎。
- 不连接券商，不自动交易。
- 不自动物理删除原始调查材料。

## 3. 总体架构

```text
                     飞书用户
                自然语言管理与投资对话
                          │
                          ▼
                     cc-connect
                          │
                          ▼
              Primary Investment Codex
              唯一指挥、综合与通知主体
                │                    │
        Companion MCP          派遣短命 Agent
                │          ┌─────────┼──────────┐
                │          ▼         ▼          ▼
                │       Scout     Analyst     Critic
                │          └──── 文件句柄 ──────┘
                ▼
          Companion Core
          ├─ Schedule Manager
          ├─ Watch / Run / Event
          ├─ Patrol / Case
          ├─ Artifact Catalog
          ├─ Outbox
          └─ SQLite
                ▲
                │ Observation / Source Item
        ┌───────┼───────────┬─────────────┐
        ▼       ▼           ▼             ▼
     Tushare  信息源      用户材料      内部时间/状态
        ▲
        │
  systemd timer：唯一固定心跳
  默认每30分钟执行 companion tick
```

## 4. 核心对象与生命周期

### 4.1 Schedule

Schedule 表达“何时为了什么使命醒来”，不是 Cron 表达式的别名。

```text
active ⇄ paused → archived
                  expired
```

它保存：使命、类型、范围、频率、时区、有效窗口、跳过策略、失败策略、预算、创建来源、下次运行时间和版本号。

类型仅包括：

- `patrol`：巡视真实世界信息；
- `review`：复核 Thesis、Case 或 Decision；
- `maintenance`：认知园丁与系统治理；
- `one_shot`：指定时间的一次性任务。

### 4.2 Watch

Watch 表达长期观察意图，可以被 Schedule 或确定性 Adapter 服务。它说明观察对象、原因、验证或失效条件、期限和相关 Thesis/Case。

自动派生的 Watch 默认是临时对象，必须有 TTL、最大运行次数和来源 Case；转为长期观察需由 Primary Codex 重新判断并留下理由。

### 4.3 Patrol

Patrol 是一次短命巡视行动：

```text
commissioned → scanning → returned
                         ├─ no_change
                         ├─ file_only
                         ├─ propose_case
                         └─ urgent_review
                  failed / expired
```

Agent 完成后结束；长期保存的是 Brief、工作材料和运行记录。

### 4.4 Investigation Case

Case 是一个可以跨数周或数月恢复的调查案件：

```text
proposed → open → investigating → awaiting_evidence
                         └──────→ ready_for_judgment
                                      ↓
              resolved | monitoring | rejected | superseded | expired
```

Case 不等于 Agent 会话。每次需要工作时，Primary Codex重新派遣临时 Agent 并传入委托书和必要文件句柄。

### 4.5 Observation、Source Item 与 Event

- Observation：一次带来源、口径和时间戳的结构化采样。
- Source Item：一篇公告、新闻、文章、用户文件或研究材料的入口记录。
- Event：不可变的材料性变化事实或系统状态事实。

信息源内容不是天然 Event。Scout 或确定性 Adapter 先形成证据，Primary Codex 再决定其认知意义。

## 5. Schedule Manager 与心跳

### 5.1 技术决策

使用 systemd timer 作为唯一、固定、默认每30分钟一次的粗粒度心跳：

```text
systemd timer（默认30分钟）→ companion tick → Schedule Manager
```

不自行实现系统计时器，不让 systemd 理解任何投资任务。Schedule Manager 负责时区、交易日、活动窗口、冷却、预算、任务合并、Misfire 和 `next_run_at`。30分钟只是默认调度精度，不代表每30分钟调用数据源或 AI；没有到期任务时，Tick 只完成一次本地 SQLite 查询并退出。

需要更低延迟的 webhook、用户材料和未来消息订阅不等待下一次心跳：入口先原子写入 Inbox/Event 与 Outbox，再触发一次合并执行；30分钟心跳只承担到期扫描和兜底恢复。默认任务频率按投资意义而非技术能力设置：广域新闻/文章巡视每个交易日1次（收盘后），重点主题可在盘前、午间、收盘后共3次；结构化持仓与标的复核通常每日收盘后1次；财报或重大案件的临时强化巡视每2–4小时，必须带TTL；价格类30分钟巡视只有用户明确要求时才启用。园丁按周做活跃材料体检、按月做时间线归纳与归档，不设置每日园丁。

### 5.2 AI 友好的管理接口

Primary Codex 通过 Companion MCP 使用：

- `schedule_create`
- `schedule_list`
- `schedule_get`
- `schedule_patch`
- `schedule_pause`
- `schedule_resume`
- `schedule_run_now`
- `schedule_archive`
- `schedule_history`
- `schedule_explain`
- `system_status`

自然语言先由 Codex 理解，再转成字段级 Patch。服务验证 `expected_version` 后原子提交，防止基于旧状态覆盖新修改。高频化、扩大范围、永久运行和批量归档需要先回显影响；普通暂停、恢复、频率调整可以执行后回显并支持撤销。

用户永远管理 Schedule，不接触 systemd timer 或 Crontab。

### 5.3 Misfire 策略

每个 Schedule 明确选择：

- `skip`：错过的运行不补；
- `run_once`：恢复后只补一次；
- `catch_up_limited(n)`：最多补最近 N 次。

默认：投资 Patrol 使用 `run_once`，园丁使用 `run_once`，可从数据源补区间的结构化采样使用 `skip` 后回补数据区间。

## 6. 机器死机与重启恢复

重启恢复是 V2 的强制验收项，不是部署细节。

### 6.1 持久化边界

任何外部动作之前必须先提交 SQLite：

- 到期计划先创建唯一 Run；
- 事件先写 Event；
- 主动消息先写 Outbox；
- Agent 派遣前先写 Commission 和 Brief 路径。

不得依赖内存保存尚未完成的任务。

### 6.2 单实例与租约

- `companion tick` 使用数据库租约或文件锁，保证同一时刻只有一个调度 Tick。
- Run、Patrol 和 Outbox 使用带 `lease_until` 的租约。
- 进程死亡后，过期租约可由下一次 Tick 回收。
- 每次副作用使用稳定幂等键，恢复时不会重复建 Event 或重复投递同一消息。

### 6.3 开机恢复顺序

```text
机器启动
  ↓
文件系统与网络可用
  ↓
cc-connect.service 启动并健康
  ↓
companion-recover.service 执行一次
  ├─ SQLite integrity_check
  ├─ 回收过期租约
  ├─ 将中断 Run 标为 recoverable
  ├─ 根据 Misfire 生成最多一次补跑
  └─ 恢复未发送 Outbox
  ↓
companion.timer 开始默认每30分钟 Tick
```

systemd 单元应配置：

- `After=network-online.target cc-connect.service`
- `RequiresMountsFor=/home/ghk/investment-home`
- timer 使用 `Persistent=true`
- `RandomizedDelaySec` 防止所有恢复任务同秒启动
- 限制连续失败重启，不制造重启风暴

### 6.4 数据安全

- SQLite 使用 WAL、`foreign_keys=ON`、事务和唯一约束。
- 定期在线备份到工作目录之外的受控备份目录，使用 SQLite backup API，而非复制活动 WAL 文件。
- 每日校验数据库可读性；备份保留滚动版本。
- Artifact 先写临时文件、刷新并原子重命名，避免半份 Markdown。
- 系统状态必须展示最近成功 Tick、数据库检查、备份、投递和数据源成功时间。

### 6.5 用户可见行为

恢复后不能把停机期间每个心跳逐一补发。系统只允许：

- 合并为一次恢复 Patrol；
- 从结构化数据源补取完整缺口区间；
- 对可能错过的新闻时间窗做一次受限增量巡视；
- 仅在发现材料性变化或恢复异常时通知用户。

用户可问：“昨晚重启后漏了什么？”`system_status` 和 `schedule_history` 必须给出可审计回答。

## 7. 哨骑与专业调查组

### 7.1 组织原则

哨骑是一项调查行动，不是常驻 Agent。`market_scout` 只是执行 Patrol 的一种临时角色。

只有 Primary Codex 拥有：派遣 Agent、开立 Case、激活正式 Watch、修改 Thesis 和通知用户的权力。子 Agent 可以大胆提出怀疑、关联和扩展建议，但不能自行升级或继续派生 Agent。

### 7.2 调查委托书

每次派遣由 Primary Codex 创建 `BRIEF.md`，包含：

- 为什么派遣；
- 明确问题与时间范围；
- 优先信息源和已有文件句柄；
- 禁止事项与权限边界；
- 时间、来源数量和工具预算；
- 返回标准和停止条件。

Agent 可以请求改变范围，不能静默改变命令。

### 7.3 专业角色组织

按问题依赖关系临时组织，而非固定委员会：

- `market_scout`：增量巡视、聚类、新颖性和外围关联；
- `source_researcher`：原始来源、身份、日期与冲突核验；
- `financial_analyst`：财务、基金结构、统计和估值影响；
- `thesis_critic`：在初步 Thesis 后攻击关键因果链和遗漏变量。

简单案件只调用一个角色；材料性预测按现有规则使用 Analyst 和 Critic。所有 Agent 只返回工作材料，Primary Codex 独立综合。

### 7.4 防止调查失控

- Scout 不能创建新 Agent、正式 Watch、Case、Thesis 或通知。
- 一次 Patrol 中的新线索只返回建议；必须回到 Primary Codex。
- 自动派生深度最多一级；Case 派生新 Case 必须由 Primary 留下批准理由。
- 每次 Tick 最多启动一个 Patrol；同时最多两个调查 Agent。
- 临时 Watch 默认不超过 30 天，并限制运行次数和数据预算。
- 普通信号进入摘要；单日主动通知有全局上限。
- 每个调查必须有时间、来源和工具预算以及明确结束状态。

允许大胆发现，限制擅自行动。

## 8. 多 Agent 信息沟通

不使用庞大 Candidate Signal Schema 传递认知。采用：

```text
小型控制信封 + 文件句柄 + Markdown 工作材料
```

控制信封只包含：Case/Patrol ID、状态、注意级别、Brief 路径、结果路径和幂等键。论证、来源上下文、矛盾证据、未知项和建议保存在文件中。

```text
investigations/
├── inbox/
├── patrols/
└── cases/
    └── <case-id>/
        ├── BRIEF.md
        ├── STATUS.md
        ├── TIMELINE.md
        ├── CURRENT.md
        ├── OPEN-QUESTIONS.md
        ├── sources/
        ├── patrols/
        ├── analysis/
        └── critiques/
```

SQLite 是目录和运行账本，Markdown 是工作材料，Primary Codex 是读者和作者。

Artifact Catalog 仅保存：路径、类型、主体、Case/Watch/Event 关联、创建及生效时间、状态、取代关系和内容哈希，不把完整认知关系化。

## 9. 信息发现机制

### 9.1 确定性信息

Tushare 等 Adapter 增量取得行情、财报日期、基金/指数变化和其他结构化事实。Adapter 只生成 Observation，不做投资解释。

### 9.2 新闻和深度文章

已有信息源、官方公告、用户链接/PDF 和 Codex 研究中发现的材料进入 Information Inbox，保存来源、发布时间、采集时间、内容指纹和正文/文件句柄。

Patrol 只处理游标之后的增量材料，并围绕 Watch、Thesis、Case 和 Brief 聚类、去重、核验原始来源及判断新增信息。后台不重复全网漫游。

Scout 返回三类内容，而非伪精确单一置信分：

- `Observation`：已确认发生的事实；
- `Suspicion`：值得继续调查但尚不充分的怀疑；
- `Challenge`：可能推翻既有 Thesis 的材料。

Primary Codex 决定归档、关联 Thesis、创建临时 Watch、开立 Case 或形成正式 Event。

### 9.3 有机传播链

```text
定时 Patrol
  ↓
发现新材料或新关联
  ↓
Scout 返回文件句柄和调查建议后结束
  ↓
Primary Codex 判断
  ↓
Case / 临时 Watch / 静默归档
  ↓
后续真实世界变化
  ↓
新 Event 再次唤醒 Primary Codex
```

每次传播都必须经过 Primary Codex，且受派生深度、TTL、运行次数和预算约束。

## 10. 认知园丁与 Meta 管理

`knowledge_gardener` 是由 Maintenance Schedule 触发的短命 Agent，不是常驻清理进程。

职责：

- 识别重复和已被正式 Thesis 吸收的临时材料；
- 按线索和时间增量维护 `TIMELINE.md`；
- 标记 `current / superseded / contradicted / background / unresolved / duplicate`；
- 将满足规则的已结案 Case 移出活跃目录；
- 发现陈旧 Watch、未结案 Case、过期证据和异常增长；
- 生成治理建议和可审计的变更清单。

触发机制：

- 每周：Inbox 去重、开放 Case、临时 Watch、失败和未处理线索体检；
- 每月：按 Case/标的整理时间线和当前证据；
- 每季度：可选的长期 Thesis 与冷归档建议；
- 容量/状态：材料超过阈值、Case 结案或 Thesis 重大变化时局部整理。

园丁可自动更新索引、生成时间线草稿和移动满足明确规则的归档材料；改变当前 Thesis、合并 Case 或暂停长期 Watch 需要 Primary 审核；永久删除材料、决策历史或用户原文必须由用户确认。V2 默认不实现物理删除。

## 11. 飞书使用体验

不要求命令语法。用户可以自然表达：

- “列出所有主动任务，按下一次运行时间排序。”
- “159101 改成交易日收盘后巡视，财报前后一周每天一次。”
- “暂停它，但保留案件和材料。”
- “立即派一次哨骑，只检查过去七天的新信息。”
- “哪些任务是系统从调查中自动建议的？”
- “为什么今天运行了三次？”
- “把月度整理改成季度，但这个 Case 仍按月。”
- “昨晚机器重启后漏了什么？”

每次修改回显实际字段变化、下一运行时间、期限、预算和是否需要用户确认。`schedule_explain`、`schedule_history` 和 `system_status` 共同提供管理感与审计能力。

## 12. 最小工具面

### Schedule 与运行

- `schedule_create/list/get/patch/pause/resume/run_now/archive/history/explain`
- `run_list/get/retry/cancel`
- `system_status`

### Watch、事件与材料

- `watch_create/list/get/patch/pause/resume/archive`
- `event_list/get/acknowledge`
- `artifact_register/list/get`
- `inbox_add/list`

### Investigation

- `case_create/list/get/patch/close`
- `patrol_commission/list/get/complete`

工具方法可以合并实现，但语义边界不得混淆。所有变更接口使用版本检查并写审计日志。

## 13. 实施计划与验收

### Phase 0：运行契约与故障实验（1 天）

- 验证 `cc-connect send` 对指定项目和投资会话的投递、会话缺失与 `/new` 行为。
- 验证 cc-connect daemon 的 systemd 单元名称、启动顺序和健康判断。
- 实测 Tushare 交易日、ETF 日线、停牌、空值和更新时间。
- 做一次受控重启实验，记录当前会话与任务行为。

验收：形成运行契约；不创建真实主动投资通知。

### Phase 1：Companion Core（2 天）

- 建立单包核心、SQLite migrations 和 CLI。
- 实现 Schedule、Run、租约、Misfire、审计和 Outbox。
- 实现幂等键、乐观锁、单 Tick 锁、失败退避和状态查询。

验收：重复 Tick 不重复运行；并发 Tick 只有一个生效；状态均可查询和解释。

### Phase 2：重启恢复与部署（1–2 天）

- 实现 recover、integrity check、在线备份和过期租约回收。
- 编写 companion service/recover/timer systemd 单元及依赖顺序。
- 测试在 Tick、Patrol commission、Outbox 发送各阶段强制终止进程和重启机器。

验收：任务不丢、事件不重复、消息至多一次可见投递；恢复过程可查询。

### Phase 3：信息入口与确定性事件（2 天）

- 实现 Information Inbox、Artifact Catalog、内容指纹和增量游标。
- 接入现有信息源入口与用户材料登记。
- 接入 Tushare 交易日历、股票/ETF 日线和时间到期 Adapter。

验收：增量内容不重复入箱；非交易日/停牌不误报；缺口区间可恢复。

### Phase 4：哨骑与 Case（2–3 天）

- 新增 `market_scout` Agent Profile 和调查 Skill。
- 实现 Brief、Patrol、Case 与文件句柄交接。
- 实现派生深度、TTL、预算和不可递归派遣约束。
- 复用现有 Source Researcher、Analyst 和 Critic。

验收：定时 Patrol 能从新增文章发现线索，Scout 结束后由 Primary 决定是否开 Case；Scout 无法自行升级或通知。

### Phase 5：Companion MCP 与飞书控制面（2 天）

- 暴露最小管理工具；更新 Plugin Skills。
- 完成自然语言 Patch、影响预览、回显、撤销和 explain/history/status。
- 验证创建、细粒度修改、暂停、恢复、立即运行和归档。

验收：用户无需接触网站、Cron、数据库或配置文件即可完整管理主动系统。

### Phase 6：主动唤醒闭环（2 天）

- Outbox 可靠调用 cc-connect 投递同一投资项目/会话。
- 实现 Codex 事件处理 Skill：上下文恢复、材料性判断、研究、Thesis/Case 更新和通知决策。
- 实现静默、摘要和单日通知预算。

验收：低价值事件静默处理；高价值事件说明为何现在联系用户；重启后不重复通知。

### Phase 7：认知园丁（2–3 天）

- 新增 Gardener Agent Profile 和 Maintenance Schedule。
- 实现 Timeline、状态标记、归档提案和治理报告。
- 完成每日/每周/月度与容量触发测试。

验收：材料增长后 Primary 仍只需读取少量相关句柄；无自动物理删除；所有整理可审计。

### Phase 8：真实使用观察（至少 2 周）

- 从少量标的、两个 Patrol 和一个月度园丁开始。
- 记录噪声、遗漏、调用成本、错误派生、恢复和通知质量。
- 只根据真实使用增加规则或数据源。

验收：用户能说明系统在观察什么、为什么运行、最近发现什么、重启后是否恢复；系统没有不可解释的任务扩散。

## 14. V2 完成定义

同时满足以下条件才算 V2 完成：

1. 飞书可以自然语言管理全部 Schedule、Watch、Patrol 和 Case。
2. 新闻、文章、用户材料和结构化数据都能增量进入统一调查链。
3. Primary Codex 能派遣短命哨骑并通过文件句柄接续调查。
4. 有机派生每一步都经过 Primary，且受 TTL、深度和预算约束。
5. 园丁能按时间和线索维护材料，活跃上下文不会无限增长。
6. 机器强制重启后任务、事件和材料不丢，补跑受控，不重复通知。
7. 用户能随时获得可解释的系统状态、历史、失败和恢复报告。

## 15. 设计原则摘要

- Cron/systemd 只负责叫醒；Manager 决定做什么。
- 哨骑负责扩大感知；园丁负责控制熵增。
- Agent 短暂存在；使命、Case、证据和 Thesis 长期存在。
- 结构化状态负责可靠运行；文件句柄承载复杂认知。
- 允许大胆发现，限制擅自行动。
- 每次任务传播都回到 Primary Codex。
- 重启是正常生命周期事件，不是异常边角情况。
