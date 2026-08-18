# Investment Companion：项目状态与会话交棒

更新时间：2026-08-18
当前生产：主项目 `v3.0.4` / SQLite Schema 3；生产数据库和主动链路未迁移
当前开发：`feature/v5-investment-operating-system` / SQLite Schema 5
V4 冻结：annotated tag `v4.0.0-engineering-baseline`
当前 Plugin 源与本机安装：`0.1.0+codex.20260818163220`；新 Skill 只在新会话加载，并对未迁移的 V3 MCP 明确降级

## 1. 当前结论

V5 已被重新定义为：**Codex 驱动、确定性证据支撑、以个人投资经营计划和决策闭环为中心、飞书极简协作、人工执行、能用真实结果持续自我否证的个人投资系统**。

V4 的数据、研究、Shadow、组合计算和人工行动内核继续保留；V5 新增的不是另一个选股算法，而是 Program → Opportunity → DecisionQueue → 人工成交 → Review/Scorecard 的用户经营闭环。

工程候选不代表真实数据合格、策略有效、高胜率或已经可以稳定盈利。生产仍在固定 V3 runtime 上运行；本次开发没有迁移数据库、修改 live cc-connect cron、启用 systemd Job Worker 或打开任何 Feature。

## 2. 新会话从这里开始

```bash
cd /home/ghk/investment-home
git status -sb
git fetch origin --tags --prune
./bin/companion doctor
./bin/companion agent-check
python3 -m pytest -q
codex plugin list
```

阅读顺序：

1. 本文：当前生产与开发边界；
2. [V5-DESIGN.md](V5-DESIGN.md)：为什么 V5 是经营闭环；
3. [V5-USER-GUIDE.md](V5-USER-GUIDE.md)：用户实际怎么用；
4. [V5-ARCHITECTURE-DECISIONS.md](V5-ARCHITECTURE-DECISIONS.md)：对象边界和不采用什么；
5. [V5-IMPLEMENTATION-PLAN.md](V5-IMPLEMENTATION-PLAN.md) 与 [V5-ACCEPTANCE.md](V5-ACCEPTANCE.md)：完成状态和验收；
6. [V5-OPERATIONS.md](V5-OPERATIONS.md)：只有获得生产授权后才执行的迁移；
7. [V4-DESIGN.md](V4-DESIGN.md) 和 [V3-DESIGN.md](V3-DESIGN.md)：底层研究内核与长期事实系统。

不要从旧聊天、`memory/*.md` 或 `portfolio/current.md` 推断真实持仓、个人事实或当前任务；使用 Companion 工具。

## 3. 当前架构真相

### 生产

```text
飞书 → cc-connect → Primary Investment Codex
                   ├─ V3 Companion / Financial / Cognition / Attention
                   └─ 短命只读专业 Agents

systemd Tick → Schedule / Run / Outbox → 静态 cc-connect 唤醒
固定 V3 runtime → 生产 SQLite Schema 3
```

### V5 工程候选

```text
用户 → v5_today
        ├─ setup_required → 确认 InvestmentProgram
        ├─ action → DecisionQueue / ActionCard
        ├─ no_action → 本轮有证据的无行动
        └─ review_required → 补本期检查

Program → Opportunity Funnel → Decision → Queue
        → 用户手工执行 → confirmed Ledger → Review / Scorecard

Outbox sending → 静态 cron → wake_claim 精确信封
              → 完成 Run/Event → wake_complete
```

Primary Codex 仍是唯一最终语义判断、正式发布和用户沟通主体。Companion 不包裹 Codex，不连接券商，不自动交易。

## 4. V5 已实现

- Schema 5 有序迁移 `0005_v5_investment_operating_system`，普通启动拒绝隐式升级；
- InvestmentProgram：不可变 Revision、confirmed Context、单 active、Trial 到期、显式 supersede；
- Opportunity：observed/researching/qualified/actionable 单向证据状态机、失败终态、证据引用和幂等转换；
- DecisionQueue：只接 current issued Decision、有效期、定时 snooze、accepted 待人工执行、实时失效、Attention 呈现和用户响应；
- OperatingBrief：daily/weekly/monthly 固定契约、no-action 防伪、版本/supersedes；
- ProgramScorecard：过程流量由专用确定性 Calculation 生成，指标只能从 `Calculation outputs.*` 解析并在读取时复核；
- `v5_today`：setup/action/no_action/review_required 四态用户入口；
- versioned wake envelope、`wake_claim` / `wake_complete`、旧 Run/研究事件兼容；
- cron exec 只记 signaled，Primary 完成后才记 sent；
- `v4_live_data_canary` 与 `v4_decision_support_beta`，把受控证据生成和正式放行分开；
- MCP server 5.0.0，V5/Wake 工具、CLI status/today/wake 入口；
- Plugin 新增 `operate-investment-program`，并更新主动、研究、决策、生命周期交接；
- V5 设计、ADR、计划、验收、用户与运维文档。

