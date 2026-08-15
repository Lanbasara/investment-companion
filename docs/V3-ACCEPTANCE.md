# Investment Companion V3 验收记录

日期：2026-08-15

验收范围：V3 Core 的代码、确定性不变量、Schema 迁移、Plugin 和备份恢复。它不证明真实持仓已录入、90 天稳定运行、所有 Adapter 已接入或投资建议质量已经长期验证。

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
- Custom Agent 不得重复定义项目已有的同名 MCP server；静态检查必须先于动态派遣验收通过。

## Custom Agent 生产修复验收（2026-08-15）

- 根因已由最小对照实验确认：父项目和子 Agent 重复定义同名 `tushareMcp` 时，Codex 返回误导性的 `agent type is currently not available`；删除子级重复定义后可正常派遣。
- 共享 MCP 已收口到项目级 `.codex/config.toml`；5 个 Agent 均通过独立真实 spawn 烟测。
- `market_scout` 已成功重跑失败的收盘巡视，Run `run_f4903af736f848c4` 为 succeeded，Patrol `patrol_a9457e9b0c904b7e` 为 returned。
- 原失败 Run 和调查材料继续保留，不覆盖审计历史。

## 尚未验收

- 用户的 Investor、Mandate、账户、现金和持仓尚未确认。
- 尚无真实券商月结单对账与多年流水重放。
- 收益率、复杂成本基础、公司行动、税务和完整多币种 FX 仍属后续增强。
- Tushare 自动 Watch 采样、官方公告/财报专用 Adapter 尚未交付。
- Attention Engine 已通过确定性测试，但主动通知的误报、漏报和疲劳度需要 2–4 周真实使用。
- 90 天 Shadow Mode、月末 Close 和跨机器完整恢复尚未完成。

当前正确定位：**V3 Core 可开始真实使用，但不应宣称已经达到长期托付的最终完成定义。**
