# Investment Companion

一个生长在 Codex 内的个人全生命周期投资伴侣。Primary Codex 负责理解、调查组织、投资判断和用户沟通；Companion Core 只保存不可模糊的运行事实、调度状态和 Markdown 工作材料句柄。

## 初始化

```bash
git clone https://github.com/Lanbasara/investment-companion.git
cd investment-companion
./bin/companion workspace-init --finance-source /absolute/path/to/finance-feed
./bin/companion status
```

初始化是幂等的，不会清空已有数据。省略 `--finance-source` 可以稍后通过 Companion MCP 或自然语言接入信息源。

Tushare 凭据放在本机的 `~/.config/tushare/token`（权限建议 `0600`），不得提交到仓库。Custom Agents 通过 `bin/launch-tushare-mcp` 在运行时读取它。

## 架构

```text
systemd timer → Companion Schedule/Run/Outbox → cc-connect
              → Primary Codex → 短命 Scout/Gardener
              → Markdown 文件句柄与 SQLite 精确状态
```

从 [项目状态与交棒入口](docs/PROJECT-STATUS.md) 开始阅读。当前架构见 [V3 设计](docs/V3-DESIGN.md)，运行与恢复见 [运维手册](docs/V2-OPERATIONS.md)，历史决策见 [V2 设计](docs/V2-DESIGN.md)。
浏览器阅读入口为 [docs/index.html](docs/index.html)；它直接渲染上述权威 Markdown，不维护第二份易过期的文档副本。

## 验证

```bash
python3 -m pytest -q
./bin/companion recover
./bin/companion doctor
```

项目不连接券商、不自动交易、不维持常驻 Subagent。
