# Investment Companion V2 验收记录

日期：2026-08-13

## 自动化验证

- Python 测试：7 项通过，覆盖 Schedule、版本冲突、Watch 事件跨越、Inbox 去重、Run 幂等与真实子进程崩溃后的租约恢复。
- MCP 协议：`initialize`、`tools/list` 与 Companion 工具调用通过。
- 数据库：SQLite `integrity_check=ok`，WAL、外键、同步写、唯一幂等键和在线备份已启用。
- systemd：开机恢复、30 分钟 Tick、每日在线备份均已启用。

## 行为验证

- 飞书链路：Companion Run → Outbox → cc-connect 唤醒桥 → Primary Codex → `run_complete` 已真实跑通。
- 哨骑：Primary 显式派遣一个短命 `market_scout`，结果写入 Case 文件并登记 Artifact；Scout 未创建 Agent、Case、Watch、Thesis 或消息。
- 园丁：Primary 显式派遣一个短命 `knowledge_gardener`，只写维护报告；Case 文件哈希在执行前后保持一致，无删除、移动或认知性越权。
- 静默策略：唤醒桥在无任务或不满足 Schedule 通知策略时严格输出 `NO_REPLY`。

## 已知边界

- V2 不自动交易，不维护常驻 Subagent，不建设网站。
- cc-connect 当前通过单一休眠 Cron 充当“唤醒原语”；投资任务和频率不存入该 Cron。未来若 cc-connect 提供无需重启且可定向唤醒会话的原生接口，可只替换 Outbox Adapter，不改 Companion 模型。
