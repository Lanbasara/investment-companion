# Investment Companion V5：运维与生产切换

状态：V5 已在生产；持续量化研究按独立用户授权运行
日期：2026-08-19

## 1. 当前边界

生产使用只读 Git worktree 固定 V5 Runtime，业务状态仍位于 `/home/ghk/investment-home/.state`。数据库已是 Schema 5，`v5_operating_system` 已启用；正式 Live Data、Shadow、Decision Support 和自动交易仍关闭。开发工作树不能直接充当生产 Runtime，切换代码版本后必须重新生成当前提交的 G0 证据。

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

## 3. 每次生产变更前检查

涉及 Runtime、Plugin、Feature 或 systemd 的变更仍需要用户明确批准：

1. 固定主项目和 Plugin release commit；
2. 读取 live Schedule/Run/Outbox/Context/Account 数量并保存变更前报告；
3. 确认 tick/recover/backup 服务状态和 cc-connect cron ID；
4. 创建数据库在线备份，并在隔离副本演练迁移和 V3 恢复；
5. 停止所有可能打开数据库的 Companion service/timer；
6. 确认没有 leased Run、sending Outbox 或 running Job，或先安全恢复/完成。

不要使用手工 `UPDATE meta`、复制单个 WAL 文件或删除旧运行时来代替变更窗口。

## 4. Schema 迁移与恢复参考

仅当旧环境仍是 Schema 3/4 时，在已停止服务的生产根执行：

```bash
./bin/companion migrate \
  --backup-directory /home/ghk/.local/share/investment-companion/backups
./bin/companion doctor
./bin/companion status
./bin/companion v4-status
./bin/companion v5-status
```

必须核对：`schema_version=5`、`integrity=ok`、三条 migration、旧 Schedule 数量一致、旧 Context/Ledger 数量一致、全部新 Feature 默认 disabled。

当前生产已经完成 Schema 5 迁移。迁移本身不会创建 Program、启用 Worker、打开数据采集或修改 cron。

## 5. Plugin 更新

Plugin 源通过 validator 后，按 plugin-creator 的 cachebuster 流程更新版本并重装。Codex 只在新进程/新会话加载插件变更，因此：

1. 完成 cachebuster 和 `codex plugin add`；
2. 验证 `codex plugin list` 指向新版本；
3. 重启需要长期持有 MCP 配置的桥接进程；
4. 用户 `/new`；
5. 先调用 session brief / `v5_today`，不要从旧聊天猜状态。

## 6. cc-connect 静态 wake prompt

V5 的生产 cron prompt 已使用 version-neutral wake envelope。需要恢复或审计时，目标语义如下：

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

更新后用一个隔离测试 Run 验证 Outbox `pending → sending → sent` 和 Run `queued → leased → succeeded`。

## 7. Feature 启用顺序

1. 生产 G0 通过后启用 `v5_operating_system`；当前已完成；
2. 用户共同起草并确认 Program；
3. 旧主动任务继续运行，不批量重建；
4. 数据研究链开 `v4_live_data_canary`，绑定用户批准、开始时间和单 Job 请求预算，不设置到期或运行次数上限；
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

V5 没有新增常驻 AI Worker。`companion-job-worker` 只运行 allow-listed、资源受限、默认无模型 Token 的确定性 Job。持续量化研究获批后，生产启用该 timer，每 5 分钟最多领取两个 Job；它不是 Codex 会话，也不会自行形成投资判断。

启用或切换 Runtime 前必须验证 G0、JobDefinition 状态、数据凭据边界和恢复。持续研究的定义、启动、停止和月度复盘见 [V5-CONTINUOUS-QUANT-RESEARCH.md](V5-CONTINUOUS-QUANT-RESEARCH.md)。

## 8.1 主动研究质量检查

```bash
./bin/companion v5-research-quality --days 30
```

该回执检查 Patrol 来源覆盖、原始来源比例、任务启动延迟和 Outbox 积压。`needs_attention` 表示本期研究或交付证据未达到契约门槛，不等于投资结论失败。

生产 Tick 每五分钟批量处理最多 20 个到期任务。确定性 Worker 完成材料性 Job 后立即触发 Primary；收盘复盘等待同日量化扫描，最长等待 15 分钟，之后必须标记缺口并继续生成报告。

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

经营层继续验证 session restore、旧任务、wake、today、周报、通知预算和无行动。持续量化研究每天进入收盘复盘；每月 19 日强制生成前向 Review Manifest 和用户报告。任何阶段发现重复通知、证据断链、虚假数值或自动改变持仓，立即暂停对应 Schedule 或 Feature 并保留审计。
