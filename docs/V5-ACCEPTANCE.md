# Investment Companion V5：验收契约

状态：工程与真实使用分层验收
日期：2026-08-18

## 1. 验收结论只有三类

- **工程通过**：代码、迁移、状态机、权限和恢复符合契约；
- **允许持续研究运行**：范围、用户批准、资源预算、停止方式和观测指标齐全；
- **允许扩大/正式使用**：真实证据通过对应 Gate。

“测试通过”不得写成“策略有效”，运行时间达到某个天数也不得自动写成“Go”。

## 2. A：V2/V3/V4 不回归

- 旧 Schedule、Watch、Event、Run、Case、Patrol 数量和历史迁移后不变；
- 旧 `codex_turn` 默认行为可被 wake envelope 领取；
- deterministic Job 的父子终态、预算、恢复和零模型阶段不变；
- confirmed Ledger 仍是唯一真实组合来源；
- Context、Thesis、Decision、Review 历史不可覆盖；
- ManualAction、Execution 和 Ledger 仍严格分离；
- Shadow 不能写真实 Ledger；
- Custom Agents 没有可写 Companion MCP。

任一失败：V5 工程不通过。

## 3. B：Schema 与恢复

必须自动化验证：

1. 全新数据库直接初始化到 Schema 5，并有 0003/0004/0005 三条有序记录；
2. Schema 3 和 Schema 4 普通启动均 fail closed；
3. `migrate` 先创建独立备份，再原子升级；
4. 人为制造 DDL 冲突时，schema_version 和旧表结构不被半升级；
5. 迁移后 `PRAGMA integrity_check=ok`；
6. 重复启动不重复迁移；
7. V3 稳定运行时可使用迁移前备份恢复，而不是手工改版本号。

## 4. C：InvestmentProgram

- 内容必须有目标、成功标准、基准、风险预算、范围、周期、节奏、停止条件和账户；
- 只接受存在且 active 的账户；
- 只引用已确认的 Investor/Mandate/Attention；
- 确认时再次验证引用是 current；
- Trial 必须有未来到期时间；
- 同时只能有一份 active Program；
- supersede 必须显式指向旧 Program；
- 修改使用新 Revision 和 optimistic version，不覆盖旧内容；
- Context 或账户状态漂移后经营动作 fail closed，直到用户重审 Program；
- Feature 关闭时可以起草，不能确认和运行经营动作。

## 5. D：Opportunity Funnel

- 新机会必须有非空 subject、原因和不可变证据；
- active 阶段一次只能前进一级，不能从 observed 跳到 actionable；
- qualified 必须有 active Thesis 或合格 Strategy；
- qualified 至少声明并冻结两个独立来源、falsifier 和 counterevidence；
- actionable 必须是 current 数据、decision-grade、无重大未知项；
- actionable 必须绑定 current issued、未过期且有 invalidator/no-action 的 Decision；
- idempotency key 不能被另一个 Opportunity 重用；
- rejected/expired/closed 后不能重新推进；
- 每次转换均保留 before/after、evidence、actor、reason 和时间。

## 6. E：DecisionQueue 与人工边界

- 只有 active actionable Opportunity 可以入队；
- Queue Decision 必须与 Opportunity 相同；
- Queue 不能超过 Decision 或 ManualAction 有效期；
- ready/presented/snoozed/accepted 过期后自动变成 expired；
- 行动卡必须显示 invalidators、no-action、有效期、证据等级和“人工执行”；
- 有 ManualAction 时，行动卡调用实时重验证并暴露阻断原因；
- Decision 被 supersede 或 ManualAction 失效时，旧 Queue 确定性过期并留下审计；
- presented 必须关联实际 delivered 且 evidence 引用该 Queue 的 AttentionDecision；
- accepted 不自动创建 Execution、不写 Ledger、不连接券商，并继续作为待手工执行行动显示；
- 用户拒绝、稍后处理和关闭均保留原因；snoozed 必须有确定恢复时间；
- 真正成交只能在用户报告后进入 Lifecycle/Ledger 流程。

自动化反例：接受 Queue 后 `executions` 和 confirmed Ledger 计数必须不变。

## 7. F：Brief 与今日入口

- `v5_today` 只能返回 setup_required/action/no_action/review_required；
- 没有 active Program 时不能生成股票或行动；
- 有 ready/presented/accepted Queue 时必须返回 action；snoozed 返回带确定恢复时间的 review_required；
- 没有 Queue 但没有本期日 Brief 时必须返回 review_required；
- no_action Brief 在存在有效 Queue 时失败，且过了 next_check_at 或 Program Revision 变化后不再代表今日结论；
- action Brief 必须至少引用一个有效 Queue；
- Brief payload 使用固定字段，不能塞入隐含投资真相；
- 同一 period 的修订产生 supersedes，不覆盖历史；
- presented 必须关联实际 delivered 且 evidence 引用该 Brief 的 AttentionDecision。

## 8. G：Scorecard

- 调用方不能提交 metric value；
- Program 过程指标必须由 `v5_program_metrics_calculate` 从冻结 transition/audit/brief ID 计算；
- 每个 metric 只能由存在且 hash 验证通过的 Calculation 解析；
- Calculation 的 as_of 必须落在 Scorecard 周期内；
- output path 必须以 `outputs.` 开始并解析到标量；
- 比较只能引用已解析指标名；
- 读取 Scorecard 时重新核对 Calculation 值；
- 没有指标时状态是 insufficient_evidence；
- 无 Program 级收益、基准、用户时间或成本归因时必须明确 insufficient_evidence，不能借用全局计数；
- 模型不能把文字判断、外部文章数字或聊天记忆写成业绩；
- Correction 使用新 revision/supersedes。

