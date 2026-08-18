# Investment Companion V5：实施计划

状态：按此计划开发；生产切换为独立授权阶段
日期：2026-08-18

## 1. 交付原则

每个阶段必须同时回答三个问题：它解决哪个用户问题、复用了哪个权威事实、如何证明没有越过人工执行边界。接口数量和 Token 消耗不算进度。

## 2. Phase 0：冻结 V4

- [x] 验证 V4 全量测试和 Agent 配置；
- [x] 创建并推送 annotated tag `v4.0.0-engineering-baseline`；
- [x] Tag 注明生产仍是 V3/Schema 3，未宣称数据/策略资格；
- [x] 创建 `feature/v5-investment-operating-system`。

退出条件：V4 可以独立定位和恢复，V5 不改写其历史。

## 3. Phase 1：定义用户经营闭环

- [x] 固定 V5 一句话定义、用户四种输出和非目标；
- [x] 明确 Program → Opportunity → DecisionQueue → Ledger → Review；
- [x] 定义证据阶段，不使用 AI 置信百分比；
- [x] 分离工程验收、受控使用验收和长期投资价值验收；
- [x] 写用户手册和生产运维边界。

退出条件：任何新对象都能说明其真相来源和用户价值。

## 4. Phase 2：Schema 5 与领域实现

- [x] 新增有序、带 checksum 的 Schema 5 显式迁移；
- [x] 实现 InvestmentProgram 不可变版本和单 active 约束；
- [x] 实现 Opportunity 状态机、证据引用和乐观并发；
- [x] 实现 DecisionQueue、过期、行动卡和用户响应；
- [x] 实现 OperatingBrief 的版本/supersedes；
- [x] 实现只读取 Calculation 的 ProgramScorecard；
- [x] 实现 Program 过程流量的确定性 Calculation，并显式报告结果/成本覆盖缺口；
- [x] 把 V5 状态加入 `system_status`、`session_brief` 和 `doctor`。

退出条件：新对象不复制 Ledger/Context/Decision，且所有关键转换失败关闭。

## 5. Phase 3：主动唤醒与 Gate 修正

- [x] 为新 Outbox 写入 versioned wake envelope；
- [x] 兼容旧 scheduled Run 和 V4 research-ready payload；
- [x] 实现 `wake_claim` / `wake_complete` 租约握手；
- [x] cron exec 成功后保持 Outbox sending，而不是伪报 sent；
- [x] scheduled Run 必须先终态才能成功完成 wake；
- [x] 增加 Canary/Beta Feature，区分证据生成和正式发布。

退出条件：新会话能准确知道为什么被唤醒，失败可恢复且不重复完成。

## 6. Phase 4：Codex Plugin 与用户入口

- [x] 增加 `operate-investment-program` 高层 Skill；
- [x] 更新主动管理 Skill 的 version-neutral wake 路由和 V3 降级路径；
- [x] 更新研究、决策、生命周期 Skill，使结果回到 V5 漏斗；
- [x] 运行 Plugin validator 和每个 Skill quick validation；
- [x] 更新 cachebuster、重装本地 Plugin；
- [x] 用 MCP 协议和隔离工作区验证 `v5_today`、wake 和人工成交边界；生产 `/new` 验证保留到 Phase 6。

退出条件：用户只需自然语言，Primary 会自己选择底层工具而不让用户操作内部对象。

## 7. Phase 5：工程验收与发布候选

- [x] Schema 3→5、4→5、重复启动、失败回滚和备份恢复测试；
- [x] V2/V3/V4 全量回归；
- [x] MCP initialize/tools/call 和 CLI 冒烟；
- [x] 唤醒发送、领取、超时、重试、父 Run 完成测试；
- [x] Program/Opportunity/Queue/Brief/Scorecard 端到端测试；
- [x] Agent 权限和只读隔离检查；
- [x] 更新 PROJECT-STATUS、README 和文档中心；
- [x] Commit 并推送主项目和 Plugin 的 V5 分支。

退出条件：形成可审阅的工程候选；生产仍不自动切换。

## 8. Phase 6：生产变更窗口（需要用户另行批准）

1. 固定 release commit，备份生产数据库和运行时；
2. 停止会打开数据库的 timer/service；
3. 在生产副本演练 Schema 3→5 和回滚；
4. 显式迁移生产、验证 integrity/doctor/旧任务数量；
5. 更新 Plugin 和静态 wake prompt，重启 MCP/cc-connect 会话；
6. 只开启 G0 允许的 V5 operating layer，其他 V4 Feature 保持关闭；
7. 用户 `/new`，完成首次 Program 草稿与确认；
8. 观察至少一个完整日/周/月周期，再决定是否扩大 Beta。

任何一步失败：停止扩大变更，保留失败库和日志，按运维手册回滚。

## 9. Phase 7：真实价值验证

- 数据 Canary 产生 G1 证据；
- official golden cases 产生 G2 证据；
- 研究完整性和 Shadow 产生 G3/G4；
- 明确 opt-in 的飞书 Beta 产生 G5；
- 足够真实日历时间、样本和用户成本数据后评审 G6；
- 失败策略、无价值主动任务和负净值功能必须 retire/简化。

这部分不能靠开发阶段伪造完成。工程候选的正确输出是“已经具备受控验证能力”，不是“已经能稳定盈利”。
