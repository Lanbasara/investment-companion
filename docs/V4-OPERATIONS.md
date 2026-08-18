# Investment Companion V4 运维手册

状态：工程实现完成；生产资格 Gate 未通过
日期：2026-08-18

## 1. 当前安全状态

生产数据库仍是 Schema 3，V3 定时服务固定从只读用途的稳定工作树 `/home/ghk/.local/share/investment-companion/runtime-v3` 运行。当前工作区的 `.state/runtime-code-root` 还把 MCP 与 SessionStart hook 指向同一稳定工作树；该本地文件被 Git 忽略，只用于变更窗口隔离。V4 分支不会隐式迁移旧数据库；所有 V4 feature flags 默认关闭，Job worker/timer 已随仓库提供但未启用。

过渡期内，稳定 V3 运行时的旧 `agent_config` 校验器会把 V4 Custom Agent 为禁用 Companion MCP 所需的同身份覆盖报告为告警；这不影响 V3 Tick 或数据库。以当前分支的 `./bin/companion agent-check` 和 `./bin/companion-agent-smoke` 验证角色权限，数据库/调度健康仍从 V3 `doctor.status` 与 timer 状态读取。迁移并切换到 V4 运行时后，`doctor` 必须恢复全部通过。

在 G0/G1 等门禁完成前，不要迁移生产库、启用 worker、激活 JobDefinition 或打开 V4 数据/Shadow/Decision 功能。

## 2. 离线验收

在临时根目录创建全新 Schema 4，不接触生产事实：

```bash
tmp_root="$(mktemp -d /tmp/investment-companion-v4.XXXXXX)"
COMPANION_GATE_SCOPE=test_fixture ./bin/companion --root "$tmp_root" init
COMPANION_GATE_SCOPE=test_fixture ./bin/companion --root "$tmp_root" v4-status
python3 -m pytest -q
python3 -m py_compile companion/*.py
```

测试结束后可以删除该临时目录；不得把真实 Token、账单、持仓或 provider Raw 响应复制进仓库 Fixture。

## 3. 显式迁移

迁移前先停所有可能打开数据库的服务并做独立在线备份。新程序遇到 Schema 3 会 fail closed，唯一升级入口是：

```bash
./bin/companion migrate \
  --backup-directory /home/ghk/.local/share/investment-companion/backups
```

成功结果必须包含 `schema_version=4`、`integrity=ok`、有序 migration 列表和迁移前备份路径。随后执行：

```bash
./bin/companion doctor
./bin/companion v4-status
./bin/companion agent-check
python3 -m pytest -q
```

`agent-check` 必须显示每个 Custom Agent 的 `disabled_mcp_servers` 包含 `investmentCompanion`，且所有 Agent 的 `sandbox_mode` 保持 `read-only`。修改 Agent 或 MCP 后还必须运行 `./bin/companion-agent-smoke`；提示词约束不能替代这两个技术检查。

迁移与检查均成功后，才可删除 `.state/runtime-code-root`，让 MCP 与 SessionStart hook 在下一进程启动时使用当前 V4 代码；删除前后都要做 MCP `initialize` / `tools/list` 协议检查。迁移不会自动启用任何 V4 feature。

正确发布顺序是：先在备份副本/隔离根完成 G0 预检并取得用户对生产迁移的明确批准；再在独立变更窗口迁移生产库但保持全部 Feature/Job inactive；随后基于已迁移生产状态签发 production G0。G0=Go 之前不得启用任何 V4 能力。隔离预检不是 production G0 assessment，不能用来开启 Feature。

## 4. 回滚

若迁移或启动检查失败：停止 Companion 服务，保留失败数据库及其 `-wal/-shm` 供调查，在离线状态从命令返回的 `companion-pre-schema-4-*.db` 恢复，然后只用与 Schema 3 匹配的稳定 V3 工作树运行 `doctor`。不要让 V3 程序打开 Schema 4 数据库，也不要手工降低 `meta.schema_version`。

2026-08-18 已在生产备份副本上完成 Schema 3→4、重复迁移、哈希一致和 V3 恢复演练；未迁移生产库。

## 5. Tushare Canary

交互式 probe/canary 可通过 `--token-file`、`TUSHARE_TOKEN_FILE` 或 `TUSHARE_TOKEN` 注入；受控生产 Job 固定读取 `~/.config/tushare/token`，不会接受 Job 参数中的凭据。命令输出、SQLite、Raw 请求和 Git 均不得保存 Token：

