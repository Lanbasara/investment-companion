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
 "watch_pause":("暂停观察项。",schema({"watch_id":S},["watch_id"])),
 "watch_resume":("恢复观察项。",schema({"watch_id":S},["watch_id"])),
 "watch_archive":("归档观察项。",schema({"watch_id":S},["watch_id"])),
 "observation_add":("登记来自确定性数据源的一次标准化采样。",schema({"subject_type":S,"subject_id":S,"metric":S,"value":{},"observed_at":S,"source":S,"source_ref":S,"quality":O,"watch_id":S},["subject_type","subject_id","metric","value","observed_at","source"])),
 "watch_evaluate":("用一项已登记 Observation 判断 Watch 条件，只在未成立到成立的跨越时产生事件。",schema({"watch_id":S,"observation_id":S},["watch_id","observation_id"])),
 "inbox_add":("登记用户文章、链接或调查材料，返回稳定文件句柄。",schema({"source":S,"title":S,"content":S,"url":S,"published_at":S,"source_key":S,"metadata":O},["source","title"])),
 "inbox_list":("读取增量信息 Inbox。",schema({"status":S,"limit":I})),
 "event_list":("列出事件。",schema({"status":S,"limit":I})),
 "event_get":("读取事件事实与来源。",schema({"event_id":S},["event_id"])),
 "event_acknowledge":("记录 Primary Codex 对事件的处理结论。",schema({"event_id":S,"note":S},["event_id","note"])),
 "case_create":("建立跨会话调查案件并落盘调查委托。",schema({"title":S,"brief":S,"subject":O,"origin":O},["title","brief"])),
 "case_list":("列出调查案件。",schema({"status":S})),
 "case_get":("读取案件及文件句柄。",schema({"case_id":S},["case_id"])),
 "case_set_status":("由 Primary Codex 推进或结案。",schema({"case_id":S,"status":S,"reason":S},["case_id","status"])),
 "patrol_commission":("基于现有委托文件登记一次短命哨骑行动。此工具不直接派遣 Agent。",schema({"brief_path":S,"case_id":S,"schedule_id":S,"budget":O},["brief_path"])),
 "patrol_list":("列出哨骑行动。",schema({"status":S})),
 "patrol_get":("读取哨骑行动和结果文件句柄。",schema({"patrol_id":S},["patrol_id"])),
 "patrol_complete":("登记哨骑返回的工作材料。",schema({"patrol_id":S,"result_path":S,"disposition":S},["patrol_id","result_path","disposition"])),
 "artifact_register":("登记工作目录中的认知材料句柄。",schema({"path":S,"kind":S,"subject":O,"case_id":S,"watch_id":S,"event_id":S,"status":S,"effective_at":S,"supersedes":S},["path","kind"])),
 "artifact_list":("按案件和状态取得少量文件句柄。",schema({"case_id":S,"status":S,"limit":I})),
 "system_status":("查看数据库、调度、投递和恢复状态。",schema()),
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
    if name in {"watch_pause","watch_resume","watch_archive"}:return C.watch_set_status(a["watch_id"],{"watch_pause":"paused","watch_resume":"active","watch_archive":"archived"}[name],actor)
    if name=="observation_add":return C.observation_add(**a)
    if name=="watch_evaluate":return C.evaluate_watch(a["watch_id"],a["observation_id"])
    if name=="inbox_add":return C.inbox_add(**a)
    if name=="inbox_list":return C.inbox_list(a.get("status","new"),a.get("limit",100))
    if name=="event_list":return C.event_list(a.get("status"),a.get("limit",50))
    if name=="event_get":return C.event_get(a["event_id"])
    if name=="event_acknowledge":return C.event_acknowledge(a["event_id"],a["note"],actor)
    if name=="case_create":return C.case_create(**a,actor=actor)
    if name=="case_list":return C.case_list(a.get("status"))
    if name=="case_get":return C.case_get(a["case_id"])
    if name=="case_set_status":return C.case_set_status(a["case_id"],a["status"],a.get("reason"),actor)
    if name=="patrol_commission":return C.patrol_commission(**a,actor=actor)
    if name=="patrol_list":return C.patrol_list(a.get("status"))
    if name=="patrol_get":return C.patrol_get(a["patrol_id"])
    if name=="patrol_complete":return C.patrol_complete(a["patrol_id"],a["result_path"],a["disposition"])
    if name=="artifact_register":return C.artifact_register(**a)
    if name=="artifact_list":return C.artifact_list(a.get("case_id"),a.get("status"),a.get("limit",100))
    if name=="system_status":return C.system_status()
    raise CompanionError(f"unknown tool: {name}")


def reply(request:dict[str,Any])->dict[str,Any]|None:
    method=request.get("method");rid=request.get("id")
    if rid is None:return None
    if method=="initialize":result={"protocolVersion":"2025-06-18","capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":"investment-companion","version":"2.0.0"}}
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
