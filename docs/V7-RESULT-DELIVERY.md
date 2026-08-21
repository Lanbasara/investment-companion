# V7：结果交付闭环

## 结论

V7 把“工作已完成”和“用户已收到可理解的结果”拆成两个独立、可审计的事实。完成卡片、Wake 和 Job Manifest 只说明系统发生了什么；它们不再被视为投资结论的替代品。

## 交付契约

每个 Run 在创建时都有唯一 `DeliveryRecord`，由 Schedule Policy 的 `delivery_mode` 决定：

| Mode | 含义 | 可接受的完成状态 |
| --- | --- | --- |
| `silent_allowed` | 只需内部记录 | `suppressed` |
| `digest_required` | 必须进入一次用户摘要 | `delivered`（摘要发送成功后） |
| `report_required` | 必须单独给用户结果 | `delivered`（cc-connect 实际发送成功后） |
| `action_required` | 必须给出行动或不行动结论 | `delivered`（cc-connect 实际发送成功后） |

结果内容使用不可变 `ResultEnvelope`：结论、简短说明、最多三条依据、下一步、下次检查时间和稳定来源引用。首次 `delivery_prepare` 后不能悄悄改写；内容变化必须生成新的 Run/Delivery。

## Attention Policy 的边界

Attention Policy 只决定**何时**通知：立即发送、进入摘要或因预算/静默时段延后。它不能把 `digest_required`、`report_required` 或 `action_required` 的结果抑制为“无需说明”。

若 Policy 将直接报告延后，DeliveryRecord 保持 `queued_digest` 并保留冻结内容；`delivery_status` 与 `doctor` 会把未交付的 required result 显式暴露出来。实际发送成功后才标记 `delivered`；失败保留为 `retry`，第五次失败才为 `failed`。

## Primary Codex 工作流

1. Claim `codex_turn` Wake，读取 Run、Manifest 和 `DeliveryRecord`。
2. 用 `delivery_prepare` 写入用户能读懂的 ResultEnvelope；不要只写 `run_complete` 或完成卡片。
3. 对 `report_required` / `action_required`，Worker 通过 cc-connect 直接发送结果；对 `digest_required`，在收盘摘要调用 `delivery_digest_send`，一次发送可覆盖多条记录。
4. 用 `delivery_get` 或 `delivery_status` 核对 `delivered`；未送达时让 outbox 重试或运行 `recover`。

## 旧 Schedule 映射

迁移不会猜测或静默改变现有任务。先预览，再在确认后固定 Policy：

```bash
./bin/companion delivery-migrate-schedule-policies
./bin/companion delivery-migrate-schedule-policies --apply
```

兼容映射为：`report_every_successful_run` / `notify=every_successful_run` → `report_required`；`material_only`、`exceptions_only`、`daily_brief_input` → `digest_required`；其余 → `silent_allowed`。之后以显式 `delivery_mode` 为准。

## 上线与回滚

先跑全量测试并固定 Runtime，再执行带备份的数据库迁移：

```bash
./bin/companion migrate --backup-directory /home/ghk/.local/share/investment-companion/backups
./bin/companion delivery-migrate-schedule-policies
./bin/companion doctor
```

确认映射后才执行 `--apply`。迁移返回的数据库备份是唯一回滚起点；停止 Companion 服务、保留失败数据库和 WAL/SHM，再按 V5 运维手册离线恢复。不要手工修改 SQLite 或降低 schema version。
