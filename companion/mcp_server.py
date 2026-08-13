from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from .core import Companion, CompanionError

ROOT=os.environ.get("COMPANION_ROOT","/home/ghk/investment-home")
C=Companion(ROOT)


def schema(properties:dict[str,Any]|None=None,required:list[str]|None=None)->dict[str,Any]:
    return {"type":"object","properties":properties or {},"required":required or [],"additionalProperties":False}


S={"type":"string"};I={"type":"integer"};O={"type":"object","additionalProperties":True}

TOOLS={
 "schedule_create":("创建可管理的主动计划。频率、使命、范围和策略会精确持久化。",schema({"name":S,"kind":{"type":"string","enum":["patrol","review","maintenance","one_shot"]},"mission":S,"cadence":O,"scope":O,"policy":O,"origin":O,"timezone":S},["name","kind","mission","cadence"])),
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
 "patrol_complete":("登记哨骑返回的工作材料。",schema({"patrol_id":S,"result_path":S,"disposition":S},["patrol_id","result_path","disposition"])),
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
 "max_purchase_calculate":("在现金底线、费用和最小交易单位下精确计算最大买入量。",schema({"as_of":S,"account_id":S,"asset_id":S,"price":{},"minimum_cash":{},"fee":{},"lot_size":{}},["as_of","account_id","asset_id","price"])),
 "portfolio_exposure_calculate":("按指定基准币种计算组合权重；缺失汇率时明确警告而不猜测。",schema({"as_of":S,"account_id":S,"prices":O,"base_currency":S},["as_of","account_id","prices","base_currency"])),
 "calculation_get":("读取可重放的精确计算记录。",schema({"calculation_id":S},["calculation_id"])),
 "portfolio_reconcile":("将账本派生状态与券商账单对账，差异不自动补平。",schema({"account_id":S,"as_of":S,"statement":O,"source_ref":S},["account_id","as_of","statement"])),
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
 "execution_set_status":("推进 Execution；filled 状态必须关联已确认流水。",schema({"execution_id":S,"status":S,"ledger_entry_ids":{"type":"array","items":S}},["execution_id","status"])),
 "recovery_package_create":("为跨对话恢复组装有界、可审计的最小上下文包。",schema({"purpose":S,"subject":O,"max_handles":I},["purpose","subject"])),
 "attention_decide":("使用当前 Attention Policy 对主动消息执行确定性门控并记录理由。",schema({"topic":S,"materiality":S,"confidence":S,"reason":S,"event_id":S,"evidence":{"type":"array"},"requested_action":S},["topic","materiality","confidence","reason"])),
 "attention_decision_list":("列出通知、摘要、落盘或抑制决定。",schema({"action":S,"limit":I})),
 "attention_feedback":("登记用户对主动消息的反馈；负反馈只产生策略调整提案。",schema({"decision_id":S,"feedback":S,"note":S},["decision_id","feedback"])),
 "attention_mark_delivered":("在主动消息实际送达后登记投递状态。",schema({"decision_id":S},["decision_id"])),
 "system_status":("查看数据库、调度、投递和恢复状态。",schema()),
 "system_doctor":("诊断 V3 数据库、工作区、个人上下文和金融事实准备度。",schema()),
}


def call(name:str,a:dict[str,Any]):
    actor="primary-codex"
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
    if name=="patrol_complete":return C.patrol_complete(a["patrol_id"],a["result_path"],a["disposition"])
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
    if name=="execution_set_status":return C.cognition.execution_set_status(a["execution_id"],a["status"],a.get("ledger_entry_ids"))
    if name=="recovery_package_create":return C.cognition.recovery_package(a["purpose"],a["subject"],a.get("max_handles",20))
    if name=="attention_decide":return C.attention.decide(**a)
    if name=="attention_decision_list":return C.attention.list(a.get("action"),a.get("limit",100))
    if name=="attention_feedback":return C.attention.feedback(a["decision_id"],a["feedback"],a.get("note"))
    if name=="attention_mark_delivered":return C.attention.mark_delivered(a["decision_id"])
    if name=="system_status":return C.system_status()
    if name=="system_doctor":return C.doctor()
    raise CompanionError(f"unknown tool: {name}")


def reply(request:dict[str,Any])->dict[str,Any]|None:
    method=request.get("method");rid=request.get("id")
    if rid is None:return None
    if method=="initialize":result={"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"investment-companion","version":"3.0.0"}}
    elif method=="tools/list":result={"tools":[{"name":n,"description":d,"inputSchema":s} for n,(d,s) in TOOLS.items()]}
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
