# 市场数据 Adapter 与前瞻式影子候选评估 PRD

状态：**已废弃（Superseded），禁止按本文原方案实施**
原草案日期：2026-08-18
废弃日期：2026-08-18
取代文件：[V4-DESIGN.md](V4-DESIGN.md)、[V4-ARCHITECTURE-DECISIONS.md](V4-ARCHITECTURE-DECISIONS.md)、[V4-IMPLEMENTATION-PLAN.md](V4-IMPLEMENTATION-PLAN.md)、[V4-ACCEPTANCE.md](V4-ACCEPTANCE.md)

## 为什么废弃

原 PRD 把 V4 定义为“市场数据 Adapter + 主动候选发现 + 单候选前向评价”。它保留了若干正确局部设计，但不足以支撑用户确认的新 V4：

> 可审计、Codex 驱动、确定性量化内核支撑、飞书协作、人工执行的专业个人投资研究与决策系统。

原方案存在四个结构性问题：

1. 先用 AI/Web 扩大候选，再补数据与研究内核，容易复现“烧 Token 后交付股票列表”；
2. 以 selected/rejected/deferred 单候选表现为中心，不能证明完整组合、成本和风险的可投资性；
3. Shadow Trial 冻结 Thesis、风险、动作和证据，容易形成第二套 Cognitive Ledger；
4. 没有完整定义 PIT Dataset、Experiment Governance、typed Job、组合求解和人工执行协议。

## 被新 V4 保留的思想

- capability probe 区分连接器、权限、参数、陈旧、部分覆盖和合法空集；
- Adapter 游标、水位、幂等、Raw 原文、修订和 Source Health；
- knowledge cutoff、不可变记录和禁止事后改写；
- selected/rejected/deferred 的完整记录，可作为候选覆盖和假阴性分析；
- 不连接券商、不自动交易、不静默购买或绕过数据权限。

这些能力已经被重新放入完整架构：数据资格先于研究；候选评价从属于完整 denominator 与 Strategy Shadow Book；面向用户的动作继续使用 V3 Decision/Execution/Ledger/Review。

## 历史处置

旧草案正文不再保留在当前实施入口，避免后续开发者误执行旧 Phase 0→候选→Artifact 路线。如需追溯，其原始内容应通过 Git 历史或实施前备份查看。

任何后续开发以新 V4 四份权威文档为准；本文只保留废弃决定与迁移说明。