```bash
chmod 600 /home/ghk/.config/tushare/token
export TUSHARE_TOKEN_FILE=/home/ghk/.config/tushare/token
./bin/companion tushare-probe daily --params '{"trade_date":"YYYYMMDD"}'
./bin/companion tushare-stream-configure daily
./bin/companion tushare-canary daily --params '{"trade_date":"YYYYMMDD"}'
./bin/companion data-health
```

Canary 只允许写 `role=canary`，不能发布 ready Dataset Snapshot。每个端点须按 [数据资格报告](DATA-QUALIFICATION-v1.md)连续运行并完成 official golden cases 后，才可评审 G1。

生产摄取以 `job_run_id` 派生 Batch 幂等键：同一 JobRun 的故障重试复用原批次，不重复推进 cursor；新的 JobRun 可以重拉同一交易日/重叠窗口，以追加捕获源端修订。交互式 canary 默认保持单参数集幂等。字段完整但业务零行的 `empty_valid` 是成功终态并推进 cursor，不得伪报成故障；覆盖是否合理仍由 canary/golden evidence 单独判断。

G1 生产 assessment 还会要求首批 Stream 最新 probe 为 healthy 且不超过 72 小时；一次成功调用不能长期冒充当前能力。

## 6. Job Worker

确定性 Schedule 使用 `dispatch_type=deterministic_pipeline` 并引用已激活的 JobDefinition。定义激活与 `v4_jobs` feature 都要求 G0=Go；数据库只存 handler 名称，进程只执行代码内白名单。

手工离线 worker：

```bash
./bin/companion job-work --limit 1
```

仓库提供 `systemd/companion-job-worker.service` 与 `.timer`，但发布流程不得自动 enable。Worker 子进程默认网络拒绝、模型 Token 为零，并受 CPU、内存、文件句柄和 wall timeout 限制。

## 7. Gate 与 Feature

```bash
./bin/companion gate-list
./bin/companion feature-list
./bin/companion quant-health
./bin/companion data-health
./bin/companion v4-status
```

Gate evidence 必须绑定当前干净 Git commit、Schema、不可变 artifact、未知项和反证处置。生产 `Go` 只能由 Primary Codex 记录，并带用户明确 approval ref。代码更新会使旧 Gate assessment 失效；feature flag 不能绕过前置 Gate。

生产 Gate 证据按三步签发，且每一步都保留独立 Manifest：

```bash
./bin/companion gate-report-publish test_report \
  --checks '{"test_command_completed":true,"zero_failures":true,"v3_v4_suite_passed":true}' \
  --input-refs '["manifest_..."]' \
  --commands '["python3 -m pytest -q"]' \
  --observations '{"tests":64,"subtests":8}'

./bin/companion gate-evidence-publish G0 \
  --checks '{...G0 完整 checklist...}' \
  --artifacts '["manifest_test_report...","manifest_backup_restore_report...","manifest_run_lifecycle_report..."]'

./bin/companion gate-assess G0 go manifest_gate_evidence... \
  --approval-ref '用户批准记录'
```

Report 只打包可重放证据，不会自行产生 assessment；缺失精确 checklist、当前代码、必需报告、前置 Gate 或用户 approval 时 `Go` 会失败关闭。

## 8. 前向研究与人工闭环

透明策略完成 final holdout 并由 Primary 推进到 Shadow 后，先创建 Shadow Book，再用生产日线 Snapshot 调用 `v4_forward_signal_start`。实验日期不能由调用方提交；worker 只从冻结分区和交易日历生成下一开放日 target。

执行日 rebalance 必须提供两个不同快照：`signal_snapshot_id` 证明信号在开盘前可知，`execution_snapshot_id` 在收盘后提供仿真行情。随后用 `portfolio_rebalance_plan_calculate` 把 target、真实账户、冻结 Market Snapshot、RealitySpec 与当前 Mandate 联合求解。只有可行且非空的方案、精确单笔约束、完整 Agent critique provenance 和明确用户 opt-in 才能发布 beta Decision。

## 9. 故障处置

- `requires explicit migration`：当前数据库较旧；不要重试普通命令，按第 3 节安排迁移或切回稳定 V3；
- `release gates not satisfied`：缺少资格证据，不是系统故障；补证据，不能改数据库绕过；
- Job lease 过期：运行 `recover`，再由 worker 领取；父 Run、Job、Step 与 Experiment 必须保持同一最终状态；
- 对象 hash/路径/Manifest 校验失败：对应 Snapshot 立即不可消费，保留 Raw 和审计，重新规范化并发布新对象；
- Tushare `unauthorized/rate_limited/partial`：批次保持 blocked/failed，cursor 不前进，按 capability report 处理。
