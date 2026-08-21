# Investment Companion：项目状态与会话交棒

更新时间：2026-08-21

当前发布目标：V7 / SQLite Schema 7 / 固定 Git Runtime（代码与回归已完成；生产迁移须按 V7 手册执行）

当前分支：`feature/v5-investment-operating-system`

V4 冻结：annotated tag `v4.0.0-engineering-baseline`

当前 Plugin：`0.1.0+codex.20260819040624`，新 Skill 在新会话加载

## 1. 当前结论

V5 是：**Codex 驱动、确定性证据支撑、飞书协作、人工执行、可审计并能用真实结果持续自我否证的个人投资研究与决策系统**。

V6 在保留 Schema 5、V5 operating layer、version-neutral wake 和 active InvestmentProgram 的基础上，追加股票与 ETF 的独立预测、推荐、结果结算与周期复核。V7 继续保留全部历史对象，并新增独立的结果交付账本：Run 成功不等于用户已经收到结果。

持续量化研究已经改为真实世界长期运行：Tushare 真实数据 → 内容寻址对象 → 零模型 Token 的确定性扫描 → 当日收盘复盘/完整研究 → 每月严格前向复盘。它没有试用到期或运行次数上限，也不是自动交易或盈利承诺。

## 2. 新会话从这里开始

```bash
cd /home/ghk/investment-home
git status -sb
./bin/companion doctor
./bin/companion v5-status
./bin/companion v5-quant-status
systemctl --user status companion-job-worker.timer --no-pager
codex plugin list
```

阅读顺序：

1. 本文：当前生产事实和边界；
2. [V5-DESIGN.md](V5-DESIGN.md) 与 [V5-USER-GUIDE.md](V5-USER-GUIDE.md)：系统为什么存在、用户怎么用；
3. [V7-RESULT-DELIVERY.md](V7-RESULT-DELIVERY.md)：结果交付契约、Attention 边界与上线步骤；
4. [V5-ARCHITECTURE-DECISIONS.md](V5-ARCHITECTURE-DECISIONS.md) 与 [V5-ACCEPTANCE.md](V5-ACCEPTANCE.md)：对象边界和验收；
5. [V5-OPERATIONS.md](V5-OPERATIONS.md)：生产恢复、Feature 和 Worker 运维。

不要从旧聊天、`memory/*.md` 或 `portfolio/current.md` 推断真实持仓和当前任务；使用 Companion 工具。

## 3. 当前架构真相

```text
飞书 → cc-connect → Primary Investment Codex
                   ├─ V5 Program / Opportunity / Decision / Lifecycle
                   ├─ 只读专业 Agents
                   └─ 持续量化研究交接

systemd Tick → Schedule / Run / DeliveryRecord / Outbox → version-neutral wake
systemd Worker → allow-listed deterministic Job → immutable Manifest

DeliveryRecord → immutable ResultEnvelope → cc-connect direct result / close digest → delivery receipt

Tushare research stream → Raw / Canonical → deterministic scan
                        → 当日复盘 / 完整研究
                        → 月度严格前向 Review
```

Primary Codex 仍是唯一最终语义判断、正式发布和用户沟通主体。Companion 不包裹 Codex，不连接券商，不自动交易。

## 4. 已实现并验证

- Schema 7 有序迁移、备份恢复、完整性检查和固定 Runtime；
- Program → Opportunity → DecisionQueue → 人工成交 → Review/Scorecard 经营闭环；
- `v5_today` 四态入口和 version-neutral wake claim/complete；
- 原 V2/V3/V4 Schedule、Run、Event、Watch、Case、Ledger、Context 和 Job 兼容；
- Tushare 日线/复权因子/交易日历的冻结 Canary 契约；
- 21 日真实数据回填、确定性横截面扫描和下一交易日前向观测；
- 持续运行、单 Job 请求预算、失败修复和旧试用配置无损迁移；
- 候选持续性研究触发与每月确定性前向复盘；
- 扫描 Handler 不创建 Opportunity、Decision、Shadow、Execution 或 Ledger Entry；
- MCP 7.0.0 的 DeliveryRecord、ResultEnvelope、摘要批量发送、恢复和 Policy 映射入口；
- 全量自动化测试、Agent 检查与 Plugin 校验。

最终测试数量和 release commit 以 Git 历史和最新验证报告为准，不从本文猜。

## 5. 生产运行边界

- `.state/runtime-code-root` 是当前固定 Runtime 的权威指针；
- 生产数据库是 `/home/ghk/investment-home/.state/companion.db`；上线前为 Schema 6，执行 V7 迁移后为 Schema 7；
- `v5_operating_system`、确定性 Job 能力、持续研究和 `v6_predictive_recommendations` 可独立启用；
- `v4_live_data`、Shadow、Decision Support 与自动交易保持关闭；
- Job Worker 每 5 分钟最多执行两个白名单任务，模型 Token 为 0；
- 量化研究 Schedule 不设到期或最大次数；用户可随时明确暂停；
- 旧主动任务继续原样运行，不批量重建；
- 用户升级后只需在飞书 `/new`，无需重置项目或数据库。

具体账户、持仓、金额、对象 ID、任务数量和最新扫描只能现场读取，不在本文复制。

## 6. 持续量化研究的合法输出

系统每天可以输出：数据健康、不可变候选清单、规则解释、输入哈希、前向过程观测、候选持续性和完整研究触发；每月输出严格前向复盘。

扫描 Handler 不能直接输出自动买卖、正式 Decision、行动卡或真实持仓变更。Primary 可在任何一天把持续候选推进为完整研究；完整研究满足来源、反证、个人约束和 Decision 契约后，可以形成只供人工执行的建议，不必等待月度复盘。

月度复盘的结论是继续、深化研究、调查失效环境、发布修改版或停止该基线；月度节点不限制日常功能。

## 7. 尚待真实世界完成

- Tushare 数据连续性、延迟、失败率和修复成本；
- 候选变化率、误报、漏报、重复唤醒和 Token 净成本；
- 持续积累完整前向观测，并按月公开证据是否足够；
- 官方 golden cases、研究完整性和 Shadow 的 G2–G4 证据；
- 决策 Beta 的真实飞书使用证据 G5；
- 足够时间、样本、成本和组合结果后的 G6 价值评审。

没有 strategy-eligible 策略，不声明 Alpha、高胜率或稳定盈利。

## 8. 变更与恢复原则

1. 代码变更先通过全量测试，再固定为独立 Runtime；
2. 每个生产 commit 重新生成并评估 G0，不沿用旧提交的放行；
3. Feature、Plugin、systemd 和 cc-connect 变更需要用户明确授权；V7 的生产迁移与 Schedule Policy 固定也必须先完成备份和预览；
4. 生产数据库变更前创建并验证可恢复备份；
5. 发生重复通知、证据断链、自动写决策/持仓或数据异常时，先暂停对应 Feature 与 Schedule，保留审计。

## 9. 仓库与敏感信息

- 主项目：`https://github.com/Lanbasara/investment-companion`（Private）；
- Plugin：`https://github.com/Lanbasara/investment-companion-plugin`（Private）；
- Tushare Token：`~/.config/tushare/token`，不得写入 Git、SQLite Raw 或日志；
- `.state/`、真实调查材料、账单、数据对象和导出默认不进入 Git；
- 仓库根未跟踪 zip、`reports/` 和周报属于用户材料，不得提交或删除。
