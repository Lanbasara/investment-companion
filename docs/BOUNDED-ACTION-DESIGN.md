# 分级行动资格与风险预算

状态：实现契约
日期：2026-08-26

## 1. 解决的问题

旧流程把研究分成 `research_only` 和 `eligible_for_decision` 两档，导致尚未达到完整研究标准、但可以用极小风险验证的机会，与数据损坏或没有基本证据的线索一起被禁止。新流程不降低正式行动标准，而是增加独立的受限条件行动通道。

## 2. 三档资格

| 研究资格 | 允许的判断 | 最大风险来源 |
|---|---|---|
| `research_only` | 观察、补研究、淘汰 | 不允许形成行动卡 |
| `eligible_for_bounded_action` | `conditional_action` | 当前确认 Program 的 `risk_budget.bounded_action` |
| `eligible_for_decision` | `action` 或 `conditional_action` | Mandate；条件行动还受 bounded policy 的更小上限 |

`eligible_for_bounded_action` 仍要求：当前且不可变的 Thesis、至少一项冻结且可追溯的非预测证据、时点正确、数据新鲜、反证搜索、明确失效条件、适用范围和成本假设。它只放宽完整行动所需的多源独立印证和预测前向验证，不能容忍身份错误、未来数据、陈旧数据或纯粹未验证信号。

## 3. 风险边界

受限条件行动必须由已确认 Program 显式启用并给出：允许资产类型、允许的执行计划类型、单笔最大组合权重、交易后最大持仓权重、最长有效交易日档位和同时有效的条件行动数量。缺少任何字段时失败关闭。有效期只接受中金财富实际支持的 5/20/60/180 个交易日。所有原有 Mandate、现金、集中度、流动性、行情新鲜度、交易单位和禁止范围规则继续生效；两套限制取更严格者。

首版 bounded policy 明确禁止 `moving_grid`。网格仍可用于完整研究资格的 standard 行动；在累计换手、触发次数和费用预算进入确定性控制前，不允许弱证据通道启动持续网格。

## 4. 职责边界

- Research Validation 只给资格，不给仓位。
- Risk Gate 根据当前组合和已确认政策计算最大可承受行动，不解释 Alpha。
- Decision 比较行动、不行动和替代方案，并声明 `standard` 或 `bounded`。
- Action Queue 只呈现仍然有效的行动卡。
- 券商条件单只执行用户确认的参数，不创造投资理由，也不自动写入成交。

## 5. 失败关闭

系统异常、账本或行情不满足新鲜度、Program 未启用 bounded policy、风险额度超限、研究只含未验证预测、Decision/Context/Ledger 漂移或用户未确认时，均不得生成或继续呈现条件行动卡。
