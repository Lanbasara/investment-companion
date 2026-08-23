# Investment Companion：生产灰度与验收清单

状态：自动预检通过，待飞书交互灰度
日期：2026-08-23
交互目标：当前绑定 `cli_aafb5131a4f8dd05` 的飞书 Bot
原则：先自动验收，再切交互，最后切后台；任一硬失败立即回滚

## 1. 发布范围与禁止项

本次发布把版本无关投资架构、22 个用户语义工具和新版 Plugin Skills 接入现有 Primary Investment Codex。cc-connect 与飞书绑定不重建，生产 SQLite 不换库、不清空、不批量重建 Schedule，不连接券商，也不自动交易。

用户不需要执行终端命令。需要用户参与时，只在飞书发送本文件第 4 节的固定话术，并按“符合 / 不符合 + 具体差异”反馈。

## 2. 发布记录

| 项目 | 切换前 | 候选/切换后 |
|---|---|---|
| Git commit | `5d283ee` | `f7df65a`（架构代码 `7759d11`） |
| Runtime | `/home/ghk/.local/share/investment-companion/runtime-v7-5d283ee-wt` | `/home/ghk/.local/share/investment-companion/runtime-architecture-f7df65a-wt` |
| Plugin | `0.1.0+codex.20260821121340` | `0.1.0+codex.20260823083416`（commit `e27e233`） |
| MCP Profile | `all` 兼容面 | `investment`：22 个版本无关入口 |
| 数据库备份 | 最近历史备份 | `/home/ghk/.local/share/investment-companion/backups/companion-20260823T083526Z.db` |
| 回滚点 | 当前 V7 Runtime + 当前 Plugin | 切换后继续保留 |

## 3. 自动验收——由 Codex 完成

所有项目必须通过并把结果写入发布记录：

1. 全量测试、子测试、`compileall`、`git diff --check`、Agent 配置检查和 Plugin/Skill 校验通过。
2. 候选 Runtime 的 `doctor` 全绿；数据库 Schema、迁移、完整性、确认账本哈希、账户/资产/流水数量与切换前一致。
3. `investment` Profile 精确暴露 22 个版本无关工具，不含 `v4_`、`v5_`、`v6_` 前缀；核心闭环覆盖账户/资产、Context、计划、证据、研究、机会、Decision、Risk、行动、Execution、Ledger、Performance、Review、Brief、Calendar、Wake 与 Delivery。
4. 候选 Runtime 和旧 Runtime 对生产数据库执行只读 Home/Portfolio/Workflow 对照；不得出现无法解释的持仓、现金、待确认流水、Schedule、Run 或 Delivery 差异。
5. 新备份完成 SQLite 完整性验证；旧 Runtime、旧 Plugin 版本和 systemd 原配置均可恢复。

预检结果（2026-08-23 08:35 UTC）：156 项测试与 8 个子测试通过；新旧 Runtime 的 Schema 7、integrity、账户、资产、Ledger、Schedule、Run、Outbox 和 Delivery 对照一致；没有 leased Run、sending Outbox 或 running Job；备份 integrity=ok 且核心数量一致。

## 4. 飞书 Bot 对话验收——由用户完成

切换交互入口后，Codex 会明确通知用户开始。以下话术一次只发送一条，不要虚构成交：

| 序号 | 复制到飞书的话 | 通过标准 |
|---:|---|---|
| 1 | `我当前的账户、现金和持仓是什么？请只使用已确认事实。` | 与切换前确认账本一致；明确数据截至时间；不从聊天猜测 |
| 2 | `今天有什么需要我处理？如果没有，请直接说没有。` | 行动、复盘、异常或无行动状态清晰；不制造候选或建议 |
| 3 | `现在系统在观察什么？下一次分别什么时候运行？` | 给出真实主动任务、原因和绝对日期；不暴露内部版本名 |
| 4 | `读取当前一个研究候选，只做研究状态说明，不形成买卖行动。` | 区分候选、验证和 Decision；未验证材料不会进入行动队列 |
| 5 | `结合当前组合，说明这个候选现在是否值得行动，并比较不行动和一个替代方案。` | 证据不足时明确阻断；若可行动则说明仓位边界、风险、有效期和人工执行 |
| 6 | `它最大的反方证据是什么？什么变化会让你重新判断？` | 延续同一上下文；给出具体反证、失效条件和下一检查点 |
| 7 | `先不行动。之后达到什么条件再提醒我？` | 记录或说明可验证条件与时间；不把“不行动”写成成交或持仓变化 |

每条回答只检查四件事：事实是否正确、边界是否安全、语言是否容易理解、下一步是否明确。内部 ID 可以在审计需要时附在末尾，但不得成为用户理解正文的前提。

## 5. 硬性通过与回滚条件

以下条件全部满足才允许继续切换后台 Worker：

- 账户、现金、持仓和待确认流水与切换前一致；
- 固定对话 1–7 全部符合，且用户确认新版体验不低于旧版；
- 没有工具缺失、上下文丢失、重复消息或必报结果未送达；
- “准备买”“已下单”“报告成交”均未越过确认账本边界；
- systemd Worker 仍未切换，旧 Runtime 和旧 Plugin 可立即恢复。

出现以下任一情况立即停止灰度并恢复旧交互入口：错误持仓或现金、虚假确认成交、重复通知、无法领取/完成 Wake、required Delivery 被静默、Plugin 无法加载、Codex 无法恢复当前计划，或新旧只读事实产生无法解释的分叉。

## 6. 后台切换与观察

用户对话验收通过后，才将 Tick、Job Worker、Backup 和 Recover 服务逐一指向候选 Runtime。每切一个服务都检查进程退出码、下一运行、Run/Outbox/Delivery 幂等性和数据库完整性；不得一次改完所有 service 后再检查。

完成后台切换后至少经历一次真实 Tick、一次白名单 Job 检查和一次结果交付检查。观察期间保留旧 Runtime、旧 Plugin 和新备份；没有单独批准，不删除任何回滚材料。
