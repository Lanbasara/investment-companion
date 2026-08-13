# Investment Companion V3 验收记录

日期：2026-08-13

## 已实现

- Schema v3 向前迁移，保留全部 V2 主动运行对象。
- Financial Kernel：Account、Asset、只追加 Ledger、确认/冲销、CSV Draft Import、时点持仓、市场快照、交易影响、最大买入、组合暴露、Calculation Record 与 Reconciliation。
- Versioned Context：Investor、Mandate、Attention Policy 的 Draft、Current、Trial、Superseded。
- Cognitive Ledger：Thesis、Decision、Review 的稳定对象和不可变 Revision；Decision 强制冻结上下文；Execution 与确认流水分离。
- Context Recovery Package：按主题组装有界句柄和缺失警告。
- Attention Engine：静默时段、每日预算、主题冷却、确定性动作、反馈与投递状态。
- Source Health：游标、覆盖、连续失败与显式降级。
- Codex：新增 `manage-investment-lifecycle` Skill；MCP 工具总数 77。

## 自动化不变量

- 未确认流水不改变组合。
- 冲销通过反向记录恢复状态，原事实不删除。
- 相同流水和计算幂等。
- Simulation 不产生真实流水。
- Decimal 与最小交易单位计算不依赖 LLM 心算。
- Reconciliation 差异不自动补平。
- Decision 缺少冻结上下文时拒绝发布。
- Filled Execution 只能关联 confirmed Ledger Entry。
- Attention 负反馈不自动修改 Policy。
- V2 调度、Watch、事件、Outbox 与崩溃恢复测试继续通过。
