from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from .core import Companion, CompanionError
from .interfaces.mcp_profiles import INVESTMENT_TOOLS, RECONCILIATION_STATEMENT, active_tools, call_investment

ROOT=os.environ.get("COMPANION_ROOT","/home/ghk/investment-home")
C=Companion(ROOT)

def schema(properties:dict[str,Any]|None=None,required:list[str]|None=None)->dict[str,Any]:
    return {"type":"object","properties":properties or {},"required":required or [],"additionalProperties":False}

S={"type":"string"};I={"type":"integer"};O={"type":"object","additionalProperties":True}
TOOLS={
 "schedule_create":("创建可管理的主动计划。确定性任务必须引用已激活的 JobDefinition。",schema({"name":S,"kind":{"type":"string","enum":["patrol","review","maintenance","one_shot"]},"mission":S,"cadence":O,"scope":O,"policy":O,"origin":O,"timezone":S,"dispatch_type":{"type":"string","enum":["codex_turn","deterministic_pipeline"]},"job_definition_id":S},["name","kind","mission","cadence"])),
 "schedule_list":("列出主动计划及其状态和下次运行时间。",schema({"status":S,"kind":S})),
 "schedule_get":("读取一个主动计划的精确配置。",schema({"schedule_id":S},["schedule_id"])),
 "schedule_patch":("按字段精确修改计划，必须提供当前版本以防覆盖并发修改。",schema({"schedule_id":S,"expected_version":I,"changes":O,"reason":S},["schedule_id","expected_version","changes"])),
 "schedule_pause":("暂停计划但保留历史与材料。",schema({"schedule_id":S,"reason":S},["schedule_id"])),
 "schedule_resume":("恢复计划并重新计算下次运行时间。",schema({"schedule_id":S,"reason":S},["schedule_id"])),
 "schedule_archive":("归档计划并停止后续运行。",schema({"schedule_id":S,"reason":S},["schedule_id"])),
 "schedule_run_now":("为计划创建一次立即运行，不修改原频率。",schema({"schedule_id":S},["schedule_id"])),
 "schedule_history":("查看计划近期运行记录。",schema({"schedule_id":S,"limit":I},["schedule_id"])),
 "schedule_explain":("解释计划为什么存在、何时运行、来源和近期结果。",schema({"schedule_id":S},["schedule_id"])),
 "run_get":("读取一次不可变运行及当前状态。",schema({"run_id":S},["run_id"])),
 "run_list":("列出近期运行。",schema({"status":S,"limit":I})),
 "run_complete":("由 Primary Codex 登记本次到期任务成功或失败。",schema({"run_id":S,"success":{"type":"boolean"},"error":S},["run_id","success"])),
 "run_cancel":("取消尚未完成的运行并记录原因。",schema({"run_id":S,"reason":S},["run_id","reason"])),
 "watch_create":("创建观察意图。系统派生的观察必须有到期时间。",schema({"name":S,"subject_type":S,"subject_id":S,"intent":S,"condition":O,"schedule_id":S,"origin":O,"ttl_at":S,"max_runs":I},["name","subject_type","subject_id","intent","condition"])),
 "watch_list":("列出观察项。",schema({"status":S,"subject_id":S})),
 "watch_get":("读取观察项。",schema({"watch_id":S},["watch_id"])),
 "watch_patch":("按字段精确修改观察项，使用版本号防止覆盖。",schema({"watch_id":S,"expected_version":I,"changes":O,"reason":S},["watch_id","expected_version","changes"])),
 "watch_pause":("暂停观察项。",schema({"watch_id":S},["watch_id"])),
 "watch_resume":("恢复观察项。",schema({"watch_id":S},["watch_id"])),
 "watch_archive":("归档观察项。",schema({"watch_id":S},["watch_id"])),
 "observation_add":("登记来自确定性数据源的一次标准化采样。",schema({"subject_type":S,"subject_id":S,"metric":S,"value":{},"observed_at":S,"source":S,"source_ref":S,"quality":O,"watch_id":S},["subject_type","subject_id","metric","value","observed_at","source"])),
 "watch_evaluate":("用一项已登记 Observation 判断 Watch 条件，只在未成立到成立的跨越时产生事件。",schema({"watch_id":S,"observation_id":S},["watch_id","observation_id"])),
 "inbox_add":("登记用户文章、链接或调查材料，返回稳定文件句柄。",schema({"source":S,"title":S,"content":S,"url":S,"published_at":S,"source_key":S,"metadata":O},["source","title"])),
 "inbox_list":("读取增量信息 Inbox。",schema({"status":S,"limit":I})),
 "inbox_set_status":("标记一项材料已分流、关联、归档或判重。",schema({"item_id":S,"status":{"type":"string","enum":["new","triaged","linked","archived","duplicate"]}},["item_id","status"])),
 "source_health_record":("登记数据源成功、陈旧、部分、无权限或失败状态。",schema({"source":S,"status":S,"error":S,"cursor":S,"coverage":O},["source","status"])),
 "source_health_list":("列出所有数据源健康度、游标和连续失败。",schema()),
 "v5_research_quality_status":("读取主动研究的来源覆盖、调度延迟和交付质量记分卡。",schema({"days":I})),
 "event_list":("列出事件。",schema({"status":S,"limit":I})),
 "event_get":("读取事件事实与来源。",schema({"event_id":S},["event_id"])),
 "event_acknowledge":("记录 Primary Codex 对事件的处理结论。",schema({"event_id":S,"note":S},["event_id","note"])),
 "case_create":("建立跨会话调查案件并落盘调查委托。",schema({"title":S,"brief":S,"subject":O,"origin":O},["title","brief"])),
 "case_list":("列出调查案件。",schema({"status":S})),
 "case_get":("读取案件及文件句柄。",schema({"case_id":S},["case_id"])),
 "case_patch":("精确修改案件标题、标的或来源。",schema({"case_id":S,"expected_version":I,"changes":O,"reason":S},["case_id","expected_version","changes"])),
 "case_set_status":("由 Primary Codex 推进或结案。",schema({"case_id":S,"status":S,"reason":S},["case_id","status"])),
 "patrol_commission":("基于现有委托文件登记一次短命哨骑行动。此工具不直接派遣 Agent。",schema({"brief_path":S,"case_id":S,"schedule_id":S,"budget":O},["brief_path"])),
 "patrol_list":("列出哨骑行动。",schema({"status":S})),
 "patrol_get":("读取哨骑行动和结果文件句柄。",schema({"patrol_id":S},["patrol_id"])),
 "patrol_complete":("登记哨骑返回的工作材料及来源覆盖回执。",schema({"patrol_id":S,"result_path":S,"disposition":S,"evidence_receipt":O},["patrol_id","result_path","disposition"])),
 "artifact_register":("登记工作目录中的认知材料句柄。",schema({"path":S,"kind":S,"subject":O,"case_id":S,"watch_id":S,"event_id":S,"status":S,"effective_at":S,"supersedes":S},["path","kind"])),
 "artifact_list":("按案件和状态取得少量文件句柄。",schema({"case_id":S,"status":S,"limit":I})),
 "artifact_get":("读取一个认知材料的稳定文件句柄。",schema({"artifact_id":S},["artifact_id"])),
 "account_create":("创建投资账户事实容器，不保存券商凭据。",schema({"name":S,"base_currency":S,"institution":S,"metadata":O},["name","base_currency"])),
 "account_list":("列出投资账户。",schema()),
 "asset_upsert":("按稳定外部标识登记股票、ETF、基金、现金或其他资产。",schema({"asset_type":S,"name":S,"currency":S,"identifiers":O,"metadata":O},["asset_type","name","currency","identifiers"])),
 "asset_list":("列出已登记资产身份。",schema()),
 "ledger_add":("创建待确认金融流水；不会立即改变权威持仓。数量和金额使用十进制字符串。",schema({"account_id":S,"entry_type":S,"occurred_at":S,"amount":{},"currency":S,"source":S,"asset_id":S,"quantity":{},"price":{},"fee":{},"settled_at":S,"external_id":S,"metadata":O,"status":S},["account_id","entry_type","occurred_at","amount","currency","source"])),
 "ledger_list":("列出金融流水及确认状态。",schema({"account_id":S,"status":S,"limit":I})),
 "ledger_import_csv":("把标准 CSV 导入为待确认流水；不会自动改变持仓。",schema({"content":S,"source":S},["content"])),
 "ledger_confirm":("确认一条真实流水；确认后才影响持仓。",schema({"entry_id":S},["entry_id"])),
 "ledger_reverse":("用不可变冲销记录更正已确认流水。",schema({"entry_id":S,"reason":S,"occurred_at":S},["entry_id","reason"])),
 "market_snapshot_add":("登记计算使用的价格、汇率或其他市场观测及质量。",schema({"asset_id":S,"metric":S,"value":{},"observed_at":S,"source":S,"quality":S,"currency":S,"metadata":O},["asset_id","metric","value","observed_at","source"])),
 "portfolio_state_as_of":("从确认流水精确重建指定时点的现金和持仓，并生成 Calculation ID。",schema({"as_of":S,"account_id":S,"prices":O},["as_of"])),
 "trade_impact_simulate":("模拟交易对现金与持仓的影响，不写入真实流水。",schema({"as_of":S,"account_id":S,"asset_id":S,"quantity":{},"price":{},"fee":{},"mandate":O},["as_of","account_id","asset_id","quantity","price"])),
 "portfolio_rebalance_plan_calculate":("把冻结 target_weights、真实账户、报价、RealitySpec 与 Mandate 联合求解为可审计人工方案。",schema({"as_of":S,"account_id":S,"target_manifest_id":S,"market_snapshot_ids":{"type":"array","items":S},"reality_spec":O,"mandate":O,"max_price_age_seconds":I},["as_of","account_id","target_manifest_id","market_snapshot_ids","reality_spec","mandate"])),
 "max_purchase_calculate":("在现金底线、费用和最小交易单位下精确计算最大买入量。",schema({"as_of":S,"account_id":S,"asset_id":S,"price":{},"minimum_cash":{},"fee":{},"lot_size":{}},["as_of","account_id","asset_id","price"])),
 "portfolio_exposure_calculate":("按指定基准币种计算组合权重；缺失汇率时明确警告而不猜测。",schema({"as_of":S,"account_id":S,"prices":O,"base_currency":S},["as_of","account_id","prices","base_currency"])),
 "calculation_get":("读取可重放的精确计算记录。",schema({"calculation_id":S},["calculation_id"])),
 "portfolio_reconcile":("将账本派生状态与券商账单对账；statement 必须包含 cash、positions、position_values、position_total_by_currency、total_by_currency，全部验证才返回 matched，差异不自动补平。",schema({"account_id":S,"as_of":S,"statement":RECONCILIATION_STATEMENT,"source_ref":S},["account_id","as_of","statement"])),
 "context_revision_create":("创建 Investor、Mandate 或 Attention Policy 草稿版本。",schema({"context_type":S,"content":O,"reason":S,"effective_from":S,"expires_at":S},["context_type","content"])),
 "context_revision_confirm":("经用户确认后启用 Context 版本；trial 必须有到期时间。",schema({"revision_id":S,"trial":{"type":"boolean"}},["revision_id"])),
 "context_current":("读取当前生效的个人事实、Mandate 或 Attention Policy。",schema({"context_type":S},["context_type"])),
 "context_revision_list":("列出 Context 版本史。",schema({"context_type":S})),
 "cognitive_object_create":("创建 Thesis、Decision 或 Review 的稳定认知对象。",schema({"object_type":S,"subject":O,"status":S},["object_type","subject"])),
 "cognitive_object_list":("列出稳定认知对象。",schema({"object_type":S,"status":S})),
 "cognitive_revision_publish":("原子发布不可变完整认知版本；Decision 必须冻结精确上下文。",schema({"object_id":S,"content":S,"knowledge_cutoff":S,"context_refs":O,"calculation_ids":{"type":"array","items":S},"metadata":O},["object_id","content"])),
 "cognitive_revision_get":("读取一个不可变认知版本及文件句柄。",schema({"revision_id":S},["revision_id"])),
 "cognitive_link":("建立少量明确认知链接。",schema({"from_id":S,"to_id":S,"link_type":S,"metadata":O},["from_id","to_id","link_type"])),
 "execution_create":("创建建议之后、成交之前的 Execution 意图。",schema({"decision_id":S,"details":O},["details"])),
 "execution_set_status":("推进 Execution；filled 状态必须关联已确认流水，拒绝/取消/偏离等终态必须说明原因。",schema({"execution_id":S,"status":S,"ledger_entry_ids":{"type":"array","items":S},"reason":S},["execution_id","status"])),
 "recovery_package_create":("为跨对话恢复组装有界、可审计的最小上下文包。",schema({"purpose":S,"subject":O,"max_handles":I},["purpose","subject"])),
 "attention_decide":("使用当前 Attention Policy 对主动消息执行确定性门控并记录理由。",schema({"topic":S,"materiality":S,"confidence":S,"reason":S,"event_id":S,"evidence":{"type":"array"},"requested_action":S},["topic","materiality","confidence","reason"])),
 "attention_decision_list":("列出通知、摘要、落盘或抑制决定。",schema({"action":S,"limit":I})),
 "attention_feedback":("登记用户对主动消息的反馈；负反馈只产生策略调整提案。",schema({"decision_id":S,"feedback":S,"note":S},["decision_id","feedback"])),
 "attention_mark_delivered":("在主动消息实际送达后登记投递状态。",schema({"decision_id":S},["decision_id"])),
 "delivery_get":("读取一次任务的用户结果交付状态；运行成功不代表已交付。",schema({"delivery_id":S},["delivery_id"])),
 "delivery_list":("列出待编写、待发送、重试、已送达或失败的结果交付记录。",schema({"status":S,"mode":S,"limit":I})),
 "delivery_prepare":("为必报或摘要任务冻结用户结果；必报结果将进入可重试的 cc-connect 发送队列。",schema({"delivery_id":S,"conclusion":{"type":"string","enum":["no_action","action","review_required","insufficient_evidence"]},"summary":S,"key_evidence":{"type":"array","items":S},"next_step":S,"next_check_at":S,"source_refs":{"type":"array","items":S}},["delivery_id","conclusion","summary","key_evidence","next_step"])),
 "delivery_digest_send":("把多个摘要任务的结果合并为一条用户消息并提交实际发送；只接受同一会话的 digest_required 记录。",schema({"delivery_ids":{"type":"array","items":S},"conclusion":{"type":"string","enum":["no_action","action","review_required","insufficient_evidence"]},"summary":S,"key_evidence":{"type":"array","items":S},"next_step":S,"next_check_at":S,"source_refs":{"type":"array","items":S}},["delivery_ids","conclusion","summary","key_evidence","next_step"])),
 "delivery_status":("读取 V7 结果交付积压、失败和逾期必报任务。",schema()),
 "delivery_migrate_schedule_policies":("预览或应用现有 Schedule 到 V7 delivery_mode 的最小字段迁移。",schema({"apply":{"type":"boolean"}})),
 "v4_status":("读取 V4 Gate、Feature、数据、量化内核和合格策略总状态；不会开启任何能力。",schema()),
 "v4_feature_list":("读取所有 V4 Feature Flag；Feature 只能在发布流程中启用。",schema()),
 "v4_gate_list":("读取指定范围的 Gate 评估历史。",schema({"scope":{"type":"string","enum":["production","test_fixture"]}})),
 "v4_job_definition_list":("列出确定性 JobDefinition。",schema({"status":S})),
 "v4_job_run_list":("列出确定性 JobRun。",schema({"status":S,"limit":I})),
 "v4_job_run_get":("读取 JobRun、Step、资源用量和产物。",schema({"job_run_id":S},["job_run_id"])),
 "v4_data_health":("读取数据根、阻断问题与真实 Probe 状态。",schema()),
 "v4_manifest_get":("读取并校验一个不可变研究 Manifest。",schema({"manifest_id":S},["manifest_id"])),
 "v4_source_capability_list":("列出 Adapter Probe 产生的能力记录。",schema({"provider":S,"capability":S})),
 "v4_asset_identity_list":("读取带生效期和知识截止语义的 provider 证券身份版本。",schema({"provider":S,"identifier_value":S})),
 "v4_agent_review_record":("冻结材料性 Agent 评审的调用标识、模型、输入引用、Token、输出与采用决定；不会发布投资判断。",schema({"invocation_ref":S,"role":S,"model":S,"prompt_template":S,"input_refs":{"type":"array","items":S},"review":O,"token_usage":O,"started_at":S,"finished_at":S,"adopted":{"type":"boolean"},"adoption_reason":S},["invocation_ref","role","model","prompt_template","input_refs","review","token_usage","started_at","finished_at","adopted","adoption_reason"])),
 "v4_agent_review_get":("读取并校验一个不可变 Agent 评审及其输出血缘。",schema({"invocation_id":S},["invocation_id"])),
 "v4_dataset_snapshot_get":("读取并校验一个不可变 DatasetSnapshot。",schema({"snapshot_id":S},["snapshot_id"])),
 "v4_hypothesis_create":("预注册研究假设与严格试验预算。",schema({"name":S,"spec":O,"experiment_budget":I,"preregister":{"type":"boolean"}},["name","spec","experiment_budget"])),
 "v4_hypothesis_list":("列出研究假设注册表。",schema({"status":S})),
 "v4_strategy_register":("注册不可变 StrategySpec；必须声明物理分区、基准、成本和通过条件。",schema({"hypothesis_id":S,"spec":O,"code_ref":S,"environment_ref":S,"parent_id":S},["hypothesis_id","spec","code_ref","environment_ref"])),
 "v4_strategy_list":("列出 StrategyVersion。",schema({"hypothesis_id":S,"status":S})),
 "v4_experiment_start":("按预注册预算创建 ExperimentRun；不运行模型。",schema({"strategy_version_id":S,"dataset_snapshot_id":S,"split":O,"params":O,"seed":I},["strategy_version_id","dataset_snapshot_id","split"])),
 "v4_forward_signal_start":("为已进入 Shadow 的策略创建不占调参预算的前向信号 ExperimentRun。",schema({"strategy_version_id":S,"dataset_snapshot_id":S,"partition_names":{"type":"array","items":S}},["strategy_version_id","dataset_snapshot_id","partition_names"])),
 "v4_experiment_submit":("冻结无内嵌行情的实验 Spec，并提交受限确定性 Job。",schema({"experiment_id":S,"spec":O},["experiment_id","spec"])),
 "v4_experiment_list":("列出实验及失败记录。",schema({"strategy_version_id":S,"status":S})),
 "v4_promotion_decide":("由 Primary 依据注册指标拒绝、修订或推进策略；不能绕过确定性 Gate。",schema({"experiment_id":S,"decision":S,"reason":S,"evidence":O},["experiment_id","decision","reason"])),
 "v4_shadow_book_create":("创建与真实账本物理隔离的连续前向 Shadow Book。",schema({"strategy_version_id":S,"name":S,"initial_cash":{},"reality_spec":O,"sample_gate":O,"base_currency":S},["strategy_version_id","name","initial_cash","reality_spec","sample_gate"])),
 "v4_shadow_rebalance":("使用隔离的信号/执行快照、冻结 Target 和上次 Shadow 状态确定性模拟一次前向调仓。",schema({"book_id":S,"signal_snapshot_id":S,"execution_snapshot_id":S,"experiment_run_id":S,"as_of":S,"target_manifest_id":S,"denominator_hash":S},["book_id","signal_snapshot_id","execution_snapshot_id","experiment_run_id","as_of","target_manifest_id","denominator_hash"])),
 "v4_shadow_book_list":("列出 Shadow Book。",schema({"status":S})),
 "v4_shadow_sample_status":("读取真实经过时间、调仓、Decision、市场状态和连续性样本。",schema({"book_id":S},["book_id"])),
 "v4_manual_action_create":("从当前已签发 V4 Decision 生成可重验证、只供人工执行的 ManualActionSpec。",schema({"decision_revision_id":S,"spec":O},["decision_revision_id","spec"])),
 "v4_manual_action_get":("读取 ManualActionSpec。",schema({"spec_id":S},["spec_id"])),
 "v4_manual_action_validate":("用最新账本、上下文和健康行情重新验证 ManualActionSpec；as_of 仅允许当前时刻 ±5 秒。",schema({"spec_id":S,"as_of":S},["spec_id"])),
 "v4_manual_action_set_status":("记录 ManualActionSpec 的呈现、用户接受或拒绝。",schema({"spec_id":S,"status":S,"reason":S},["spec_id","status"])),
 "v4_execution_from_action":("从通过实时重验证的 ManualActionSpec 创建人工 Execution；不会连接券商。",schema({"spec_id":S,"idempotency_key":S},["spec_id","idempotency_key"])),
 "wake_claim":("由被静态 cron 唤醒的 Primary Codex 原子领取真正触发本次唤醒的一个信封；兼容旧 Run 和 V4 研究事件。",schema({"owner":S,"lease_seconds":I},["owner"])),
 "wake_complete":("在 Run 或事件确实处理后结束唤醒信封；成功的 scheduled_run 必须已先调用 run_complete。",schema({"outbox_id":S,"owner":S,"success":{"type":"boolean"},"error":S},["outbox_id","owner","success"])),
 "v5_status":("读取 V5 投资经营计划、机会漏斗和用户决策队列的确定性状态。",schema()),
 "v5_quant_research_status":("读取持续真实数据量化研究、每日扫描、月度复盘和运行状态；不会开启能力。",schema()),
 "v5_quant_experiment_status":("兼容旧客户端：读取持续量化研究状态；系统已不再采用限期试用。",schema()),
 "v5_quant_scan_get":("读取并校验一个持续量化扫描；它可触发完整研究，但本身不是行动建议。",schema({"manifest_id":S},["manifest_id"])),
 "v5_quant_review_get":("读取并校验一个月度严格前向复盘及其确定性指标。",schema({"manifest_id":S},["manifest_id"])),
 "v6_predictive_status":("读取 V6 股票与基金预测任务、月度反馈复核及其边界；不会开启能力。",schema()),
 "v6_forecast_get":("读取并校验一条冻结的 V6 股票或基金预测；它不是 Decision 或交易。",schema({"manifest_id":S},["manifest_id"])),
 "v6_fund_universe_get":("读取并校验冻结的 V6 ETF 标的池及排除原因；它不是基金推荐。",schema({"manifest_id":S},["manifest_id"])),
 "v6_fund_feature_snapshot_get":("读取冻结的 V6 ETF 特征快照；它不是预测或交易。",schema({"manifest_id":S},["manifest_id"])),
 "v6_fund_candidates_get":("读取透明 ETF 研究候选；它不是预测、推荐或交易。",schema({"manifest_id":S},["manifest_id"])),
 "v6_fund_data_bundle_get":("读取 V6 ETF 数据采集、预热与特征快照状态；不会推荐或交易。",schema({"manifest_id":S},["manifest_id"])),
 "v6_fund_provisional_signals_get":("读取冻结的 V6 ETF 暂定推荐及其前向验证状态；不会下单。",schema({"manifest_id":S},["manifest_id"])),
 "v6_stock_candidates_get":("读取 V6 股票研究候选；不会下单。",schema({"manifest_id":S},["manifest_id"])),
 "v6_stock_provisional_signals_get":("读取 V6 股票暂定预测信号及其验证状态；不会下单。",schema({"manifest_id":S},["manifest_id"])),
 "v5_today":("读取面向用户的今日入口：设置缺口、行动卡、无行动结论或待复核状态。",schema()),
 "v5_program_create":("创建投资经营计划草稿；只引用现有 Context 和账户，不复制投资事实。",schema({"name":S,"content":O,"context_refs":O,"reason":S,"expires_at":S},["name","content","context_refs","reason"])),
 "v5_program_revise":("按乐观版本创建投资经营计划的新草稿版本。",schema({"program_id":S,"expected_version":I,"content":O,"context_refs":O,"reason":S,"expires_at":S},["program_id","expected_version","content","context_refs","reason"])),
 "v5_program_confirm":("依据明确用户批准启用一个计划版本；试用版必须有未来到期时间。",schema({"revision_id":S,"user_approval_ref":S,"trial":{"type":"boolean"},"supersedes_program_id":S},["revision_id","user_approval_ref"])),
 "v5_program_set_status":("暂停、恢复或归档投资经营计划；不会修改其历史版本。",schema({"program_id":S,"status":{"type":"string","enum":["active","paused","archived"]},"reason":S},["program_id","status","reason"])),
 "v5_program_get":("读取投资经营计划及其不可变版本史。",schema({"program_id":S},["program_id"])),
 "v5_program_list":("列出投资经营计划。",schema({"status":S})),
 "v5_program_current":("读取当前唯一生效的投资经营计划。",schema()),
 "v5_opportunity_create":("把有不可变证据来源的想法登记为 observed 机会；不是选股结论。",schema({"subject":O,"evidence_refs":{"type":"array","items":S},"reason":S,"program_id":S,"thesis_id":S,"strategy_version_id":S},["subject","evidence_refs","reason"])),
 "v5_opportunity_get":("读取机会、证据成熟度和完整转换历史。",schema({"opportunity_id":S},["opportunity_id"])),
 "v5_opportunity_list":("读取机会漏斗；stage 是证据成熟度，不是模型置信概率。",schema({"program_id":S,"stage":S,"status":S,"limit":I})),
 "v5_opportunity_transition":("按严格顺序推进机会证据；qualified 必须引用 Research Validation Calculation，actionable 还必须绑定当前有效 Decision 与 Risk Gate。",schema({"opportunity_id":S,"expected_version":I,"to_stage":S,"to_status":S,"evidence_refs":{"type":"array","items":S},"reason":S,"qualification":O,"decision_revision_id":S,"idempotency_key":S},["opportunity_id","expected_version","to_stage","to_status","evidence_refs","reason"])),
 "v5_decision_queue_enqueue":("把 actionable Opportunity 的当前 Decision 放入人工决策队列；不创建 Execution。",schema({"opportunity_id":S,"decision_revision_id":S,"manual_action_spec_id":S,"valid_until":S,"idempotency_key":S},["opportunity_id","decision_revision_id"])),
 "v5_decision_queue_get":("读取一个用户决策队列项。",schema({"queue_id":S},["queue_id"])),
 "v5_decision_queue_list":("列出用户决策队列；过期项会确定性失效。",schema({"program_id":S,"state":S,"limit":I})),
 "v5_action_card":("生成一个只供人工判断和执行的结构化行动卡，并实时重验证 ManualActionSpec。",schema({"queue_id":S},["queue_id"])),
 "v5_decision_queue_respond":("记录呈现、定时稍后处理、接受、拒绝或关闭；接受仍不会自动成交。",schema({"queue_id":S,"state":S,"reason":S,"snoozed_until":S,"attention_decision_id":S},["queue_id","state"])),
 "v5_brief_prepare":("生成日/周/月用户简报记录；no_action 只能在没有有效行动队列时成立。",schema({"brief_type":S,"period_key":S,"as_of":S,"conclusion":S,"payload":O,"source_refs":{"type":"array","items":S},"idempotency_key":S,"program_id":S},["brief_type","period_key","as_of","conclusion","payload","source_refs"])),
 "v5_brief_get":("读取一个可审计用户简报。",schema({"brief_id":S},["brief_id"])),
 "v5_brief_list":("列出当前版本的用户简报。",schema({"program_id":S,"brief_type":S,"limit":I})),
 "v5_brief_mark_presented":("在飞书实际送达后，把简报关联到已送达 AttentionDecision。",schema({"brief_id":S,"attention_decision_id":S},["brief_id","attention_decision_id"])),
 "v5_program_metrics_calculate":("确定性计算 Program 在一个周期内的机会、决策和简报流量；缺失的收益、时间与成本数据会明确标为 insufficient_evidence。",schema({"period_start":S,"period_end":S,"program_id":S},["period_start","period_end"])),
 "v5_scorecard_publish":("发布月度结果记分卡；所有数值必须从 Calculation output_path 解析，模型不能直接填写。",schema({"period_start":S,"period_end":S,"metrics":{"type":"array","items":O},"comparisons":{"type":"array","items":O},"source_refs":{"type":"array","items":S},"caveats":{"type":"array","items":S},"program_id":S},["period_start","period_end","metrics","comparisons","source_refs","caveats"])),
 "v5_scorecard_get":("读取并重新校验一个结果记分卡的 Calculation 血缘。",schema({"scorecard_id":S},["scorecard_id"])),
 "v5_scorecard_list":("列出投资经营计划的结果记分卡。",schema({"program_id":S,"limit":I})),
 "system_status":("查看数据库、调度、投递和恢复状态。",schema()),
 "system_doctor":("诊断数据库、工作区、个人上下文和金融事实准备度。",schema()),
}


