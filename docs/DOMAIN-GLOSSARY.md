# Investment Companion：领域词典

状态：目标架构统一语言
日期：2026-08-23

这份词典用于减少版本迭代产生的重复概念。新代码、文档和 Codex 工具优先使用下列版本无关名称；旧数据库对象在兼容期内保留原名。

## 1. 用户需要理解的十个投资概念

| 概念 | 精确定义 | 当前对象迁移来源 |
|---|---|---|
| Investor Profile | 用户确认的人生目标、流动性和个人事实 | Investor Context Revision |
| Investment Mandate | 用户确认的不可越过硬约束 | Mandate Context Revision |
| Investment Policy | 当前投资目标、基准、范围、风险预算、节奏和停止条件 | InvestmentProgram + 当前 Context 引用 |
| Portfolio Ledger | 经确认的现金、持仓、成交、费用和公司行动事实 | Account、Asset、confirmed Ledger Entry |
| Research Case | 一个有边界、可结束、可证伪的研究问题及其证据进度 | Case + Opportunity 的用户语义投影 |
| Investment Thesis | 对标的或投资方法为何可能有效、何时失效的版本化判断 | Thesis Revision、ResearchHypothesis |
| Strategy Version | 可重放的信号、组合、成本、适用范围和验证条件 | StrategyVersion、预测模型标识 |
| Investment Decision | 基于当时证据、真实组合和替代方案形成的有期限正式判断 | Decision Revision |
| Action Card | 聚合当前 Research Validation、Risk Gate、行动参数和替代方案，等待用户接受、拒绝、延后或补证据的建议投影 | DecisionQueue + Decision；V4 兼容路径另含 ManualActionSpec |
| Execution & Review | 已接受意图、用户报告订单、待确认/确认成交、成交偏离、客观绩效和后续修订提案 | Execution、confirmed Ledger、Calculation、Review Revision |

Action Card 被接受只表示用户认可建议，不表示已经下单或成交。
Execution 的 `ordered` 只表示用户报告已向券商下单；`partially_filled / filled / deviated` 必须引用已确认 Ledger Entry。

## 2. 用户不需要日常管理的平台概念

| 概念 | 作用 | 边界 |
|---|---|---|
| Market Calendar | 统一交易日、场次、截止时间和评价到期日 | 不决定买卖 |
| Schedule / Run | 说明何时检查以及一次检查是否完成 | Run 成功不等于结果正确或已送达 |
| Data Object / Manifest | 保存不可变输入、输出和血缘 | 不是推荐 |
| Gate / Feature | 控制能力是否满足工程和证据门槛 | 不替代投资判断 |
| Delivery Record | 证明用户可读结果是否真正送达 | 不保存投资真相 |

## 3. 明确合并或停止扩张的旧概念

| 旧概念 | 处理方式 |
|---|---|
| V5 Quant Experiment / Continuous Quant Research | 统一迁移到 Research Pipeline；“持续”是运行策略，不是新领域 |
| V6 Predictive Recommendation | 迁移为 Strategy Version 产生的 Forecast 与 Evaluation，不再作为独立产品层 |
| Opportunity 与调查 Case | 底层历史保留；默认 Codex 使用统一 Research Case 上下文 |
| DecisionQueue 与 ManualActionSpec | 保留职责差异，由一个 Action Card 聚合呈现 |
| OperatingBrief 与 ResultEnvelope | 前者是投资摘要，后者是交付载荷；不再向用户暴露两个名词 |

## 4. 禁止使用的模糊词

- “系统学习了”：必须说明是新增 Review、Change Proposal、Thesis Revision 还是 Strategy Version。
- “已经执行”：必须区分建议已接受、Execution 已创建、用户已下单和 Ledger 已确认。
- “已经验证”：必须说明数据、样本外、Shadow、实盘或结果评价中的哪一级。
- “有资格进入决策”：只表示冻结来源、时点、反证、范围、成本或策略样本达到声明门槛；不表示预测必然正确。定性 Thesis 使用 `research_only / eligible_for_bounded_action / eligible_for_decision`；量化 Strategy 另有 `eligible_for_shadow` 中间态。
- “系统盈利”：必须给出账户范围、期间、现金流处理、成本、基准和 Calculation/Performance ID。
- “无变化”：必须说明来源覆盖已经通过，否则只能说覆盖不足。
