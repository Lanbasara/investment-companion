# Execution Capability Catalog

状态：信息采集中；用于记录券商实际提供的人工确认式智能交易能力。

本目录描述的是 `Decision -> ManualActionSpec -> 用户确认 -> 券商执行 -> 成交对账` 之间的执行工具箱。券商能力不会绕过研究验证、风险门、用户授权或确认账本，也不等同于推荐或成交。

## 中金财富：定价买入

### 能力结论

定价买入同时支持两个触发方向：

- 回落买入：行情价格下穿或不高于监控价时触发；
- 突破买入：行情价格上穿或不低于监控价时触发。

系统必须显式保存触发方向，不能只保存监控价。

### 已确认参数

- 标的、证券账户；
- 监控价与触发方向；
- 委托价类型：限价或交易所支持的市价类型；
- 限价档位：自定义价格、即时现价、买卖档位；
- 委托数量；
- 有效期：5、20、60 或 180 个交易日；
- 可选监控时段；
- 可选有效触发区间：偏离监控价超过设定比例时不触发；
- 可选延迟确认：按当日行情刷新次数进行连续或累计确认，范围 2 至 20 次。

### 执行语义

- 使用 Level-1 行情，每约 3 秒判断一次；不是逐笔触发；
- 条件满足只代表自动提交委托，不保证委托成功、完全成交或按监控价成交；
- 无档位报价时，券商可能退化为即时现价委托；
- 开收盘、跳空、涨跌停、价格笼子、流动性和账户权限均可能改变结果；
- 信用账户在创建条件单时不完成全部信用风控校验，触发时才校验，可能失败或部分执行。

### 适用执行策略

- 回落至估值或支撑区间后的条件建仓；
- 突破确认后的条件建仓；
- 多张独立条件单组成的有上限分档建仓。

它是网格或分批策略的基础执行原语，但单独不构成完整网格。多张条件单必须共享组合级资金与最大新增仓位上限，并具有跳空保护、撤销条件和到期复核。

### 建议的通用规格字段

```yaml
strategy_type: priced_buy
trigger_direction: cross_down | cross_up
monitor_price: decimal
order_price_policy: custom_limit | current | book_level | market
order_price_detail: object
quantity: integer
valid_for_trading_days: 5 | 20 | 60 | 180
monitoring_window: optional
valid_trigger_band_pct: optional
confirmation_mode: none | consecutive | cumulative
confirmation_ticks: optional_integer_2_to_20
max_incremental_position: required
cancel_conditions: required
post_trigger_reconciliation: required
```

### 信息来源

2026-08-25 用户根据中金财富 App 界面整理的功能说明；具体规则仍以券商交易系统及交易所当时规定为准。账户和资产隐私信息不进入本目录。

## 中金财富：定价卖出

### 能力结论

定价卖出同时支持两个触发方向：

- 上穿卖出：行情价格上穿或不低于监控价时触发，可用于目标价止盈或上涨分档减仓；
- 下穿卖出：行情价格下穿或不高于监控价时触发，可用于止损、破位减仓或风险退出。

它与定价买入共享价格触发、有效期和高级过滤能力，但卖出规格还必须冻结可卖数量、持仓比例和剩余底仓要求。

### 已确认参数

- 标的、证券账户；
- 监控价与触发方向；
- 委托价类型：限价或交易所支持的市价类型；
- 限价档位：自定义价格、即时现价、买卖档位；
- 委托数量，或全仓、二分之一仓、三分之一仓、四分之一仓；
- 有效期：5、20、60 或 180 个交易日；
- 可选监控时段；
- 可选有效触发区间；
- 可选连续或累计延迟确认，范围 2 至 20 次。

### 执行语义与风险

- 使用 Level-1 行情，每约 3 秒判断一次；条件满足只代表提交委托；
- 开收盘、跳空、涨跌停、价格笼子、流动性和账户权限可能导致价差、部分成交或失败；
- 下穿止损若使用限价委托，价格继续快速下跌时可能无法成交；
- 下穿止损若启用过窄的有效触发区间，跳空越过区间时可能完全不触发。该功能对回落买入是接飞刀保护，对风险退出却可能与“尽快卖出”目标冲突；
- 延迟确认可以减少瞬时误触发，但也会延迟止损。延迟次数必须根据策略目的选择，不能统一默认开启；
- 比例卖出必须在触发前以已人工对账持仓换算，并在用户修改持仓或另一条件单成交后重新核验，避免多张卖单争用同一持仓。