## 9. H：唤醒与恢复

- 新 scheduled Run 和 research-ready 都写 versioned envelope；
- 旧 payload 可归一化；
- cron exec 成功后 Outbox 是 sending，不是 sent；
- `wake_claim` 优先领取触发本次唤醒的 sending 信封；
- 同一租约只有 owner 可以完成；
- scheduled Run 未终态时 `wake_complete(success=true)` 必须失败；
- 失败会把 leased Run 变为 recoverable，并让 Outbox 重试；
- 租约过期由 recover 恢复；
- 事件成功送给 Primary 后是 delivered，仍需 Primary 独立 acknowledge 才是 handled；
- 空唤醒返回 null 并静默，不随便领取/搜索其他任务。

## 10. I：Gate 与 Feature 无死循环

| 能力 | 受控证据生成 | 正式放行 |
|---|---|---|
| 数据 | `v4_live_data_canary`：G0、持续研究、冻结端点与单 Job 请求预算 | `v4_live_data`：G0+G1 |
| Decision | `v4_decision_support_beta`：G0–G4、opt-in、expires | Full：G0–G5 |
| V5 经营层 | G0 后、用户确认 Program | 真实产品范围扩大仍看 G5/G6 |
| Strategy eligible | Shadow 可产生样本 | G6 + 样本门 + Primary 评审 |

Beta 配置缺 `user_opt_in_ref` 或未来 `expires_at` 时必须失败。Feature Flag 不能绕过 Gate。

## 11. J：MCP、CLI、Plugin

- MCP server version 为 5.2.0；
- `tools/list` 中所有 V5/Wake 工具有 implementation；
- 参数 Schema 拒绝未知字段；
- CLI 提供 status/today/wake claim/wake complete；
- Plugin 有高层经营 Skill，主动 Skill 识别 version-neutral wake；
- 所有 Skill 通过 validator 和 quick validation；
- cachebuster 更新后本地重装；
- 新 `/new` 会话能先读取 session brief，再通过 `v5_today` 进入；
- 用户不需要知道 table、Gate ID 或 Worker 命令即可正常使用。

## 12. K：真实使用验收，不能由 Fixture 代替

### 日/周/月产品验证

- 持续记录每个交易日：日入口是否准确、无行动是否诚实、通知是否过量；
- 至少 4 个周周期记录：研究推进/淘汰是否可理解、重复任务是否减少；
- 至少 3 个月记录：Scorecard 是否能从真实 Calculation 重放，用户时间和 Token 是否值得；
- 至少 5 个真实用户选择记录：接受、拒绝、等待、过期和偏离是否完整。

### 策略价值验证

必须按 Strategy 的预注册 sample gate、真实经过时间、独立基准、成本、回撤、市场状态和完整 denominator 评审。90 日只是最低时间示例，不是自动通过条件。

### 产品净价值

至少比较：决策质量、漏报/误报、避免的错误、用户花费时间、Token/数据成本、真实组合结果和原有人工流程。若净价值为负，应停用相应任务/策略/功能。

## 13. 发布命令基线

```bash
python3 -m py_compile companion/*.py
python3 -m pytest -q
./bin/companion agent-check
./bin/companion --root "$(mktemp -d)" init
./bin/companion --root "<isolated-root>" v5-status
python3 /home/ghk/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  /home/ghk/plugins/investment-companion
```

涉及 MCP/Agent 后还需真实 smoke。生产迁移、Plugin 切换、systemd enable 和 cron edit 不属于自动化测试授权。

## 14. L：持续量化研究

- 只有 `v4_jobs`、生产 G0 和配置完整的 `v4_live_data_canary` 同时有效时才可运行；
- Program 必须绑定用户批准、开始时间和单 Job 请求上限；配置和 Schedule 不得设置试用到期或最大运行次数；
- 数据 Handler 只允许冻结的 Tushare 日线、复权因子和交易日历端点；
- 扫描规则、输入哈希、候选和前向观测必须写入不可变 Manifest；
- 扫描 Job 的模型 Token 必须为 0；最新扫描必须进入当日收盘复盘，不得等待月度样本结束；
- 连续候选可立即触发完整研究，完整研究满足契约后可形成手工 Decision，不以月度复盘为前置条件；
- Handler 不得创建 Opportunity、Decision、ActionCard、Execution、Shadow 或 Ledger Entry；
- 每月 19 日必须生成严格前向 Review Manifest，并无论结果好坏都形成用户报告；
- 证据不足只影响结论强度，不暂停功能；结果无效时建议修改或替换新版本，不能重写旧结果或自动晋级 Strategy；
- 旧试用配置迁移后必须保留历史 ID、Manifest 和开始时间，同时移除 `expires_at`、`max_trading_days` 与 Schedule `max_runs`；
- 真实首跑需核对：数据对象、Scan Manifest、月度 Review Schedule、Job 终态、每日复盘接入、Ledger/Decision/Opportunity 计数不被 Handler 自动改变和飞书交接。
