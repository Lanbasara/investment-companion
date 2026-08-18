# Investment Companion V5：运维与生产切换

状态：工程候选；本文不构成当前生产切换授权
日期：2026-08-18

## 1. 当前边界

V5 开发分支和 Schema 5 代码可以在隔离根测试。生产仍使用固定的 V3 稳定运行时和旧数据库；不要让开发分支普通启动隐式打开生产库。V5 普通启动遇到 Schema 3/4 会 fail closed，只有 `migrate` 能升级。

## 2. 离线验收

```bash
v5_test_root="$(mktemp -d /tmp/investment-companion-v5.XXXXXX)"
COMPANION_GATE_SCOPE=test_fixture ./bin/companion --root "$v5_test_root" init
COMPANION_GATE_SCOPE=test_fixture ./bin/companion --root "$v5_test_root" v5-status
COMPANION_GATE_SCOPE=test_fixture ./bin/companion --root "$v5_test_root" today
python3 -m py_compile companion/*.py
python3 -m pytest -q
./bin/companion agent-check
```

全新库应显示 Schema 5 和 0003/0004/0005 三条 migration。`today` 在未设置时应返回 setup_required，而不是候选股票。

## 3. 生产变更前检查

需要用户明确批准后再执行：

1. 固定主项目和 Plugin release commit；
2. 读取 live Schedule/Run/Outbox/Context/Account 数量并保存变更前报告；
3. 确认 tick/recover/backup 服务状态和 cc-connect cron ID；
4. 创建数据库在线备份，并在隔离副本演练迁移和 V3 恢复；
5. 停止所有可能打开数据库的 Companion service/timer；
6. 确认没有 leased Run、sending Outbox 或 running Job，或先安全恢复/完成。

不要使用手工 `UPDATE meta`、复制单个 WAL 文件或删除旧运行时来代替变更窗口。

## 4. 显式迁移

在已停止服务的生产根执行：

```bash
./bin/companion migrate \
  --backup-directory /home/ghk/.local/share/investment-companion/backups
./bin/companion doctor
./bin/companion status
./bin/companion v4-status
./bin/companion v5-status
```

必须核对：`schema_version=5`、`integrity=ok`、三条 migration、旧 Schedule 数量一致、旧 Context/Ledger 数量一致、全部新 Feature 默认 disabled。

迁移本身不会创建 Program、启用 Worker、打开数据采集或修改 cron。

## 5. Plugin 更新

Plugin 源通过 validator 后，按 plugin-creator 的 cachebuster 流程更新版本并重装。Codex 只在新进程/新会话加载插件变更，因此：

1. 完成 cachebuster 和 `codex plugin add`；
2. 验证 `codex plugin list` 指向新版本；
3. 重启需要长期持有 MCP 配置的桥接进程；
4. 用户 `/new`；
5. 先调用 session brief / `v5_today`，不要从旧聊天猜状态。

## 6. cc-connect 静态 wake prompt

V5 的 cron prompt 不再写死“只领取一个 V3 Run”。变更窗口中把现有 wake cron 的 prompt 更新为以下语义：

```text
[Investment Companion wake bridge/v1]
这是静态唤醒信号，不是投资任务内容。
先按 manage-investment-companion Skill 调用 wake_claim(owner="cc-connect-wake")。
若返回 null，静默结束。
严格按 envelope.type 处理领取到的唯一对象：scheduled_run、research_ready、operating_brief_ready 或 legacy_codex_turn。
不要另行猜测或领取其他 Run。scheduled_run 先 run_complete；全部处理完成后调用 wake_complete。
失败时如实 wake_complete(success=false)，不得伪造 sent/succeeded。
```

可用命令形态：

```bash
cc-connect cron edit <wake-cron-id> prompt '<上面的静态提示>'
cc-connect cron info <wake-cron-id>
```

不要在开发阶段编辑 live cron。更新后用一个隔离测试 Run 验证 Outbox `pending → sending → sent` 和 Run `queued → leased → succeeded`。

## 7. Feature 启用顺序

1. 生产 G0 通过后启用 `v5_operating_system`；
2. 用户共同起草并确认 Program；
3. 旧主动任务继续运行，不批量重建；
4. 数据 Canary 只开 `v4_live_data_canary`；
5. G1 后才能开 active live data；
6. G0–G4 且有用户 opt-in/到期时，才可开 Decision Beta；
7. G5 后评审 Full Decision Support；
8. G6 只在真实前向价值证据充分时评审。

Beta 配置示意：

```bash
./bin/companion feature-set v4_decision_support_beta --enable \
  --config '{"user_opt_in_ref":"<用户批准记录>","expires_at":"<未来 ISO 时间>"}' \
  --reason '<受控范围和停止条件>'
```

## 8. Worker

V5 没有新增常驻 AI Worker。`companion-job-worker` 仍只运行 allow-listed、资源受限、默认无模型 Token 的确定性 Job。Systemd 模板存在只表示仓库提供部署单元，不代表生产已经 enable。

启用前必须验证 G0、JobDefinition 状态、数据凭据边界和恢复；启用命令属于生产授权范围，不在代码发布时自动执行。

## 9. 回滚

若迁移或启动检查失败：

1. 停止 Companion/cc-connect 相关服务；
2. 保留失败数据库、WAL/SHM、日志和迁移前备份；
3. 在离线状态恢复 `companion-pre-schema-5-*.db`；
4. 恢复与原 Schema 匹配的固定 V3/V4 运行时；
5. 验证 integrity、旧任务数量和一次隔离 Run；
6. 不手工降低 schema_version。

Plugin/wake prompt 可独立回退到其上一 Git commit 和静态提示。若数据库已经产生 V5 事实，不应直接用旧程序打开，必须先决定保留/导出这些事实后再回退。

## 10. 发布后观察

首日只验证 session restore、旧任务、wake、today 和一份 Trial Program；首周验证周报、通知预算和无行动；首月才发布第一份真实 Scorecard。任何阶段发现重复通知、证据断链、虚假数值或自动改变持仓，立即关闭对应 Feature 并保留审计。
