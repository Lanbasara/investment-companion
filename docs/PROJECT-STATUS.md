# Investment Companion：项目状态与会话交棒

更新时间：2026-08-23

当前生产基线：V7 / SQLite Schema 7 / 固定 Git Runtime（已迁移并运行）

当前开发目标：版本无关架构 Batch A–C 代码收口已完成，Batch D 开发态工具面已完成；生产 Runtime 不随工作树自动切换

当前分支：`feature/v5-investment-operating-system`

V4 冻结：annotated tag `v4.0.0-engineering-baseline`

当前 Plugin：`0.1.0+codex.20260821121340`，仍使用生产兼容工具面；新 Skill 在新会话加载

## 1. 当前结论

产品的长期身份不再用 V5、V6 或 V7 定义，而是：**Codex 驱动、确定性证据支撑、飞书协作、人工执行、可审计并能用真实盈亏持续自我否证的个人投资管理伴侣**。

V5–V7 名称仍用于描述已经上线的历史 Schema、工具别名和兼容契约。当前生产 V7 保留 Program、股票/ETF 预测与结果交付账本；Run 成功不等于用户已经收到结果。

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
2. [ARCHITECTURE.md](ARCHITECTURE.md) 与 [DOMAIN-GLOSSARY.md](DOMAIN-GLOSSARY.md)：版本无关目标架构和统一语言；
3. [REFACTOR-PLAN.md](REFACTOR-PLAN.md)：迁移批次、兼容原则和退出门；
4. [V7-RESULT-DELIVERY.md](V7-RESULT-DELIVERY.md)：当前生产交付契约；
5. [V5-OPERATIONS.md](V5-OPERATIONS.md)：当前生产恢复、Feature 和 Worker 运维。

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

当前工作树新增、尚未切换生产 Runtime 的架构能力：

- `foundation`、公共 Audit Trail 和显式 Composition Root；Schedule/Run、Outbox/Wake 和 System Operations 已抽入 `platform`，`core.py` 从 1,194 行缩至约 513 行；
- Program/Policy 生命周期和 Opportunity/Portfolio Decision/DecisionQueue 已抽为独立应用服务，`operating.py` 从 2,008 行缩至 748 行；
- 独立确定性 Risk Gate，覆盖当前 Mandate、现金、集中度、范围、流动性、行情新鲜度、有效期和 A 股交易单位；
- 独立 Performance Engine，计算期间现金流调整收益、基准超额、成本和最大回撤；
- Review/Change Proposal 服务，复盘只能提议新版本，不能在线修改当前策略；
- Research Validation Service 把定性 Thesis 的两组冻结来源、PIT 时点、新鲜度、反证、适用范围和成本，以及量化 Strategy 的预注册离线结果与 Shadow 前向样本，统一记录为可重放 Calculation；
- Research Catalog 已把持续扫描、旧预测、信号与前向复核映射为版本无关 `ResearchRecord`；历史 V5/V6 标签不会被自动晋升为 StrategyVersion；
- 版本无关 Decision 已按研究强度分级：`eligible_for_bounded_action` 只能形成 Program 限额的条件行动，`eligible_for_decision` 才能形成正式行动；两者仍必须通过匹配等级的 Risk Gate，纯未验证预测只能停留在研究层；
- Opportunity 资格不再保存自报证据等级，只引用正式 Research Validation Calculation；Actionability Service 在行动入队、呈现和接受前重新检查研究版本、Decision、Context、确认账本、最新行情与 Risk Gate；
- Execution Lifecycle Service 区分行动卡接受、执行准备、用户报告订单、待确认成交、确认 Ledger 与部分/全部/偏离成交；只有确认 Ledger 改变组合；
- Investment Briefing Service 将 Execution、待确认/已确认 Ledger、DecisionQueue 与最新 Reconciliation 投影到 Investment Home 和 Today，并为日/周/月 Brief 自动冻结不可伪造的 `execution_operating_snapshot` Calculation；
- 冻结数据驱动的 China Market Calendar，持续量化研究已改用统一交易日解释；
- `investment_home`、组合/研究/决策/评价四个 Context Workbench 和 10 个窄写命令；
- 22 工具的版本无关 Investment MCP Profile；发布审计补齐了原 15 工具缺少的账户/资产、证据、Program、Opportunity、Action、Brief、Schedule/Wake/Delivery 入口，旧 157 工具保留在兼容/Admin Profile；
- 跨模块黄金闭环已贯通研究、Research Validation、Risk Gate、Decision、待确认成交、确认账本、现金流调整绩效和惰性变更提案；
- 精确同周期 Performance Calculation 已接入 Program 评价覆盖，月度指标不再把已有真实收益误报为缺失；
- 156 项测试、8 个子测试通过；架构测试同时锁定大型模块上限、版本依赖、新服务边界与 22 工具闭环完整性。Agent 配置未发生变化。

当前生产 Runtime 已实现并继续保留：

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
- 生产数据库是 `/home/ghk/investment-home/.state/companion.db`；当前已迁移至 Schema 7；
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

架构代码收口已完成，尚待发布或真实世界验证：

- 安装已改写的 Plugin Skills，完成 22 工具 Investment/Admin Profile 双读与飞书对话验收；
- 经用户单独批准后备份、固定新 Runtime、灰度切换；当前未改 Feature、Schedule、systemd、cc-connect 或飞书配置；
- 飞书业务交互正式使用自然语言，不开发额外交互卡片；cc-connect 现有进度显示不受影响；
- 聚合专属 Repository 与更深的历史兼容代理拆分只在存储替换、Schema 演进或历史模块再次增长时启动，不是当前发布阻塞项。

真实世界证据仍待积累：

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
