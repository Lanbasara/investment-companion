# V4 数据资格报告 v1

状态：**G1 No-Go（工程 Adapter 已完成，真实资格尚未证明）**

评审日期：2026-08-18
适用范围：中国 A 股、日频、人工执行的个人研究系统

## 1. 结论

Tushare 可以成为 V4 首批结构化数据供应商，但“购买 5000 积分”不能单独使数据达到专业研究资格。官方权限表明确区分积分接口与单独购买的分钟、实时、新闻、公告等能力；每个端点仍须用当前账号实测并完成许可审查。

当前只批准以下工程结论：

1. 已实现官方 HTTP、白名单端点、凭据隔离、Raw 保留、Canonical 规范化、语义校验和 canary 批次；
2. 若真实探针、官方案例和 10–20 个交易日 canary 全部通过，可研究 **2017 年以后、日频、价格/成交/可交易性驱动** 的候选策略；
3. 2026-08-18 已在隔离临时 Schema 4 中用当前账号对 11 个白名单端点完成单次历史样本烟测，全部返回 `healthy`；`daily/2024-08-16` 还完成一批 Raw→Canonical→semantic validation，5339 行、状态 `ready`；这些临时证据不进入生产 Gate；
4. 确定性量化内核已能回放现金分红、拆并股和退市事件，并把事件 ID 固化到 Experiment Bundle；这只证明回放能力，不证明 Tushare 公司行动事实已经具备生产资格；
5. 历史基本面、公告文本、分钟/实时和全历史全市场策略当前均不合格；
6. 合格数据只允许进入 Snapshot 和研究，不自动产生买卖建议；未完成的数据项状态是 `unknown`，不得按“通常应该有”推定为可用。

## 2. 为什么 5000 积分不等于完整数据

