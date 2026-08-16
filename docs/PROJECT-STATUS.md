# Investment Companion：项目状态与会话交棒

更新时间：2026-08-15
当前发布：主项目为 `v3.0.3`，Plugin 为 `v3.0.1`
当前定位：V3 Core 已发布，等待真实用户数据初始化与 2–4 周使用验证。

## 1. 新会话从这里开始

维护或继续设计前按顺序执行：

```bash
cd /home/ghk/investment-home
git status -sb
git fetch origin --tags --prune
./bin/companion doctor
./bin/companion agent-check
python3 -m pytest -q
codex plugin list
```

然后阅读：

1. 本文：当前真相、边界和下一步；
2. [V3-DESIGN.md](V3-DESIGN.md)：目标架构和长期不变量；
3. [V3-ACCEPTANCE.md](V3-ACCEPTANCE.md)：已经验证与尚未验证；
4. [V2-OPERATIONS.md](V2-OPERATIONS.md)：本机运行、恢复和备份；
5. `AGENTS.md` 与已安装 Plugin Skills：Codex 实际行为契约。

可视化文档中心位于 [index.html](index.html)，它直接渲染本目录的权威 Markdown；交棒时仍以本文的状态与验收记录为准。

不要从旧聊天、`memory/*.md` 或 `portfolio/current.md` 推断真实持仓和个人事实。

## 2. 当前架构真相

```text
飞书用户 → cc-connect → Primary Investment Codex
                         ├─ Investment Companion MCP（77 tools）
                         ├─ Financial Kernel
                         ├─ Cognitive Ledger
                         ├─ Attention Engine
                         └─ 短命 Custom Agents

systemd 30 分钟 Tick → Schedule / Run / Outbox → 单一 cc-connect 唤醒桥
SQLite Schema 3 保存精确状态；Markdown 保存可读认知材料。
```

Primary Codex 是唯一最终判断、Agent 派遣、正式认知发布和用户沟通主体。Companion 不包裹 Codex，不自动交易。

## 3. 已实现并验证

- V2：Schedule、Watch、Observation、Event、Run、Case、Patrol、Artifact、Outbox、租约、幂等、重启恢复、在线备份。
- V3 Financial Kernel：Account、Asset、Ledger Draft/Confirm/Reverse、CSV Draft Import、Portfolio As-of、Market Snapshot、Trade Impact、Max Purchase、基础 Exposure、Calculation Record、Reconciliation。
- V3 Context：Investor、Mandate、Attention Policy 的 Draft/Current/Trial/Superseded。
- V3 Cognition：Thesis/Decision/Review 对象与不可变 Revision、Decision Freeze、Execution 分离、Recovery Package。
- V3 Attention：静默时段、每日预算、主题冷却、通知动作、反馈、投递状态。
- Source Health、`workspace-init`、`doctor`、Plugin 安装和 GitHub Private 仓库。
- 21 项自动化测试、MCP 协议检查、5 个 Custom Agent 真实派遣烟测、全新 Codex Readiness 前向验证、在线备份独立恢复。
- 项目级 SessionStart Hook 注入有界 `session-brief`；新 Codex 会话先获得健康与活跃状态，再由 Lifecycle Skill 按问题创建 Recovery Package。
- 项目级 `.codex/config.toml` 统一拥有共享 MCP；Custom Agent 只声明角色差异并继承项目 MCP，避免同名服务覆盖导致 Agent 不可用。
- 2026-08-15 已用 `market_scout` 成功重跑此前失败的收盘巡视；新 Run 成功完成，旧失败记录保留用于审计。

## 4. 当前生产运行状态

- 默认 Schedule：工作日 17:30 信息巡视、周日 10:00 周度园丁、每月 1 日 10:30 月度园丁。
- 基础心跳：每 30 分钟，仅执行本地到期检查；每 Tick 最多投递一个 Run。
- Investor 与 Mandate：只有未初始化 Draft，尚无 Current。
- Attention Policy：保守默认版本已生效。
- Account、Asset、Ledger、Thesis、Decision、Execution：尚未录入真实用户数据。
- `doctor` 中 `investor_confirmed`、`mandate_confirmed`、`financial_facts_ready` 为 false 是业务未初始化，不是系统故障。

## 5. 已知限制

- Financial Kernel 是首版：尚无完整现金流调整收益率、复杂成本基础、完整公司行动、税务和通用多币种 FX 转换。
- Portfolio Exposure 仅在同一基准币种下精确工作；缺少 FX 时返回 Warning。
- Cognitive Ledger 已有版本机制，但尚无真实长期 Thesis/Decision 历史验证。
- Attention Engine 已有策略门控，但尚无真实误报、漏报和通知疲劳数据。
- 专用 Tushare Watch、公告和财报 Adapter 尚未完成；当前自动信息源以文件增量摄入为主。
- cc-connect 唤醒依赖一条休眠 Cron 作为唤醒原语；投资任务频率只在 Companion Schedule 中。
- Run 的 claim/lease 元数据尚未完全贯穿 cc-connect 唤醒执行链；当前成功重跑仍显示 `attempt=0`、`started_at=null`，需单独修复。
- 文件信息源首次巡视曾报告覆盖为 0，但重试时已存在可审计材料；Source cursor/coverage 诊断仍需加强，不能把该次结果解读为真实世界没有信息。
- `docs/EVENT-SYSTEM-PLAN.md` 是历史设计，不是当前实施说明。

## 6. 下一步，不要提前扩张

先让用户在飞书完成：

1. 确认 Investor Revision；
2. 确认 Mandate Revision；
3. 建立账户、资产、现金和当前持仓；
4. 用一笔小额真实成交验证 Draft → Confirm → Portfolio；
5. 正常使用 2–4 周，记录漏报、误报、工具路由、上下文恢复和录入摩擦。

第一次真实复盘后再决定 V3.1/V4。优先修真实摩擦，不以增加 Agent、Adapter 或数据表作为进展指标。

## 7. 未来版本候选

只有真实使用证明需要时考虑：

- V3.1：账本导入/对账体验、收益率与成本基础、多币种 FX；
- V3.2：Thesis/Decision/Review 的真实生命周期与月末 Close；
- V4：注意力策略学习提案、Tushare Watch 与官方公告 Adapter；
- V5：跨机器安装、加密备份、长期恢复演练和公开发行。

版本号只是建议，不是已批准路线。

## 8. 仓库与敏感信息

- 主项目：https://github.com/Lanbasara/investment-companion
- Plugin：https://github.com/Lanbasara/investment-companion-plugin
- 两者都是 Private。
- Tushare Token：`~/.config/tushare/token`，不得写入 Git。
- Tavily Token：本机受限凭据文件，Launcher 运行时读取。
- `.state/`、调查运行材料、账单和导出默认不进入 Git。

## 9. 交棒验收

下一会话只有在以下检查通过后才能修改生产状态：

- Git 工作树和远端差异已解释；
- `system_doctor` 与 SQLite Integrity 正常；
- `agent-check` 通过；修改 Agent/MCP 后还需运行 `./bin/companion-agent-smoke`；
- 当前 Context、Account、Ledger、Schedule 使用工具读取，而非凭记忆猜测；
- 修改前存在在线备份；
- 不把代码能力、自动化测试和长期真实使用验证混为一谈。