### 适用执行策略

- 目标价止盈与上涨分档减仓；
- 跌破失效位后的止损或减仓；
- 趋势破位后的条件退出；
- 与多档定价买入组合成有边界网格。

### 建议的通用规格字段

```yaml
strategy_type: priced_sell
trigger_direction: cross_down | cross_up
monitor_price: decimal
order_price_policy: custom_limit | current | book_level | market
order_price_detail: object
quantity_mode: fixed | full | half | one_third | one_quarter
quantity: optional_integer
minimum_remaining_position: required
valid_for_trading_days: 5 | 20 | 60 | 180
monitoring_window: optional
valid_trigger_band_pct: optional
confirmation_mode: none | consecutive | cumulative
confirmation_ticks: optional_integer_2_to_20
cancel_conditions: required
post_trigger_reconciliation: required
```

### 信息来源

2026-08-25 用户根据中金财富 App 的定价卖出说明页与下单页逐字转录；具体规则仍以券商交易系统及交易所当时规定为准。账户和资产隐私信息不进入本目录。

## 中金财富：止盈止损

### 能力结论

止盈止损以用户指定的基准价为中心，在同一条件单内设置一条上穿止盈线和一条下穿止损线：

- 止盈：价格相对基准价上涨达到指定比例或价格时卖出；
- 止损：价格相对基准价下跌达到指定比例或价格时卖出。

它用于表达成对退出计划，比两张互不关联的定价卖出单更接近一个完整持仓管理策略。但现有材料没有明确说明任一侧触发、委托或成交后，另一侧何时自动失效；该行为必须在正式建模前核实，不能自行假定为交易系统中的 OCO。

### 已确认参数

- 标的、证券账户；
- 基准价；
- 止盈价格或相对基准价的上涨比例；
- 止损价格或相对基准价的下跌比例；
- 委托价类型与档位；
- 委托数量，或全仓、二分之一仓、三分之一仓、四分之一仓；
- 有效期：5、20、60 或 180 个交易日；
- 可选监控时段、有效触发区间；
- 止盈和止损各自独立计数的连续或累计延迟确认，范围 2 至 20 次。

### 执行语义与风险

- 基准价是策略参数，不应默认等于最新价、持仓成本或成交价；系统必须记录其来源和选择理由；
- 使用 Level-1 行情，每约 3 秒判断一次；触发只代表提交委托；
- 止盈与止损共用还是分别使用委托数量、委托价策略，现有材料未完全明确；
- 任一侧触发后的对侧撤销时点、委托失败后的对侧状态、部分成交后的剩余数量处理均待核实；
- 止损使用限价可能追不上快速下跌；止损使用市价或对手价可能产生较大滑点；
- 对止损开启延迟确认或过窄有效触发区间，可能造成风险退出延迟或跳空后不触发；
- 条件单存在期间若持仓因人工交易、其他条件单或公司行动变化，原数量和比例必须重新核验。

### 适用执行策略

- 建仓后同时冻结盈利目标与最大可接受损失；
- 波段仓位的成对退出计划；
- ETF 或股票单次交易的风险收益比控制；
- 分批仓位中的一批独立止盈止损。

它不能替代组合级回撤控制，也不能证明止盈止损比例具有统计优势。参数应来自正式 Decision 的 Thesis、波动、流动性、持有期与风险预算，不能统一套用固定百分比。

### 建议的通用规格字段

```yaml
strategy_type: bracket_exit
reference_price: decimal
reference_price_basis: entry | average_cost | market_snapshot | thesis_level | custom
take_profit:
  price: decimal
  return_pct: decimal
  confirmation_mode: none | consecutive | cumulative
  confirmation_ticks: optional_integer_2_to_20
stop_loss:
  price: decimal
  return_pct: decimal
  confirmation_mode: none | consecutive | cumulative
  confirmation_ticks: optional_integer_2_to_20
order_price_policy: custom_limit | current | book_level | market
quantity_mode: fixed | full | half | one_third | one_quarter
quantity: optional_integer
minimum_remaining_position: required
valid_for_trading_days: 5 | 20 | 60 | 180
monitoring_window: optional
valid_trigger_band_pct: optional
sibling_condition_policy: broker_behavior_to_verify
partial_fill_policy: broker_behavior_to_verify
cancel_conditions: required
post_trigger_reconciliation: required
```

