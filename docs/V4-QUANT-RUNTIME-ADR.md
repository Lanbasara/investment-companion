# ADR：V4 QuantRuntime 选择

状态：**工程选定 NativeQuantRuntime；G2 仍为 No-Go**
日期：2026-08-18

## 决策

V4 首版使用项目自有的窄 `NativeQuantRuntime`，不把 Qlib、FinRL 或 LEAN 引入 Companion Core。该决定冻结接口，不宣称运行效果或 Alpha 已通过。

## 原因

1. 当前首要问题是 A 股 PIT 数据、Universe 和现实规则能否逐日证明，不是模型框架能力；
2. V4 只需要等权、透明排序、target weights、费用与成交约束的可审计最小内核；
3. 内核使用 `Decimal`、canonical JSON 和内容哈希，可与独立参考计算器逐日核对；
4. Qlib 只有在 G1 数据通过后，证明显著减少特征/训练/回测代码且不需要重度 fork 才值得引入；
5. 避免第二套缓存、数据格式和回测语义在资格未定时成为事实来源。

## 已实现契约

- `QuantRuntime.health()` 报告运行时版本和边界；
- 输入只来自冻结、语义验证过的 Dataset Snapshot 分区；
- 信号在 `as_of` 后的下一开放交易日生效；
- target weights 固定完整 denominator、Universe、exclusions 与数据哈希；
- 候选和简单基准使用相同 Snapshot 与 RealitySpec；
- 研究日期由冻结分区、lookback 和调仓频率派生为多期 walk-forward，不接受调用方自报日期；
- Shadow 使用不占调参预算的 `forward_shadow` 运行生成下一开放日 target；
- 100 股整手、现金、费用、滑点、停牌、涨跌停、T+1，以及冻结现金分红/拆并股/退市结算有确定性实现；worker 只选择实验分区日期范围与冻结 denominator 内的事件，并把范围及事件 ID 固化到 ExperimentBundle；内核拒绝仿真 session 外事件，分红税率是 RealitySpec 的显式近似；
- 独立参考计算器核对现金、持仓、成交、费用和 NAV；
- 输出带内容哈希，重复输入必须得到相同结果；
- Native 内核只接收已经物化的 JSON 友好输入；`run_isolated` 计算区间由 Python audit guard 禁止文件、SQLite、网络和子进程，Job 编排器仅在该区间前后读取冻结对象并发布 Manifest；子进程环境会移除凭据变量，且模型 Token 固定为零；
- `health()` 记录 Python 实现/版本、OS、零第三方依赖和 I/O guard 状态。未来若引入 Qlib/其他第三方运行时，仍必须使用独立锁版本环境，不能沿用 Native 的 stdlib 例外。

## G2 为何尚未通过

单元测试只证明代码契约。G2 还缺：官方 golden corpus、真实合格 Snapshot、逐日双计算器差异报告、公司行动案例、生产代码版本证据和 Primary 审批。因此当前可称“QuantRuntime 工程实现完成”，不能称“量化语义已获生产资格”。`observation_days >= 252` 的生产预注册下限也只阻止短样本晋级，不替代这些证据。

## 未来重新评估 Qlib 的触发条件

只有同时满足以下条件才做隔离 Spike：G1=Go；存在至少两年合格数据；原生内核已形成明确基准；需要 walk-forward、因子流水线或模型训练；Qlib 可以在独立环境通过同一 golden cases。无显著净收益则维持 Native 决策，不因开源项目知名度改变架构。

## 后果

优点是边界小、可重放、故障面低，符合个人系统的可维护性。代价是不会自动获得大型研究平台的特征库和训练能力；若以后需求真实出现，再通过既有 QuantRuntime port 替换，业务对象不依赖框架类型。
