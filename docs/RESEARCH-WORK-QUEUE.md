# Research Work Queue：候选到完整研究的连续性契约

状态：Schema 9 开发完成，待生产迁移与灰度切换
日期：2026-08-27

## 目标

候选扫描只负责发现线索。Research Work Queue 确保每个非空股票/ETF候选批次都得到明确处理，避免“任务成功、候选已生成，但无人继续研究”的静默断链。

```text
Candidate Manifest
  → candidate_triage（逐项覆盖）
      ├─ reject：说明理由并终止
      ├─ monitor：给出 next_check_at，到期重新排队
      └─ research：创建 full_research
            → ResearchRecord + Thesis + Validation
            → Opportunity 或 reject / monitor
```

## 职责边界

- Queue 保存待办、期限、优先级、租约和结果引用，不复制研究或投资事实。
- Candidate Manifest 是冻结输入；ResearchRecord/Thesis/Validation 是研究真相；Opportunity 是投资机会漏斗。
- Queue 不能创建 Decision、Risk Gate、Action Card、Execution 或 Ledger Entry。
- ETF 候选按冻结的 `tracking_index` 比较并最多保留一个研究或观察代表；没有可靠跟踪指数标签时不假装完成机器分组。

## 状态与不变量

`queued → leased → completed/rejected`；需要等待明确事件时进入 `monitoring`，到达 `next_check_at` 自动回到 `queued`。租约过期也由恢复服务重排。

1. 每个候选 Manifest 只有一条幂等 triage 任务。
2. Triage 必须精确覆盖冻结的全部 candidate identity。
3. `research` 与 `monitor` 会产生可追踪的 full_research 子任务。
4. `promoted` 必须绑定同一 Program 的 Opportunity、active Thesis 与合格 Thesis Validation Calculation。
5. 未完成任务使 Investment Home 返回 `review_required`；逾期或最新候选缺任务使 Production Doctor 失败。
6. 存在未完成任务时，确定性代码拒绝发布 `no_action` Brief。

## Codex 使用面

不新增 MCP 工具数量：

- `investment_home` 展示队列摘要和是否逾期；
- `research_context` 展示队列、选中任务、ResearchRecord 与 Validation；
- `investment_opportunity_update` 使用 `work_claim / triage_complete / research_complete` 维护研究义务；
- 原有 `create / transition` 继续维护 Opportunity。

这保持 Codex 原生的统一切入面，不引入独立 Agent Runtime。

生产首次迁移后、运行 Doctor 前，执行 `./bin/companion research-work-backfill-latest`：它为股票与 ETF 各自最新的迁移前候选，以及迁移后全部候选补建任务；命令幂等，不重建其余历史候选。