### 待核实问题

1. 止盈或止损任一侧触发后，另一侧是在触发、委托成功、完全成交还是其他时点失效？
2. 触发委托失败或仅部分成交时，条件单及另一侧条件如何处理？
3. 止盈和止损能否分别设置不同的委托价策略和卖出数量？
4. 人工减仓或其他条件单成交后，券商是否自动按剩余可卖持仓调整数量？

### 信息来源

2026-08-25 用户根据中金财富 App 止盈止损功能截图整理的说明；具体规则仍以券商交易系统及交易所当时规定为准。账户和资产隐私信息不进入本目录。

## 中金财富：网格单

### 能力结论

网格单是在用户指定价格区间内运行的状态化双向执行策略。它以动态基准价为中心：价格上涨一格触发卖出并向上移动基准，价格下跌一格触发买入并向下移动基准。它支持固定差价或百分比网格、独立买卖数量、净头寸边界、跨多格补量、区间外休眠或终止清仓。

它不是研究信号，也不自动证明标的处于震荡状态。系统只有在正式 Decision 已确认“标的适合网格、区间和失效条件成立”后，才能生成网格执行规格。

### 已确认参数

- 标的、证券账户；
- 初始基准价；
- 网格类型：固定差价或百分比；
- 每上涨一格的幅度与卖出委托价策略、每笔卖出数量；
- 每下跌一格的幅度与买入委托价策略、每笔买入数量；
- 运行价格下限和上限；
- 区间外处理：按方向休眠，或终止策略并清仓策略净买量；
- 最大净买量、最大净卖量；
- 可选倍数委托：一次行情跨越多格时按跨越格数放大委托量；
- 可选监控时段；
- 有效期：5、20、60 或 180 个交易日。

### 状态与边界

- 买方向在价格越界、达到最大净买量或资金不足时休眠；条件恢复后可自动退出休眠；
- 卖方向在价格越界、达到最大净卖量或持仓不足时休眠；条件恢复后可自动退出休眠；
- 终止清仓模式只清仓网格策略的净买量，不应默认等同于账户全部原始持仓；
- 因资金或持仓不足导致废单时，基准价不更新；
- 使用 Level-1 行情，每约 3 秒判断一次，跨格行情可选择倍数委托。

### 适用范围

优先适用于流动性好、价差低、跟踪稳定、投资者愿意持有且有合理震荡假设的 ETF。消息面和基本面用于判断市场状态及是否暂停策略，价格、波动率、成交与流动性用于估计网格区间和间距。

不适用于：

- 已进入持续单边下跌或投资逻辑失效的标的；
- 流动性差、买卖价差大或频繁溢价的 ETF；
- 没有总资金、最大净买量或下方退出边界的无限补仓；
- 网格间距无法覆盖手续费、滑点、价差和机会成本的情况；
- 依赖逐笔成交或亚秒级反应的策略。

### 主要风险与设计要求

- 网格盈利来自区间内反复波动；遇到单边上涨会不断卖出并损失趋势收益，遇到单边下跌会不断买入并积累亏损；
- 倍数委托会在跳空时突然放大交易数量，默认应关闭，只有组合级风险门计算最坏跨格数量后才可启用；
- “区间外休眠”只停止继续交易，不处置已经积累的风险仓位；“终止并清仓”可能在急跌时以较差价格退出；
- 网格必须设置最大净买量、最大净卖量、资金预算、底仓边界、运行区间、有效期与市场状态失效条件；
- 多个策略或人工订单共享同一账户资金与持仓时，券商本地休眠不能替代组合级资金预留和冲突检查；
- 评价网格必须与简单持有、一次性买入和不行动比较，并计算净收益、最大回撤、交易成本、占用资金和用户维护时间。

### 建议的通用规格字段

