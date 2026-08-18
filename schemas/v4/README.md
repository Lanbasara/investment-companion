# V4 Contract Registry

V4 契约采用“代码内强校验 + 版本字符串 + 内容哈希”，避免另存一份未执行的 JSON Schema 成为第二真相。

| 契约族 | 当前版本 | 权威验证器 |
|---|---|---|
| Data Object / Snapshot / PIT | `v1` / `v2` | `companion/v4_data.py`, `companion/data_domain.py` |
| Native experiment spec | `investment-companion.native-experiment-spec/v3` | `DataDomain.partition_validate` |
| Target weights / simulation | `v1` | `companion/quant_runtime.py` |
| Experiment bundle / evaluator | `v3` / `native-evaluator/2` | `companion/research.py` |
| Target weights series | `investment-companion.target-weights-series/v1` | `Companion._job_native_quant` |
| Job input/output/resource budget | `v1` | `companion/jobs.py` |
| Gate evidence | `investment-companion.gate-evidence/v1` | `companion/governance.py` |
| Shadow rebalance result | `investment-companion.shadow-rebalance-result/v2` | `companion/shadow.py`、`companion/core.py` |
| Agent review provenance | `investment-companion.agent-review/v1`，外部调用键 `invocation_ref` 唯一 | `companion/cognition.py` |
| ManualActionSpec | V4 revision contract | `companion/cognition.py` |

`native-experiment-spec/v3` 禁止调用方嵌入研究日期；walk-forward pair 由 worker 从冻结分区、lookback 和调仓频率派生。生产 Shadow v2 分开记录 `signal_snapshot_id` 与 `execution_snapshot_id`。任何字段或语义变化必须更换版本、补迁移/兼容测试，并生成新内容哈希。此目录只做注册索引，运行时不得仅因某份静态 schema 文件通过就跳过语义验证。

NativeQuantRuntime 内层使用 `investment-companion/experiment-bundle/v1` 作为纯计算包装；ResearchRegistry 再封装为带物理分区、公司行动、策略代码和执行血缘的 `investment-companion.experiment-bundle/v3`。`quant-health` 分别报告这两个契约，二者不能互换。