V2/V3/V4 的 Schedule、Run、Watch、Event、Case、Ledger、Context、Cognition、Job、Data、Research、Shadow 和 ManualAction 均继续使用原对象，没有复制真相。

## 5. 已验证

- 全新 Schema 5 初始化与有序 migration；
- Schema 3→5 和 Schema 4→5 显式迁移、旧 Schedule 保留；
- Program → Opportunity → Decision → Queue → Brief 端到端；
- 接受 Queue 后不创建 Execution、不改变 Ledger；
- Scorecard 拒绝调用方填写 value，并重放 Calculation；
- wake signal → exact claim → Run complete → wake complete；
- 父 Run 未终态时不能伪报 wake 成功；
- Beta 缺 opt-in/到期或到期后失败关闭；
- Tushare Canary 现在要求 G0 和独立 Feature；
- MCP 5.0.0 advertises V4/V5 safe surface；
- 5 个 Plugin Skill quick validation 和 Plugin validator；
- Plugin cachebuster 已更新并在 personal marketplace 本机重装。

最终测试数量和 commit 以本分支最后一次命令与 Git 历史为准；交棒时重新运行，不从本文猜。

## 6. 当前生产运行事实

- 生产运行时仍固定在 `/home/ghk/.local/share/investment-companion/runtime-v3`；
- 生产数据库仍是 Schema 3；开发程序的 `doctor` 对它报告“需要显式迁移”是预期 fail closed；
- 已注册的 Schedule/Watch/Run 不需要重置，Schema 5 是兼容升级；
- V5 新 Feature 默认关闭；
- 当前 live cc-connect wake cron 仍是旧静态提示，尚未改为 `wake_claim` 协议；
- Job Worker systemd 模板存在，但生产未 enable；模板存在不等于有 Worker 在运行；
- 工作项目不需要重置。生产切换和 Plugin/桥重启完成后，用户只需 `/new`。

具体账户、持仓、金额、Context ID 和主动任务清单只能现场调用工具读取，不在本文复制。

## 7. 尚未完成或不能由工程完成

- 没有执行生产 Schema 3→5 迁移和回滚窗口；
- 没有修改 live cron prompt 或重启 bridge/MCP；
- 没有在生产创建/确认第一份 InvestmentProgram；
- Tushare 真实数据资格 G1、official golden G2、真实研究/Shadow G3/G4 尚未完成；
- Decision Beta 的真实飞书 opt-in 与使用证据 G5 尚未产生；
- 策略和产品的长期前向净价值 G6 尚未产生；
- 没有 strategy-eligible 策略，不声明 Alpha、高胜率或盈利能力；
- 月度收益率、基准、资金加权/时间加权等精确指标仍须由 Financial Kernel 后续 Calculation 扩充，Scorecard 不会用模型补数。

## 8. 下一步顺序

1. 审阅 V5 代码和文档；不要先迁移生产；
2. 在生产数据库副本完成 Schema 3→5、回滚、旧任务数量和 wake smoke；
3. 获得用户对变更窗口的明确批准；
4. 迁移生产但保持全部新 Feature 关闭，验证 doctor/integrity；
5. 切换 Plugin 和 version-neutral wake prompt，重启桥后 `/new`；
6. G0 下启用 V5 operating layer，与用户确认一份有到期/停止条件的 Trial Program；
7. 用真实日/周/月周期逐步产生 G1–G6 证据，失败就缩减或停止。

不得批量重做现有主动任务。先让它们汇聚到 Program，使用数据证明重复或无价值后再逐项修订。

## 9. 仓库与敏感信息

- 主项目：`https://github.com/Lanbasara/investment-companion`（Private）；
- Plugin：`https://github.com/Lanbasara/investment-companion-plugin`（Private）；
- Tushare Token：`~/.config/tushare/token`，不得写入 Git、SQLite、Raw 或日志；
- `.state/`、真实调查材料、账单、数据对象和导出默认不进入 Git；
- 仓库根现有未跟踪 zip、reports 和周报属于用户材料，不得误提交或删除。

## 10. 交棒验收

下一会话修改生产前必须确认：

- Git branch、tag、remote 和脏文件均已解释；
- 当前生产 runtime 与数据库 Schema 现场读取；
- 备份可恢复，而不只是存在文件；
- `agent-check` 和 Plugin 版本正常；
- 当前 leased Run/sending Outbox/running Job 已处理；
- 用户明确批准生产迁移、cron 修改、service enable 或 Feature 开启；
- 工程能力、受控试用和长期投资价值没有被混为一谈。