```yaml
strategy_type: moving_grid
initial_reference_price: decimal
grid_spacing_type: absolute | percentage
up_grid_spacing: decimal
down_grid_spacing: decimal
sell_order_price_policy: current | book_level | custom_limit | market
sell_quantity_per_grid: integer
buy_order_price_policy: current | book_level | custom_limit | market
buy_quantity_per_grid: integer
price_range:
  lower: decimal
  upper: decimal
out_of_range_policy: directional_sleep | terminate_and_liquidate_strategy_net_buy
max_net_buy_quantity: required_integer
max_net_sell_quantity: required_integer
multiple_grid_ordering: false_by_default
maximum_crossed_grids_per_tick: required_if_enabled
capital_budget: required
minimum_core_position: required
valid_for_trading_days: 5 | 20 | 60 | 180
monitoring_window: optional
regime_assumption: required
regime_invalidation_conditions: required
cancel_conditions: required
post_trigger_reconciliation: required
```

### 待核实问题

1. 区间外“终止并清仓”失败后的策略状态、剩余数量和重试规则是什么？中金财富客服目前无法确认。

### 网络调研补充

中金财富官网公开网页、官方 PDF 和可检索官方内容中尚未找到证券 App 网格单完整细则。2026-08-25 找到一份与 App 术语和功能高度一致的条件单供应商帮助页，可作为供应商级旁证，但不能冒充中金官方规则：

- 页面明确第二次运行开始使用本次触发价作为新基准，支持“基准价按触发移动”，而非等待完全成交；
- 偏差保护导致不发出交易指令时，多次条件单仍继续监控，且基准价会按触发失败价格变化；
- 用户提供的中金 App 规则同时明确：资金或持仓不足导致废单时，基准价不更新。由此可知失败原因不同，基准价处理也不同；
- 动态触发价成为下一基准，因此百分比网格按新基准逐格计算，数学上形成复合网格；
- 供应商公开页没有覆盖普通未成交、部分成交、主动撤单及其他废单；以下中金财富客服答复进一步补齐了这些分支的基准价规则。

2026-08-25 用户进一步向中金财富客服核实：

- 只有持仓不足和资金不足两种废单不会更新基准价，其他情况均会更新基准价；
- 净买量包括该条件单产生的买入成交、买入挂单、卖出成交和卖出挂单，公式为 `(执行买入量 - 撤买入量) - (执行卖出量 - 撤卖出量)`。
- 终止网格单不会同步撤销已经触发但尚未成交的委托；用户必须另行检查并决定是否撤单；
- 中金财富客服后续电话更正：ETF 分红后网格单会自动终止，不会沿用旧参数继续运行；
- ETF 分红以外的拆分、合并或其他公司行动如何处理，尚未取得同等明确的券商答复；
- “终止并清仓”失败后的处理规则，客服目前无法确认。

因此中金网格应正式建模为触发驱动而非成交驱动。普通未成交、部分成交、主动撤单或其他异常不能通过“基准价是否移动”反推出成交事实。系统必须分别保存触发、活动委托、撤单、部分成交和完全成交；策略收益只能使用人工确认的真实成交与费用计算。

终止网格必须建模为一个多步骤人工操作，而不是单一状态切换：暂停或终止策略、查询未成交委托、逐笔撤销不再需要的委托、确认撤单结果、核对实际成交与净买量、再决定是否处置剩余持仓。只要仍有活动委托，就不得把策略标记为完全关闭。

ETF 分红是强制复核事件。中金会自动终止网格，但终止不代表已触发挂单自动撤销；系统仍须查询活动委托、撤单和成交，完成持仓对账，再重新计算基准价、价格区间、网格间距、委托数量和头寸边界。若仍适合网格，应创建并由用户确认一张新策略，不能把旧策略直接恢复。拆分、合并或其他公司行动在取得明确规则前采用同样的保守复核流程。

供应商旁证：<https://cdn01.touker.com/hbec/projects/qq/production/conditionH5/explainH5/grid.html>

### 信息来源

2026-08-25 用户根据中金财富 App 网格单功能截图整理的说明；具体规则仍以券商交易系统及交易所当时规定为准。账户和资产隐私信息不进入本目录。
