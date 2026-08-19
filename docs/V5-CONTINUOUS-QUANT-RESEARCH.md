# V5 持续量化研究系统

状态：生产持续运行；每日形成研究输入；每月严格前向复盘
日期：2026-08-19

## 1. 定位

这不是“先限制功能、等 30 天再决定能不能用”的试用系统。它从启用之日起持续接触真实市场、每天参与投资复盘，并用真实运行记录定期检验自己：

```text
Tushare 官方接口
→ Raw / Canonical 不可变数据
→ 零模型 Token 的确定性扫描
→ 当日收盘复盘与候选研究
→ 独立证据、反证和个人组合约束
→ 必要时形成只供人工判断与执行的 Decision

完整运行记录
→ 每月确定性前向记分
→ Primary 向用户报告有效、无效、未知和修改建议
```

月度复盘是验证任务，不是等待期，也不是功能开关。证据不足时系统继续运行并如实报告，不会自动停用、到期或减少功能。

## 2. 每日产生什么价值

- 17:20 Asia/Shanghai：持续采集当日日线和复权因子；
- 18:10：用冻结规则扫描，记录候选、进入/退出、连续出现次数和下一交易日前向结果；
- 18:30：原有收盘复盘读取最新扫描，把量化雷达与持仓、Thesis、市场事件和机会成本一起解释；
- 候选连续 3 次位列前 5 时产生 `research_shortlist`，可立即触发完整公司研究；不必等到月末；
- 完整研究若满足来源、反证、Thesis、个人约束和 Decision 契约，可以在任何一天形成手工行动建议。

扫描榜单本身不是荐股。单一量价信号只能决定“是否值得投入研究”，不能跳过专业研究直接下结论。

## 3. 冻结的首个量化基线

- 数据：A 股日线、复权因子、上交所交易日历；
- 范围：沪深主板代码基线，不冒充官方 point-in-time 成分股；
- 信号：20 个交易日横截面复权动量，最多 10 个候选；
- 过滤：数据完整、价格底线、近期极端涨跌、波动上限和流动性分位；
- 评价：下一交易日等权收益相对当期合格股票中位数；
- 防穿越：只有在生效日开盘前生成的信号才计入前向结果；
- 资源：确定性 Job 的模型 Token 固定为 0，采集网络只允许白名单 Tushare Handler。

改变回看期、筛选规则、股票池或 Top K 必须发布新版本；不能看到结果后原地改历史。这个基线用于发现线索和衡量流程，不等于已经证明 Alpha、胜率或盈利能力。

## 4. 每月严格前向复盘

Schedule 固定为每月 19 日 19:00（Asia/Shanghai），第一次为 2026-09-19。确定性 Job 会保存不可变 Review Manifest，Primary 必须向用户报告，不得静默。

每次复盘至少核对：

1. 完整扫描次数、数据成功率、失败修复和 Manifest 哈希完整性；
2. 前向观察数量、正超额比例、平均/中位超额、复合相对收益和路径回撤；
3. 候选换手、持续性、唯一候选数以及进入/淘汰完整性；
4. 进入完整研究、被否决、形成 Decision 和最终人工结果的数量；
5. 用户时间、Token、通知负担和相对原人工流程的净价值。

前向观察少于 10 个时仍照常出报告，但结论为“证据不足”，不是暂停功能。数据链故障优先修数据；方向性指标同时为负时建议修改或替换基线；指标混合时调查失效市场；指标为正也只写“有希望、尚未证明”。任何修改从新版本开始，不重写旧结果。

## 5. 永久边界

以下限制不是试用期限制，而是系统长期设计：

- 不连接券商、不自动下单、不自动改变真实持仓；
- 扫描 Handler 不直接创建 Decision、ActionCard、Execution 或 Ledger；
- 正式建议必须经过完整研究、个人组合约束和可审计 Decision；
- 数据尚未通过 G1 时不冒充正式、无缺陷的 point-in-time DatasetSnapshot；
- 不把短样本或回测包装成“高胜率”“稳定盈利”。

用户可随时暂停三个 Schedule 或关闭 `v4_live_data_canary`。停止只影响后续运行，历史 Raw、Manifest、失败和复盘记录继续保留。

## 6. 启用与迁移

新配置没有 `expires_at`、`max_trading_days` 或 Schedule `max_runs`：

```bash
./bin/companion feature-set v4_live_data_canary --enable \
  --config '{
    "mode":"v5_continuous_quant_research",
    "program_id":"<program-id>",
    "user_approval_ref":"<approval-ref>",
    "started_at":"<ISO>",
    "max_requests_per_job":3
  }' \
  --reason '<持续真实运行的用户批准>'

./bin/companion v5-quant-bootstrap --activate
./bin/companion v5-quant-backfill --through-date '<最近已收盘交易日>'
./bin/companion job-work --limit 30
./bin/companion v5-quant-status
```

从旧 `v5_quant_experiment` 配置执行 `v5-quant-bootstrap --activate` 时，会保留 `started_at`、批准记录、Program ID 和全部历史 Manifest，只删除到期时间与运行次数限制，并迁移原 Schedule。旧 CLI/MCP 名称暂时保留为兼容别名。

飞书 Bot 使用 `v5_quant_research_status`、`v5_quant_scan_get` 和 `v5_quant_review_get`。用户正常使用时无需执行命令。
