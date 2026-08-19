# V5 受控量化实验

状态：真实世界试运行；不构成策略放行或交易建议
日期：2026-08-19

## 1. 目的

这条链路解决一个具体问题：不能等所有长期 Gate 都完成后才第一次接触真实数据，也不能让 AI 每天随意搜网页后交一份股票名单。

因此 V5 在 G0 下运行一个限时实验：

```text
Tushare 官方接口
→ Raw 与 Canonical 内容寻址对象
→ 零模型 Token 的确定性筛选
→ 不可变 Scan Manifest
→ research_ready
→ Primary Codex 决定静默、继续研究或登记 observed Opportunity
```

它验证数据连续性、任务可靠性、候选稳定性、前向结果和 Token 净价值。它不验证“高胜率”或“可以盈利”。

## 2. 冻结的首个基线

- 数据：A 股日线、复权因子、上交所交易日历；
- 范围：沪深主板代码基线，不冒充官方 PIT 股票池；
- 信号：20 个交易日横截面复权动量；
- 风险过滤：21 日数据完整、价格底线、近期极端涨跌、波动上限、流动性分位；
- 输出：最多 10 个待研究线索和等权研究 Target；
- 评价：下一交易日等权收益相对当期合格股票中位数，只作短样本过程观测；
- 资源：Job 内模型 Token 固定为 0，网络只允许白名单 Tushare Handler。

参数冻结在代码版本中。改变回看期、筛选规则或 Top K 必须发布新版本，不能在结果出来后原地调参。

## 3. 硬边界

本实验不会：

- 发布 G1 合格 DatasetSnapshot 或晋级 StrategyVersion；
- 创建 Shadow Book、Decision、ActionCard、Execution 或 Ledger Entry；
- 连接券商、自动下单或自动改变真实持仓；
- 把候选榜单批量机械转成 Opportunity；
- 把 10–20 日短样本描述为 Alpha、高胜率或盈利能力。

只有 Primary 对某个线索形成了明确研究问题，核验官方来源和反证，并确认没有重复项后，才可登记一个 `observed` Opportunity。之后仍走完整 V5 漏斗。

## 4. 运行节奏与停止

- 17:20 Asia/Shanghai：采集当日日线和复权因子；
- 18:10：运行确定性扫描；
- Worker 每 5 分钟领取最多两个 Job；
- Trial 只允许 10–20 个交易日，Feature 配置必须含用户批准、开始和到期时间；
- Schedule 同时受 `expires_at` 与 `max_runs` 限制；到期后自动变为 `expired`；
- 数据异常、候选明显变化或第 5/10/20 个前向观察点才唤醒 Primary，普通无变化不烧模型 Token。

立即停止方式：暂停两个实验 Schedule、暂停两个 JobDefinition，并关闭 `v4_live_data_canary`。已发布 Raw、Manifest 和失败记录保留审计，不删除历史。

## 5. 首次启动

生产 G0 必须绑定当前固定 Runtime。用户明确批准后：

```bash
./bin/companion feature-set v4_jobs --enable \
  --config '{}' \
  --reason '<批准记录和边界>'

./bin/companion feature-set v4_live_data_canary --enable \
  --config '{
    "mode":"v5_quant_experiment",
    "trial_id":"<trial-id>",
    "user_approval_ref":"<approval-ref>",
    "started_at":"<ISO>",
    "expires_at":"<ISO>",
    "max_trading_days":20,
    "max_requests_per_job":3
  }' \
  --reason '<真实数据受控试运行>'

./bin/companion v5-experiment-bootstrap --activate
./bin/companion v5-experiment-backfill --through-date '<最近已收盘交易日>'
./bin/companion job-work --limit 1
./bin/companion v5-experiment-backfill --through-date '<最近已收盘交易日>'
./bin/companion job-work --limit 30
```

第一次 backfill 会先建立交易日历；第二次只为缺失的 21 个开放日排入日线/复权任务。所有步骤幂等，失败日期会以新 repair attempt 补采，不覆盖旧失败。

最后立即运行“机会扫描”Schedule，并用以下入口验收：

```bash
./bin/companion v5-experiment-status
./bin/companion v4-status
systemctl --user status companion-job-worker.timer --no-pager
```

飞书 Bot 使用 `v5_quant_experiment_status` 和 `v5_quant_scan_get`，用户无需执行这些命令。

## 6. 试运行验收

工程验收：测试全通过、生产数据库完整、Worker/恢复/幂等正常、Job 零模型 Token、真实 Ledger/Decision/Opportunity 计数不被 Handler 自动改变。

真实验收至少观察：

1. 10–20 个实际交易日的数据成功率、延迟、空集和修复次数；
2. 候选变化率、重复唤醒数和 Primary 实际消耗的 Token；
3. 前向观察相对简单基线的方向、回撤和样本完整性；
4. 进入研究、被否决和被忽略的线索数量；
5. 用户是否节省时间，是否出现误导性行动冲动。

样本结束后的合法结论只有：继续采证、修改后重开新版本、停止该基线。不得自动晋级正式策略。