def call(name:str,a:dict[str,Any]):
    definition=active_tools(TOOLS).get(name)
    if not definition:raise CompanionError(f"unknown tool: {name}")
    from .jobs import _validate_schema
    _validate_schema(a,definition[1],f"{name} arguments")
    actor="primary-codex"
    if name in INVESTMENT_TOOLS:return call_investment(C,name,a,actor)
    if name=="schedule_create":return C.schedule_create(**a,actor=actor)
    if name=="schedule_list":return C.schedule_list(a.get("status"),a.get("kind"))
    if name=="schedule_get":return C.schedule_get(a["schedule_id"])
    if name=="schedule_patch":return C.schedule_patch(a["schedule_id"],a["expected_version"],a["changes"],actor,a.get("reason"))
    if name.startswith("schedule_") and name in {"schedule_pause","schedule_resume","schedule_archive"}:return C.schedule_set_status(a["schedule_id"],name.split("_")[1].replace("resume","active").replace("pause","paused").replace("archive","archived"),actor,a.get("reason"))
    if name=="schedule_run_now":return C.schedule_run_now(a["schedule_id"],actor)
    if name=="schedule_history":return C.schedule_history(a["schedule_id"],a.get("limit",20))
    if name=="schedule_explain":return C.schedule_explain(a["schedule_id"])
    if name=="run_get":return C.run_get(a["run_id"])
    if name=="run_list":return C.run_list(a.get("status"),a.get("limit",50))
    if name=="run_complete":return C.complete_run(a["run_id"],a["success"],a.get("error"))
    if name=="run_cancel":return C.run_cancel(a["run_id"],a["reason"],actor)
    if name=="watch_create":return C.watch_create(**a,actor=actor)
    if name=="watch_list":return C.watch_list(a.get("status"),a.get("subject_id"))
    if name=="watch_get":return C.watch_get(a["watch_id"])
    if name=="watch_patch":return C.watch_patch(a["watch_id"],a["expected_version"],a["changes"],actor,a.get("reason"))
    if name in {"watch_pause","watch_resume","watch_archive"}:return C.watch_set_status(a["watch_id"],{"watch_pause":"paused","watch_resume":"active","watch_archive":"archived"}[name],actor)
    if name=="observation_add":return C.observation_add(**a)
    if name=="watch_evaluate":return C.evaluate_watch(a["watch_id"],a["observation_id"])
    if name=="inbox_add":return C.inbox_add(**a)
    if name=="inbox_list":return C.inbox_list(a.get("status","new"),a.get("limit",100))
    if name=="inbox_set_status":return C.inbox_set_status(a["item_id"],a["status"],actor)
    if name=="source_health_record":return C.source_health_record(**a)
    if name=="source_health_list":return C.source_health_list()
    if name=="v5_research_quality_status":return C.research_quality_status(a.get("days",30))
    if name=="event_list":return C.event_list(a.get("status"),a.get("limit",50))
    if name=="event_get":return C.event_get(a["event_id"])
    if name=="event_acknowledge":return C.event_acknowledge(a["event_id"],a["note"],actor)
    if name=="case_create":return C.case_create(**a,actor=actor)
    if name=="case_list":return C.case_list(a.get("status"))
    if name=="case_get":return C.case_get(a["case_id"])
    if name=="case_patch":return C.case_patch(a["case_id"],a["expected_version"],a["changes"],actor,a.get("reason"))
    if name=="case_set_status":return C.case_set_status(a["case_id"],a["status"],a.get("reason"),actor)
    if name=="patrol_commission":return C.patrol_commission(**a,actor=actor)
    if name=="patrol_list":return C.patrol_list(a.get("status"))
    if name=="patrol_get":return C.patrol_get(a["patrol_id"])
    if name=="patrol_complete":return C.patrol_complete(a["patrol_id"],a["result_path"],a["disposition"],a.get("evidence_receipt"))
    if name=="artifact_register":return C.artifact_register(**a)
    if name=="artifact_list":return C.artifact_list(a.get("case_id"),a.get("status"),a.get("limit",100))
    if name=="artifact_get":return C.artifact_get(a["artifact_id"])
    if name=="account_create":return C.financial.account_create(**a)
    if name=="account_list":return C.financial.account_list()
    if name=="asset_upsert":return C.financial.asset_upsert(**a)
    if name=="asset_list":return C.financial.asset_list()
    if name=="ledger_add":return C.financial.ledger_add(**a)
    if name=="ledger_list":return C.financial.ledger_list(a.get("account_id"),a.get("status"),a.get("limit",200))
    if name=="ledger_import_csv":return C.financial.ledger_import_csv(a["content"],a.get("source","broker_csv"))
    if name=="ledger_confirm":return C.financial.ledger_confirm(a["entry_id"])
    if name=="ledger_reverse":return C.financial.ledger_reverse(a["entry_id"],a["reason"],a.get("occurred_at"))
    if name=="market_snapshot_add":return C.financial.market_add(**a)
    if name=="portfolio_state_as_of":return C.financial.portfolio_state(a["as_of"],a.get("account_id"),a.get("prices"))
    if name=="trade_impact_simulate":return C.financial.trade_impact(a["as_of"],a["account_id"],a["asset_id"],a["quantity"],a["price"],a.get("fee","0"),a.get("mandate"))
    if name=="portfolio_rebalance_plan_calculate":return C.financial.portfolio_rebalance_plan(as_of=a["as_of"],account_id=a["account_id"],target_manifest_id=a["target_manifest_id"],market_snapshot_ids=a["market_snapshot_ids"],reality_spec=a["reality_spec"],mandate=a["mandate"],max_price_age_seconds=a.get("max_price_age_seconds",129600))
    if name=="max_purchase_calculate":return C.financial.max_purchase(a["as_of"],a["account_id"],a["asset_id"],a["price"],a.get("minimum_cash","0"),a.get("fee","0"),a.get("lot_size","1"))
    if name=="portfolio_exposure_calculate":return C.financial.portfolio_exposure(a["as_of"],a["account_id"],a["prices"],a["base_currency"])
    if name=="calculation_get":return C.financial.calculation_get(a["calculation_id"])
    if name=="portfolio_reconcile":return C.financial.reconcile(a["account_id"],a["as_of"],a["statement"],a.get("source_ref"))
    if name=="context_revision_create":return C.cognition.context_create(**a)
    if name=="context_revision_confirm":return C.cognition.context_confirm(a["revision_id"],a.get("trial",False))
    if name=="context_current":return C.cognition.context_current(a["context_type"])
    if name=="context_revision_list":return C.cognition.context_list(a.get("context_type"))
    if name=="cognitive_object_create":return C.cognition.object_create(a["object_type"],a["subject"],a.get("status","proposed"))
    if name=="cognitive_object_list":return C.cognition.object_list(a.get("object_type"),a.get("status"))
    if name=="cognitive_revision_publish":return C.cognition.publish(a["object_id"],a["content"],a.get("knowledge_cutoff"),a.get("context_refs"),a.get("calculation_ids"),a.get("metadata"))
    if name=="cognitive_revision_get":return C.cognition.revision_get(a["revision_id"])
    if name=="cognitive_link":return C.cognition.link(a["from_id"],a["to_id"],a["link_type"],a.get("metadata"))
    if name=="execution_create":return C.cognition.execution_create(a.get("decision_id"),a["details"])
    if name=="execution_set_status":return C.cognition.execution_set_status(a["execution_id"],a["status"],a.get("ledger_entry_ids"),a.get("reason"))
    if name=="recovery_package_create":return C.cognition.recovery_package(a["purpose"],a["subject"],a.get("max_handles",20))
    if name=="attention_decide":return C.attention.decide(**a)
    if name=="attention_decision_list":return C.attention.list(a.get("action"),a.get("limit",100))
    if name=="attention_feedback":return C.attention.feedback(a["decision_id"],a["feedback"],a.get("note"))
    if name=="attention_mark_delivered":return C.attention.mark_delivered(a["decision_id"])
    if name=="delivery_get":return C.delivery.get(a["delivery_id"])
    if name=="delivery_list":return C.delivery.list(status=a.get("status"),mode=a.get("mode"),limit=a.get("limit",100))
    if name=="delivery_prepare":return C.delivery.prepare(a["delivery_id"],conclusion=a["conclusion"],summary=a["summary"],key_evidence=a["key_evidence"],next_step=a["next_step"],next_check_at=a.get("next_check_at"),source_refs=a.get("source_refs"))
    if name=="delivery_digest_send":return C.delivery.digest_send(a["delivery_ids"],conclusion=a["conclusion"],summary=a["summary"],key_evidence=a["key_evidence"],next_step=a["next_step"],next_check_at=a.get("next_check_at"),source_refs=a.get("source_refs"))
    if name=="delivery_status":return C.delivery.status()
    if name=="delivery_migrate_schedule_policies":return C.delivery.migrate_schedule_policies(apply=a.get("apply",False),actor=actor)
    if name=="v4_status":return C.v4_status()
    if name=="v4_feature_list":return C.jobs.feature_list()
    if name=="v4_gate_list":return C.gates.list(a.get("scope",C.gate_scope))
    if name=="v4_job_definition_list":return C.jobs.definition_list(a.get("status"))
    if name=="v4_job_run_list":return C.jobs.run_list(a.get("status"),a.get("limit",50))
    if name=="v4_job_run_get":return C.jobs.run_get(a["job_run_id"])
    if name=="v4_data_health":return C.data.health()
    if name=="v4_manifest_get":return C.data.manifest_get(a["manifest_id"],verify=True)
    if name=="v4_source_capability_list":return C.data.capability_list(a.get("provider"),a.get("capability"))
    if name=="v4_asset_identity_list":return C.data.identity_list(a.get("provider"),a.get("identifier_value"))
    if name=="v4_agent_review_record":return C.cognition.agent_review_record(**a)
    if name=="v4_agent_review_get":return C.cognition.agent_review_get(a["invocation_id"])
    if name=="v4_dataset_snapshot_get":return C.data.snapshot_get(a["snapshot_id"],verify=True)
    if name=="v4_hypothesis_create":return C.research.hypothesis_create(name=a["name"],spec=a["spec"],experiment_budget=a["experiment_budget"],preregister=a.get("preregister",True))
    if name=="v4_hypothesis_list":return C.research.hypothesis_list(a.get("status"))
    if name=="v4_strategy_register":return C.research.strategy_register(hypothesis_id=a["hypothesis_id"],spec=a["spec"],code_ref=a["code_ref"],environment_ref=a["environment_ref"],parent_id=a.get("parent_id"))
    if name=="v4_strategy_list":return C.research.strategy_list(a.get("hypothesis_id"),a.get("status"))
    if name=="v4_experiment_start":return C.research.experiment_start(strategy_version_id=a["strategy_version_id"],dataset_snapshot_id=a["dataset_snapshot_id"],split=a["split"],params=a.get("params"),seed=a.get("seed",0))
    if name=="v4_forward_signal_start":return C.research.forward_signal_start(strategy_version_id=a["strategy_version_id"],dataset_snapshot_id=a["dataset_snapshot_id"],partition_names=a["partition_names"])
    if name=="v4_experiment_submit":return C.research.experiment_submit(a["experiment_id"],a["spec"])
    if name=="v4_experiment_list":return C.research.experiment_list(a.get("strategy_version_id"),a.get("status"))
    if name=="v4_promotion_decide":return C.research.promotion_decide(a["experiment_id"],a["decision"],a["reason"],a.get("evidence"),actor)
    if name=="v4_shadow_book_create":return C.shadow.book_create(strategy_version_id=a["strategy_version_id"],name=a["name"],initial_cash=a["initial_cash"],reality_spec=a["reality_spec"],sample_gate=a["sample_gate"],base_currency=a.get("base_currency","CNY"))
    if name=="v4_shadow_rebalance":return C.shadow.rebalance_record(**a)
    if name=="v4_shadow_book_list":return C.shadow.book_list(a.get("status"))
    if name=="v4_shadow_sample_status":return C.shadow.sample_status(a["book_id"])
    if name=="v4_manual_action_create":return C.cognition.manual_action_create(a["decision_revision_id"],a["spec"])
    if name=="v4_manual_action_get":return C.cognition.manual_action_get(a["spec_id"])
    if name=="v4_manual_action_validate":return C.cognition.manual_action_validate(a["spec_id"],a.get("as_of"))
    if name=="v4_manual_action_set_status":return C.cognition.manual_action_set_status(a["spec_id"],a["status"],a.get("reason"))
    if name=="v4_execution_from_action":return C.cognition.execution_create_from_action(a["spec_id"],a["idempotency_key"])
    if name=="wake_claim":return C.wake_claim(a["owner"],a.get("lease_seconds",1800))
    if name=="wake_complete":return C.wake_complete(a["outbox_id"],a["owner"],a["success"],a.get("error"))
    if name=="v5_status":return C.v5_status()
    if name in {"v5_quant_research_status","v5_quant_experiment_status"}:return C.quant_research.status()
    if name=="v5_quant_scan_get":return C.quant_research.scan_get(a["manifest_id"])
    if name=="v5_quant_review_get":return C.quant_research.review_get(a["manifest_id"])
    if name=="v6_predictive_status":return C.v6_predictive.status()
    if name=="v6_forecast_get":return C.v6_predictive.forecast_get(a["manifest_id"])
    if name=="v6_fund_universe_get":return C.v6_predictive.fund_universe_get(a["manifest_id"])
    if name=="v6_fund_feature_snapshot_get":return C.v6_predictive.fund_feature_snapshot_get(a["manifest_id"])
    if name=="v6_fund_candidates_get":return C.v6_predictive.fund_candidates_get(a["manifest_id"])
    if name=="v6_fund_data_bundle_get":return C.v6_predictive.fund_data_bundle_get(a["manifest_id"])
    if name=="v6_fund_provisional_signals_get":return C.v6_predictive.fund_provisional_signals_get(a["manifest_id"])
    if name=="v6_stock_candidates_get":return C.v6_predictive.stock_candidates_get(a["manifest_id"])
    if name=="v6_stock_provisional_signals_get":return C.v6_predictive.stock_provisional_signals_get(a["manifest_id"])
    if name=="v5_today":return C.operating.today()
    if name=="v5_program_create":return C.operating.program_create(name=a["name"],content=a["content"],context_refs=a["context_refs"],reason=a["reason"],expires_at=a.get("expires_at"),actor=actor)
    if name=="v5_program_revise":return C.operating.program_revise(program_id=a["program_id"],expected_version=a["expected_version"],content=a["content"],context_refs=a["context_refs"],reason=a["reason"],expires_at=a.get("expires_at"),actor=actor)
    if name=="v5_program_confirm":return C.operating.program_confirm(a["revision_id"],user_approval_ref=a["user_approval_ref"],trial=a.get("trial",False),supersedes_program_id=a.get("supersedes_program_id"),actor=actor)
    if name=="v5_program_set_status":return C.operating.program_set_status(a["program_id"],a["status"],reason=a["reason"],actor=actor)
    if name=="v5_program_get":return C.operating.program_get(a["program_id"])
    if name=="v5_program_list":return C.operating.program_list(a.get("status"))
    if name=="v5_program_current":return C.operating.program_current()
    if name=="v5_opportunity_create":return C.operating.opportunity_create(subject=a["subject"],evidence_refs=a["evidence_refs"],reason=a["reason"],program_id=a.get("program_id"),thesis_id=a.get("thesis_id"),strategy_version_id=a.get("strategy_version_id"),actor=actor)
    if name=="v5_opportunity_get":return C.operating.opportunity_get(a["opportunity_id"])
    if name=="v5_opportunity_list":return C.operating.opportunity_list(program_id=a.get("program_id"),stage=a.get("stage"),status=a.get("status","active"),limit=a.get("limit",100))
    if name=="v5_opportunity_transition":return C.operating.opportunity_transition(a["opportunity_id"],expected_version=a["expected_version"],to_stage=a["to_stage"],to_status=a["to_status"],evidence_refs=a["evidence_refs"],reason=a["reason"],qualification=a.get("qualification"),decision_revision_id=a.get("decision_revision_id"),idempotency_key=a.get("idempotency_key"),actor=actor)
    if name=="v5_decision_queue_enqueue":return C.operating.queue_enqueue(a["opportunity_id"],decision_revision_id=a["decision_revision_id"],manual_action_spec_id=a.get("manual_action_spec_id"),valid_until=a.get("valid_until"),idempotency_key=a.get("idempotency_key"),actor=actor)
    if name=="v5_decision_queue_get":return C.operating.queue_get(a["queue_id"])
    if name=="v5_decision_queue_list":return C.operating.queue_list(program_id=a.get("program_id"),state=a.get("state"),limit=a.get("limit",100))
    if name=="v5_action_card":return C.operating.queue_card(a["queue_id"])
    if name=="v5_decision_queue_respond":return C.operating.queue_respond(a["queue_id"],state=a["state"],reason=a.get("reason"),snoozed_until=a.get("snoozed_until"),attention_decision_id=a.get("attention_decision_id"),actor=actor)
    if name=="v5_brief_prepare":return C.operating.brief_prepare(brief_type=a["brief_type"],period_key=a["period_key"],as_of=a["as_of"],conclusion=a["conclusion"],payload=a["payload"],source_refs=a["source_refs"],idempotency_key=a.get("idempotency_key"),program_id=a.get("program_id"),actor=actor)
    if name=="v5_brief_get":return C.operating.brief_get(a["brief_id"])
    if name=="v5_brief_list":return C.operating.brief_list(program_id=a.get("program_id"),brief_type=a.get("brief_type"),limit=a.get("limit",50))
    if name=="v5_brief_mark_presented":return C.operating.brief_mark_presented(a["brief_id"],attention_decision_id=a["attention_decision_id"],actor=actor)
    if name=="v5_program_metrics_calculate":return C.operating.program_metrics_calculate(period_start=a["period_start"],period_end=a["period_end"],program_id=a.get("program_id"))
    if name=="v5_scorecard_publish":return C.operating.scorecard_publish(period_start=a["period_start"],period_end=a["period_end"],metrics=a["metrics"],comparisons=a["comparisons"],source_refs=a["source_refs"],caveats=a["caveats"],program_id=a.get("program_id"),actor=actor)
    if name=="v5_scorecard_get":return C.operating.scorecard_get(a["scorecard_id"])
    if name=="v5_scorecard_list":return C.operating.scorecard_list(program_id=a.get("program_id"),limit=a.get("limit",50))
    if name=="system_status":return C.system_status()
    if name=="system_doctor":return C.doctor()
    raise CompanionError(f"unimplemented advertised tool: {name}")


