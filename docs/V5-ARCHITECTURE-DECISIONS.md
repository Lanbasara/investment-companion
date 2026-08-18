# Investment Companion V5：架构决策

状态：V5 工程候选
日期：2026-08-18
关联设计：[V5-DESIGN.md](V5-DESIGN.md)

## ADR-001：V5 是经营层，不是第二套投资真相

**决策**：InvestmentProgram、Opportunity、DecisionQueue、Brief 和 Scorecard 只协调现有权威对象。

**结果**：持仓仍来自 Ledger，个人约束仍来自 Context，研究仍来自 Manifest/Experiment，正式判断仍来自 Decision，成交意图仍来自 Execution。V5 不建立 `current_portfolio_v5`、`ai_score` 或另一份策略状态。

## ADR-002：同一时刻只允许一份 active InvestmentProgram

**决策**：新 Program 激活时，若存在另一份 active Program，必须显式声明 supersede；不能静默并存。

**原因**：单用户的资金、流动性和风险预算是共同约束。多个隐含 Program 会制造重复资金、冲突基准和无法解释的机会成本。需要多策略时，应在同一 Program 内定义组合结构。

## ADR-003：Program 必须引用当前确认 Context

**决策**：草稿可以基于已确认 Context 起草，正式确认和恢复运行时必须再次验证 Investor、Mandate、Attention 都是当前有效版本。

**结果**：人生目标、风险边界或账户状态变化时，今日入口进入 `review_required`，经营动作 fail closed，直到用户确认修订版；不能靠旧 Prompt 继续运行。

## ADR-004：机会阶段是证据资格，不是 AI 置信分数

**决策**：使用 observed、researching、qualified、actionable 四级单向状态机；不保存模型随口给出的 0–100 分。

**原因**：语言模型概率不可校准、不可跨任务比较。证据阶段可以通过来源数、数据新鲜度、反证条件、重大未知项和当前 Decision 确定性检查来审计。

## ADR-005：Opportunity 允许失败，且必须保留失败

**决策**：每一步转换都冻结来源、原因、actor 和幂等键；rejected/expired/closed 是一等终态。

**原因**：只有保留被淘汰和错过的机会，才能检查幸存者偏差、假阴性和研究效率。

## ADR-006：DecisionQueue 是呈现投影，不是 Execution

**决策**：Queue 只能引用 current issued Decision；用户接受 Queue 不自动创建 Execution，更不会写 Ledger。

**原因**：建议、用户选择、下单意向、券商真实成交和权威账本是五个不同事实。合并它们会让系统虚构持仓或误以为用户已执行。

**结果**：accepted 在有效期内仍属于待处理行动；snoozed 必须有确定恢复时间。两者都不能被无行动结论覆盖。

## ADR-007：无行动是首要输出，但必须有证据边界

**决策**：`no_action` 简报只能在没有有效 Queue 时发布，并且只在当前 Program Revision 的 `next_check_at` 前生效；缺少本期检查则返回 `review_required`，不能把没运行或旧结论包装成今日无行动。

**原因**：专业系统大部分时间应不交易，但“研究后不行动”和“根本没检查”必须区分。

## ADR-008：Scorecard 数值只能投影 Calculation

**决策**：API 不接受调用方提供 `value`。系统按 `calculation_id + outputs path` 解析标量并在读取时复核。

**原因**：Codex 擅长解释，不应成为收益率、净值、风险或组合算术的来源。没有合格计算时，结果是 `insufficient_evidence`。

**实现**：Program 过程流量由专用确定性 Calculation 生成；它不得把尚未归因的全局 Token、数据费用或缺少现金流调整的端点净值冒充 Program 净价值。

## ADR-009：Feishu 是 Attention 门控后的投影

**决策**：Queue/Brief 只有关联一个实际 delivered、且 evidence 明确引用该 Queue/Brief 的 `notify_now` AttentionDecision 后才能标记 presented。

**原因**：数据库“准备好”不等于用户“已经看到”；安静时间、预算、去重和用户反馈仍由 Attention Policy 管理。

## ADR-010：静态唤醒与工作领取分离

**决策**：cc-connect cron 只触发会话；Primary 必须 `wake_claim` 取得精确信封和租约，完成真实对象后再 `wake_complete`。

**原因**：固定 cron prompt 无法携带动态 Run；“cron exec 成功”也不能证明 Codex 已处理工作。Outbox 状态必须覆盖整个交接过程。

## ADR-011：试运行产生 Gate 证据，正式发布消费 Gate 证据

**决策**：Canary/Beta 具有更窄 Feature、范围、用户批准、到期和停止条件；Full Feature 仍要求对应 Gate。

**结果**：G1/G5 可以通过真实受控使用产生，不形成逻辑死锁；失败时只停止扩大范围，不删除失败证据。

## ADR-012：V3 主动任务兼容升级，不做全量重设计

**决策**：Schema 5 不修改 Schedule/Run 的业务语义。旧 `codex_turn` 被 wake envelope 兼容读取；确定性 Pipeline 继续走 JobRun。

**原因**：现有任务积累了真实运行历史。只有使用数据证明某任务重复、无 Program 归属或没有净价值时，才逐项重构。

## ADR-013：不运行 7×24 LLM

**决策**：市场数据和确定性作业按交易日/事件运行，Primary Codex 仅在语义判断或沟通点唤醒。

**原因**：当前是日频个人投资系统，不是做市或高频交易。常驻模型增加成本、噪声和不可审计状态，不增加必要能力。

## ADR-014：开源项目用于检验边界，不用于拼装产品

**决策**：继续吸收 Qlib 的时间/PIT 和实验思想、LEAN 的职责/Reality Model、FinRL-X 的 target weights、RD-Agent 的假设—验证循环；运行时仍以项目自有窄契约为核心。

**采用门槛**：外部组件只有在解决已观察到的瓶颈、保持项目真相归属、通过同一 golden cases、可替换且净维护成本为正时才进入。

## ADR-015：V5 Feature 只保护经营动作，不阻止起草

**决策**：Program 草稿可以创建；确认 Program、推进机会、入队、简报和 Scorecard 需要 `v5_operating_system` 与 G0。Beta Decision Support 可在 G0–G4 和明确 opt-in 下运行；Full 仍需 G5。

**原因**：起草不会影响用户真实行为；呈现行动和运行经营闭环具有更高风险，必须经过工程与用户授权。

## ADR-016：生产迁移与工程开发严格分离

**决策**：V5 分支、测试、插件源和文档可以完成；生产数据库迁移、systemd worker enable、Feature 开启、cron prompt 修改必须在用户明确批准的变更窗口执行。

**原因**：用户要求开发 V5，不等同于授权当前生产事实和主动链路立即切换。