[Tushare 权限总表](https://tushare.pro/document/2?doc_id=290)显示，5000 积分对应常规数据较高频率与通常无日总量限制；历史分钟、实时行情、新闻和上市公司公告属于独立权限。价格、权限与频次是会变化的外部事实，采购前必须重新核对官方页面并用目标账号 probe。

已确认的端点文档边界包括：

- [股票列表 `stock_basic`](https://tushare.pro/document/2?doc_id=25)：2000 积分起，含当前上市状态、上市日和退市日，但它本身不是逐日历史股票池；
- [股票历史列表 `bak_basic`](https://tushare.pro/document/2?doc_id=262)：5000 积分，历史从 2016 年开始；
- [历史 ST 列表 `stock_st`](https://tushare.pro/document/2?doc_id=397)：3000 积分，数据从 2017-01-01 开始；
- [A 股日线 `daily`](https://tushare.pro/document/2?doc_id=27)与[复权因子 `adj_factor`](https://tushare.pro/document/2?doc_id=28)：可提供历史价格与复权基础，但停牌日不产生普通日线；
- [每日指标 `daily_basic`](https://tushare.pro/document/2?doc_id=32)：2000 积分起，5000 积分无总量限制；字段可用于当日横截面，但不能自动视为有可重放的历史修订版本；
- [涨跌停价格 `stk_limit`](https://tushare.pro/document/2?doc_id=183)、[停复牌 `suspend_d`](https://tushare.pro/document/2?doc_id=214)、[指数权重 `index_weight`](https://tushare.pro/document/2?doc_id=96)和[分红送股 `dividend`](https://tushare.pro/document/2?doc_id=103)可支撑部分现实规则，但仍需核对覆盖、发布时间、修订和空集语义。

因此，V4 的可证明统一历史起点不能早于最短板。以当前候选端点组合推断，初始研究窗口保守设为 **2017-01-01 以后**；这只是待验证候选边界，不是 G1 通过结论。

## 3. Capability Matrix

| 能力 | Adapter | 文档资格 | 真实账号 probe | 历史/PIT 资格 | 当前结论 |
|---|---:|---:|---:|---:|---|
| `stock_basic` | 已实现 | 已核对 | 临时 smoke healthy | 仅静态身份/状态 | Canary 候选 |
| `trade_cal` | 已实现 | 待逐字段核对 | 临时 smoke healthy | 需与交易所案例比对 | Canary 候选 |
| `daily` | 已实现 | 已核对 | 临时 smoke healthy | 需停牌/缺失/复权案例 | Canary 候选 |
| `adj_factor` | 已实现 | 已核对 | 临时 smoke healthy | 需公司行动重算 | Canary 候选 |
| `daily_basic` | 已实现 | 已核对 | 临时 smoke healthy | 修订/可得时点未证明 | 限前向观察 |
| `bak_basic` | 已实现 | 已核对 | 临时 smoke healthy | 2016 年起 | Canary 候选 |
| `stock_st` | 已实现 | 已核对 | 临时 smoke healthy | 2017 年起 | Canary 候选 |
| `suspend_d` | 已实现 | 部分核对 | 临时 smoke healthy | 更新不定期 | No-Go 至验证 |
| `stk_limit` | 已实现 | 已核对 | 临时 smoke healthy | 需板块/规则变更案例 | Canary 候选 |
| `index_weight` | 已实现 | 已核对 | 临时 smoke healthy | 月度值不等于公告可得时点 | No-Go 至验证 |
| `dividend` | 已实现 | 已核对 | 临时 smoke healthy | 需公告日/实施日/修订 | No-Go 至验证 |
| 财务报表/指标 | 未接入 | 未完成 | 未完成 | first-known/revision 未证明 | No-Go |
| 公告原文 | 未接入 | 独立付费 | 未完成 | 需交易所/发行人权威链 | No-Go |
| 分钟/实时 | 未接入 | 独立付费 | 未完成 | V4.0 不需要 | 不采购 |

烟测使用 2024-08-16 日频样本（指数权重使用 2024-08 月、分红使用单标的）。实际 `daily` canary 保存 1 个 Raw 对象、1 个 Canonical 对象并通过内置 daily-bars 语义校验。Raw 和 capability report 只保存在 `/tmp` 隔离数据根，不提交 Git、也不签发 production assessment。“Adapter 已实现/临时 healthy”只表示当前账号能完成该请求，不表示连续覆盖、许可或 PIT 已合格。

公司行动 Snapshot 的规范化、语义校验和确定性回放已完成工程实现。`dividend` Adapter 只把官方接口中 `div_proc=实施` 且有 `ex_date` 的行映射为事件：税前 `cash_div_tax` 映射现金分红，`1 + stk_div` 映射拆并股比例；预案仍只保留在 Raw 中，不能进入仿真。生产数据仍必须用交易所/发行人材料验证除权除息日、现金金额、拆并股比例、退市日、修订与合法空集。上述 golden cases 未完成前，公司行动能力不得成为 G1 的通过依据。

`tushare-daily/2` 暂把日线的 `first_known_at` 设为交易日 **16:00 Asia/Shanghai**。这是避免把收盘数据错误视为盘中已知的保守工程约定，不是供应商延迟事实：采集若早于该时点会因 `known <= ingested` 失败；研究还要求每条记录在冻结交易日历的下一开盘日 09:30 前已知。G1 必须用连续 canary 和官方案例校准真实发布时间；若证据不同，必须升级 parser 版本并重建 Snapshot，禁止原地改写历史。

## 4. G1 必须补齐的证据

1. 在生产资格工作区重复 11 个端点 probe，保留可复核的 capability report、Raw 对象哈希和账号 scope；
2. 连续 10–20 个交易日运行单流 canary，确认延迟、合法空集、分页、重复、断点和修订；
3. 用交易所、指数公司、发行人原始材料人工建立 20–50 个 official golden cases；
4. 对 Raw 保留、个人研究、备份和禁止再分发做书面许可审查；
5. 由 Primary Codex 对所有未知项和反证作生产 Gate 评审，用户以明确 approval ref 批准。

仓库中的 `tests/fixtures/v4/golden/candidate-corpus.json` 只是案例选择清单，不是官方事实，也不能用于签发 G1。

## 5. 通过后的首批研究范围

首批 Snapshot 仅允许：A 股日线、交易日历、逐日可研究股票池、复权因子、停复牌、涨跌停和经验证的公司行动。策略只允许透明的等权、动量/流动性排序和简单可投资基准；统一使用次一交易日生效、整手、现金、费用、滑点、停牌和涨跌停规则。

下列声明继续禁止：

- “高胜率”“稳定盈利”或任何收益保证；
- 使用当前成分/ST/退市状态回填历史；
- 无 first-known/revision 证据的历史基本面 Alpha；
- 把 Tushare 字段、新闻摘要或双引擎一致当作官方 golden truth；
- 用回测替代至少 90 个日历日且满足样本量的前向 Shadow。

## 6. 采购决策

当前不建议为 V4.0 购买分钟、实时、新闻或公告套餐。先用现有账号完成常规端点 probe；若权限不足，只购买通过策略需求反推出来的最小能力。公告研究若进入后续版本，应优先建立交易所/发行人原文链，Tushare 只作为发现与结构化入口。