def reply(request:dict[str,Any])->dict[str,Any]|None:
    method=request.get("method");rid=request.get("id")
    if rid is None:return None
    if method=="initialize":result={"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"investment-companion","version":"7.0.0"}}
    elif method=="tools/list":result={"tools":[{"name":n,"description":d,"inputSchema":s} for n,(d,s) in active_tools(TOOLS).items()]}
    elif method=="tools/call":
        p=request.get("params",{})
        try:
            value=call(p["name"],p.get("arguments",{}));result={"content":[{"type":"text","text":json.dumps(value,ensure_ascii=False,indent=2)}],"structuredContent":{"result":value}}
        except Exception as e:
            result={"content":[{"type":"text","text":str(e)}],"isError":True}
    elif method=="ping":result={}
    else:return {"jsonrpc":"2.0","id":rid,"error":{"code":-32601,"message":f"Method not found: {method}"}}
    return {"jsonrpc":"2.0","id":rid,"result":result}


def main():
    C.initialize()
    for line in sys.stdin:
        try:
            request=json.loads(line);response=reply(request)
            if response is not None:sys.stdout.write(json.dumps(response,ensure_ascii=False,separators=(",",":"))+"\n");sys.stdout.flush()
        except Exception as e:
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":None,"error":{"code":-32603,"message":str(e)}})+"\n");sys.stdout.flush()


if __name__=="__main__":main()
