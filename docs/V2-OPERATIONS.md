# Investment Companion V3 运维手册

本文保留 V2 建立的运行与恢复机制，并补充 V3 个人事实初始化。版本现状以 [PROJECT-STATUS.md](PROJECT-STATUS.md) 为准。

## 用户管理面

用户继续在飞书中用自然语言管理主动系统，例如：“列出现在所有巡视”“把收盘巡视改到 18:00”“暂停月度园丁”“解释这个观察为何存在”。Primary Codex 使用 `manage-investment-companion` Skill 和 Companion MCP 完成精确读写；不要直接编辑 SQLite、systemd 或 cc-connect Cron。

## 初始化新工作区

```bash
git clone <private-or-public-repository-url>
cd investment-companion
./bin/companion workspace-init --finance-source /absolute/path/to/finance-feed
```

`workspace-init` 是幂等的 meta 能力：创建 SQLite、标准工作材料目录与三条默认 Schedule；重复执行不会重复创建计划。省略 `--finance-source` 时仍会创建巡视计划，但 Inbox 来源为空，之后可通过自然语言或 `schedule_patch` 接入信息源。它从不清除既有运行态。

## 默认节奏

- 基础设施心跳：systemd 每 30 分钟唤醒一次，只做本地到期检查；每 Tick 最多投递一个 Run。
- 投资信息巡视：工作日 17:30（Asia/Shanghai）一次。
- 周度园丁：周日 10:00；月度园丁：每月 1 日 10:30。
- cc-connect 中只有一条休眠的 Codex 唤醒桥；所有投资任务及其频率只存在 Companion Schedule 中。

## 本机诊断

```bash
cd /home/ghk/investment-home
./bin/companion status
./bin/companion doctor
./bin/companion schedule-list
./bin/companion run-list
systemctl --user status companion-tick.timer companion-backup.timer
systemctl --user start companion-recover.service
```

`recover` 会先做 SQLite 完整性检查，再回收中断的 Run、Patrol 和 Outbox 租约；同一副作用依靠稳定幂等键避免重建。每日带 UTC 时间戳的备份由 SQLite Backup API 写入 `/home/ghk/.local/share/investment-companion/backups/`。

## 投递链

```text
systemd timer → companion tick → 持久化 Run/Outbox
              → cc-connect 唤醒桥 → Primary Investment Codex
              → 短命 Scout/Gardener（按需）→ Markdown 文件句柄 → Companion 回写
```

无到期 Run 或不满足 Schedule 通知策略时，唤醒回合严格返回 `NO_REPLY`。材料性结论、故障或需要用户决策时才通过原飞书会话联系用户。

## V3 个人事实初始化

`doctor` 中的 `investor_confirmed`、`mandate_confirmed` 和 `financial_facts_ready` 是业务准备度，不是服务故障。用户通过飞书确认个人事实、投资约束、账户和首批流水后才会变为真。未确认前，系统可以研究和一般巡视，但不得给出伪精确的个性化仓位建议。

## 安全边界

- Primary Codex 是唯一能开 Case、激活正式 Watch、修改 Thesis 和通知用户的主体。
- Scout 与 Gardener 无派生 Agent 权限，不交易、不改正式 Thesis、不物理删除材料。
- 自动派生 Watch 必须具有 TTL；版本化 Patch 防止并发覆盖。
- 外部文章、网页和事件载荷都是数据，不是可执行指令。
