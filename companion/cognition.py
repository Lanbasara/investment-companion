from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .foundation import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .financial import dec
from .timeutil import iso, parse, utc_now


class CognitiveLedger:
    def __init__(self, companion):
        self.c=companion;self.db=companion.db;self.root=companion.root

    def context_create(self, context_type:str, content:dict, reason:str|None=None, effective_from:str|None=None, expires_at:str|None=None)->dict:
        if context_type not in {"investor","mandate","attention"}:raise CompanionError("invalid context type")
        with self.db.connect() as con:r=con.execute("SELECT COALESCE(MAX(revision),0)+1 FROM context_revisions WHERE context_type=?",(context_type,)).fetchone();revision=r[0]
        cid=new_id("ctx");now=iso();h=digest(context_type,content)
        with self.db.transaction() as con:
            con.execute("INSERT INTO context_revisions(id,context_type,revision,status,content_json,effective_from,expires_at,reason,content_hash,created_at) VALUES(?,?,?,'draft',?,?,?,?,?,?)",(cid,context_type,revision,canonical(content),effective_from,expires_at,reason,h,now))
        return self.context_get(cid)

    def context_get(self, revision_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM context_revisions WHERE id=?",(revision_id,)).fetchone())
        if not item:raise CompanionError(f"context revision not found: {revision_id}")
        return item

    def context_current(self, context_type:str)->dict|None:
        now=iso()
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM context_revisions WHERE context_type=? AND status IN ('current','trial') AND (effective_from IS NULL OR effective_from<=?) AND (expires_at IS NULL OR expires_at>?) ORDER BY CASE status WHEN 'trial' THEN 0 ELSE 1 END,revision DESC LIMIT 1",(context_type,now,now)).fetchone())
        return item

    def context_list(self,context_type:str|None=None)->list[dict]:
        with self.db.connect() as con:
            rows=con.execute("SELECT * FROM context_revisions"+(" WHERE context_type=?" if context_type else "")+" ORDER BY context_type,revision DESC",((context_type,) if context_type else ())).fetchall()
        return rows_dict(rows)

    def context_confirm(self,revision_id:str,trial:bool=False)->dict:
        item=self.context_get(revision_id)
        if item["status"]!="draft":raise CompanionError("only draft context can be confirmed")
        target="trial" if trial else "current";now=iso()
        if trial and not item.get("expires_at"):raise CompanionError("trial context requires expires_at")
        with self.db.transaction() as con:
            current=con.execute("SELECT id FROM context_revisions WHERE context_type=? AND status=?",(item["context_type"],target)).fetchone()
            if current:con.execute("UPDATE context_revisions SET status='superseded' WHERE id=?",(current[0],))
            if not trial:con.execute("UPDATE context_revisions SET status='superseded' WHERE context_type=? AND status IN ('current','trial')",(item["context_type"],))
            con.execute("UPDATE context_revisions SET status=?,confirmed_at=?,effective_from=COALESCE(effective_from,?) WHERE id=?",(target,now,now,revision_id))
        return self.context_get(revision_id)

    def object_create(self,object_type:str,subject:dict,status:str="proposed")->dict:
        if object_type not in {"thesis","decision","review"}:raise CompanionError("invalid cognitive object type")
        if status not in {"proposed","draft"}:raise CompanionError("new cognitive objects must start proposed or draft")
        oid=new_id({"thesis":"ths","decision":"dec","review":"rev"}[object_type]);now=iso()
        with self.db.transaction() as con:con.execute("INSERT INTO cognitive_objects(id,object_type,subject_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",(oid,object_type,canonical(subject),status,now,now))
        return self.object_get(oid)

    def object_get(self,object_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM cognitive_objects WHERE id=?",(object_id,)).fetchone())
        if not item:raise CompanionError(f"cognitive object not found: {object_id}")
        return item

    def object_list(self,object_type:str|None=None,status:str|None=None)->list[dict]:
        q,p="SELECT * FROM cognitive_objects WHERE 1=1",[]
        if object_type:q+=" AND object_type=?";p.append(object_type)
        if status:q+=" AND status=?";p.append(status)
        q+=" ORDER BY updated_at DESC"
        with self.db.connect() as con:return rows_dict(con.execute(q,p).fetchall())

    def publish(self,object_id:str,content:str,knowledge_cutoff:str|None=None,context_refs:dict|None=None,calculation_ids:list[str]|None=None,metadata:dict|None=None)->dict:
        obj=self.object_get(object_id);context_refs=context_refs or {};calculation_ids=calculation_ids or [];metadata=metadata or {}
        if obj["object_type"]=="decision":
            required={"investor_revision_id","mandate_revision_id","portfolio_calculation_id","thesis_revision_ids"}
            missing=required-set(context_refs)
            if missing:raise CompanionError(f"decision freeze missing context refs: {sorted(missing)}")
            for key in ["investor_revision_id","mandate_revision_id"]:self.context_get(context_refs[key])
            self.c.financial.calculation_get(context_refs["portfolio_calculation_id"])
            for revision_id in context_refs["thesis_revision_ids"]:self.revision_get(revision_id)
            if str(metadata.get("decision_contract_version"))=="4":
                self._validate_v4_decision(obj,knowledge_cutoff,context_refs,calculation_ids,metadata)
        for cid in calculation_ids:
            self.c.financial.calculation_get(cid)
        with self.db.connect() as con:r=con.execute("SELECT COALESCE(MAX(revision),0)+1 FROM cognitive_revisions WHERE object_id=?",(object_id,)).fetchone();revision=r[0]
        folder={"thesis":"theses","decision":"decisions","review":"reviews"}[obj["object_type"]]
        base=self.root/folder/object_id
        if obj["object_type"]=="thesis":base=base/"revisions"
        base.mkdir(parents=True,exist_ok=True);path=base/f"R{revision:03d}.md";temp=path.with_suffix(".tmp")
        body=content.rstrip()+"\n";temp.write_text(body,encoding="utf-8");h=hashlib.sha256(body.encode()).hexdigest();rid=new_id("cogrev");now=iso();parent=obj.get("current_revision_id")
        rel=str(path.relative_to(self.root))
        os.replace(temp,path)
        with self.db.transaction() as con:
            con.execute("INSERT INTO cognitive_revisions(id,object_id,revision,status,path,content_hash,parent_id,knowledge_cutoff,context_refs_json,calculation_ids_json,metadata_json,created_at) VALUES(?,?,?,'published',?,?,?,?,?,?,?,?)",(rid,object_id,revision,rel,h,parent,knowledge_cutoff,canonical(context_refs),canonical(calculation_ids),canonical(metadata),now))
            con.execute("UPDATE cognitive_objects SET current_revision_id=?,status=?,updated_at=? WHERE id=?",(rid,"active" if obj["object_type"]=="thesis" else "issued" if obj["object_type"]=="decision" else "completed",now,object_id))
        if obj["object_type"]=="thesis":
            current=self.root/"theses"/object_id/"CURRENT.md";ct=current.with_suffix(".tmp");ct.write_text(body,encoding="utf-8");os.replace(ct,current)
        return self.revision_get(rid)

    def agent_review_record(
        self,
        *,
        invocation_ref:str,
        role:str,
        model:str,
        prompt_template:str,
        input_refs:list[str],
        review:dict[str,Any],
        token_usage:dict[str,int],
        started_at:str,
        finished_at:str,
        adopted:bool,
        adoption_reason:str,
    )->dict[str,Any]:
        """Freeze material model provenance; the record is evidence, never a Decision."""

        if not isinstance(invocation_ref,str) or not invocation_ref.strip():raise CompanionError("Agent review requires an external invocation_ref")
        allowed_roles={"thesis_critic","source_researcher","financial_analyst","market_scout","knowledge_gardener"}
        if role not in allowed_roles:raise CompanionError("unsupported material Agent role")
        if not isinstance(model,str) or not model.strip() or not isinstance(prompt_template,str) or not prompt_template.strip():raise CompanionError("Agent review requires model and prompt template version")
        if not isinstance(input_refs,list) or not input_refs or any(not isinstance(item,str) or not item for item in input_refs) or len(input_refs)!=len(set(input_refs)):raise CompanionError("Agent review requires unique immutable input refs")
        for reference in input_refs:
            if not isinstance(reference,str):raise CompanionError("Agent review input refs must be immutable identifiers")
            if reference.startswith(("manifest_","dataobj_")):self._validate_v4_source_refs([reference])
            elif reference.startswith("calc_"):self.c.financial.calculation_get(reference)
            elif reference.startswith("cogrev_"):self.revision_get(reference)
            elif reference.startswith("ctx_"):self.context_get(reference)
            else:raise CompanionError("Agent review inputs accept only verified Manifest, DataObject, Calculation, Cognitive or Context revisions")
        if not isinstance(review,dict) or set(review)!={"challenges","resolution"} or not isinstance(review["challenges"],list) or not review["challenges"] or any(not isinstance(item,str) or not item.strip() for item in review["challenges"]) or not isinstance(review["resolution"],str) or not review["resolution"].strip():raise CompanionError("Agent review requires substantive challenges and resolution")
        if set(token_usage)!={"input_tokens","output_tokens","total_tokens"} or any(isinstance(value,bool) or not isinstance(value,int) or value<=0 for value in token_usage.values()) or token_usage["total_tokens"]!=token_usage["input_tokens"]+token_usage["output_tokens"]:raise CompanionError("Agent review token_usage must contain positive exact counts")
        started,finished=parse(started_at),parse(finished_at)
        if finished<started or finished>utc_now()+timedelta(seconds=5):raise CompanionError("Agent review timestamps are invalid")
        if not isinstance(adopted,bool) or not isinstance(adoption_reason,str) or not adoption_reason.strip():raise CompanionError("Agent review requires an explicit adoption decision")
        payload={"invocation_ref":invocation_ref,"role":role,"model":model,"prompt_template":prompt_template,"input_refs":input_refs,"review":review,"token_usage":token_usage,"started_at":iso(started),"finished_at":iso(finished),"adopted":adopted,"adoption_reason":adoption_reason}
        with self.db.connect() as con:existing=row_dict(con.execute("SELECT * FROM agent_invocations WHERE invocation_ref=?",(invocation_ref,)).fetchone())
        if existing:
            recorded=self.c.data.manifest_get(existing["output_manifest_id"],verify=True)["manifest"].get("manifest",{})
            if canonical(recorded)!=canonical(payload):raise CompanionError("Agent invocation_ref already belongs to different immutable provenance")
            return self.agent_review_get(existing["id"])
        manifest=self.c.data.manifest_publish(kind="agent_review",schema_version="investment-companion.agent-review/v1",manifest=payload,_internal=True)
        iid,now=new_id("agentcall"),iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO agent_invocations(id,invocation_ref,role,model,prompt_template,input_refs_json,output_manifest_id,output_hash,token_usage_json,status,adopted,adoption_reason,started_at,finished_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,'succeeded',?,?,?,?,?)",(iid,invocation_ref,role,model,prompt_template,canonical(input_refs),manifest["id"],manifest["content_hash"],canonical(token_usage),1 if adopted else 0,adoption_reason,iso(started),iso(finished),now))
            saved=row_dict(con.execute("SELECT * FROM agent_invocations WHERE invocation_ref=?",(invocation_ref,)).fetchone())
            if saved["output_hash"]!=manifest["content_hash"]:raise CompanionError("Agent invocation_ref race produced different immutable provenance")
            iid=saved["id"]
        return self.agent_review_get(iid)

    def agent_review_get(self,invocation_id:str)->dict[str,Any]:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM agent_invocations WHERE id=?",(invocation_id,)).fetchone())
        if not item:raise CompanionError(f"Agent invocation not found: {invocation_id}")
        item["adopted"]=bool(item["adopted"]);manifest=self.c.data.manifest_get(item["output_manifest_id"],verify=True)
        if manifest["content_hash"]!=item["output_hash"] or manifest["kind"]!="agent_review":raise CompanionError("Agent invocation output lineage is invalid")
        item["review"]=manifest["manifest"].get("manifest",{}).get("review",{})
        return item

    def _validate_v4_decision(self,obj:dict,knowledge_cutoff:str|None,context_refs:dict,calculation_ids:list[str],metadata:dict)->None:
        release=self.c.jobs.decision_support_require()
        required_refs={"dataset_snapshot_id","strategy_version_id","experiment_run_id","research_experiment_run_id","target_manifest_id","portfolio_plan_calculation_id","constraint_calculation_id","attention_decision_id"}
        missing=required_refs-set(context_refs)
        if missing:raise CompanionError(f"V4 decision gate missing refs: {sorted(missing)}")
        required_metadata={"no_action","invalidators","source_refs","critic_review","valid_until","confirmed_ledger_hash","decision_mode","user_opt_in_ref","shadow_book_id"}
        missing_meta=required_metadata-set(metadata)
        if missing_meta:raise CompanionError(f"V4 decision gate missing metadata: {sorted(missing_meta)}")
        if not knowledge_cutoff:raise CompanionError("V4 decision requires knowledge_cutoff")
        investor=self.context_current("investor");mandate=self.context_current("mandate")
        if not investor or investor["id"]!=context_refs["investor_revision_id"]:raise CompanionError("V4 decision must reference current confirmed Investor")
        if not mandate or mandate["id"]!=context_refs["mandate_revision_id"]:raise CompanionError("V4 decision must reference current confirmed Mandate")
        snapshot=self.c.data.snapshot_get(context_refs["dataset_snapshot_id"])
        strategy=self.c.research.strategy_get(context_refs["strategy_version_id"])
        experiment=self.c.research.experiment_get(context_refs["experiment_run_id"])
        research_experiment=self.c.research.experiment_get(context_refs["research_experiment_run_id"])
        book=self.c.shadow.book_get(metadata["shadow_book_id"])
        if snapshot["status"]!="ready":raise CompanionError("V4 decision snapshot is not ready")
        if strategy["status"]!="shadow":raise CompanionError("V4 decision requires an explicitly promoted shadow strategy")
        if book["strategy_version_id"]!=strategy["id"]:raise CompanionError("V4 decision Shadow Book belongs to another strategy")
        if experiment["status"]!="succeeded" or experiment["strategy_version_id"]!=strategy["id"] or experiment["dataset_snapshot_id"]!=snapshot["id"]:raise CompanionError("V4 decision signal experiment lineage mismatch")
        if experiment["params"].get("evaluation_phase")!="forward_shadow" or not experiment.get("bundle_manifest_id"):raise CompanionError("V4 decision requires a successful untuned forward_shadow signal")
        if research_experiment["status"]!="succeeded" or research_experiment["strategy_version_id"]!=strategy["id"] or research_experiment["params"].get("evaluation_phase")!="final_holdout" or not research_experiment.get("bundle_manifest_id"):raise CompanionError("V4 decision requires its successful final_holdout research evidence")
        target_manifest=self.c.data.manifest_get(context_refs["target_manifest_id"],verify=True)
        if target_manifest["kind"]!="target_weights" or target_manifest["status"]!="ready":raise CompanionError("V4 decision target manifest is not ready")
        signal_bundle=self.c.data.manifest_get(experiment["bundle_manifest_id"],verify=True)["manifest"].get("manifest",{})
        target=signal_bundle.get("candidate_bundle",{}).get("experiment_result",{}).get("target_portfolio")
        if canonical(target_manifest["manifest"].get("manifest",{}))!=canonical(target):raise CompanionError("V4 decision target differs from its forward signal ExperimentBundle")
        if knowledge_cutoff!=snapshot["knowledge_cutoff"]:raise CompanionError("V4 decision knowledge_cutoff differs from DatasetSnapshot")
        mode=metadata["decision_mode"]
        if mode not in {"beta","strategy_eligible"}:raise CompanionError("V4 decision_mode must be beta or strategy_eligible")
        if not isinstance(metadata["user_opt_in_ref"],str) or not metadata["user_opt_in_ref"].strip():raise CompanionError("V4 decision requires an explicit user opt-in reference")
        if release["key"]=="v4_decision_support_beta" and metadata["user_opt_in_ref"]!=release["config"].get("user_opt_in_ref"):raise CompanionError("V4 beta Decision user opt-in differs from the enabled beta scope")
        if mode=="strategy_eligible":
            self.c.gates.require(["G6"])
            if self.c.shadow.sample_status(book["id"])["status"]!="eligible_for_review":raise CompanionError("strategy_eligible Decision requires sufficient forward Shadow evidence")
        portfolio_calc=self.c.financial.calculation_get(context_refs["portfolio_calculation_id"])
        plan_calc=self.c.financial.calculation_get(context_refs["portfolio_plan_calculation_id"])
        constraint_calc=self.c.financial.calculation_get(context_refs["constraint_calculation_id"])
        if portfolio_calc["kind"]!="portfolio_state" or plan_calc["kind"]!="portfolio_rebalance_plan" or constraint_calc["kind"]!="trade_impact":raise CompanionError("V4 decision requires portfolio_state, portfolio_rebalance_plan and trade_impact Calculations")
        account_ids={portfolio_calc["inputs"].get("account_id"),plan_calc["inputs"].get("account_id"),constraint_calc["inputs"].get("account_id")}
        if len(account_ids)!=1:raise CompanionError("V4 decision Calculations use different accounts")
        if plan_calc["inputs"].get("target_manifest_id")!=target_manifest["id"]:raise CompanionError("V4 decision portfolio plan uses another target_weights manifest")
        if plan_calc["assumptions"].get("mandate")!=mandate["content"] or constraint_calc["assumptions"].get("mandate")!=mandate["content"]:raise CompanionError("V4 decision Calculations do not freeze the current Mandate")
        if canonical(plan_calc["assumptions"].get("reality_spec"))!=canonical(strategy["spec"]["costs"]["reality_spec"]):raise CompanionError("V4 decision portfolio plan uses another RealitySpec")
        if plan_calc["outputs"].get("status")!="feasible" or not plan_calc["outputs"].get("actions"):raise CompanionError("V4 decision requires a feasible, non-empty personal portfolio plan")
        if plan_calc["outputs"].get("before",{}).get("calculation_id")!=portfolio_calc["id"]:raise CompanionError("V4 decision portfolio_state is not the frozen portfolio plan input")
        constraint_quantity=dec(constraint_calc["inputs"].get("quantity"),"V4 decision constraint quantity")
        constraint_side="buy" if constraint_quantity>0 else "sell" if constraint_quantity<0 else None
        matching_actions=[
            action for action in plan_calc["outputs"]["actions"]
            if action.get("asset_id")==constraint_calc["inputs"].get("asset_id")
            and action.get("side")==constraint_side
            and dec(action.get("quantity"),"planned quantity")==abs(constraint_quantity)
        ]
        if len(matching_actions)!=1:raise CompanionError("V4 decision constraint Calculation does not match exactly one portfolio plan action")
        planned_action=matching_actions[0]
        if dec(constraint_calc["inputs"].get("price"),"constraint price")!=dec(planned_action.get("execution_price"),"planned execution price"):raise CompanionError("V4 decision constraint price differs from the portfolio plan")
        planned_fee=dec(planned_action.get("commission","0"))+dec(planned_action.get("tax","0"))
        if dec(constraint_calc["inputs"].get("fee"),"constraint fee")!=planned_fee:raise CompanionError("V4 decision constraint fee differs from the portfolio plan")
        subject_asset=obj["subject"].get("asset_id")
        if subject_asset and constraint_calc["inputs"].get("asset_id")!=subject_asset:raise CompanionError("V4 decision constraint Calculation uses another asset")
        required_calculations={context_refs["portfolio_calculation_id"],context_refs["portfolio_plan_calculation_id"],context_refs["constraint_calculation_id"]}
        if not required_calculations<=set(calculation_ids):raise CompanionError("portfolio, joint plan and constraint Calculations must all be frozen in Decision")
        with self.db.connect() as con:attention=con.execute("SELECT id FROM attention_decisions WHERE id=?",(context_refs["attention_decision_id"],)).fetchone()
        if not attention:raise CompanionError("V4 decision requires a recorded Attention Decision")
        if not isinstance(metadata["invalidators"],list) or not metadata["invalidators"]:raise CompanionError("V4 decision requires explicit invalidators")
        self._validate_v4_source_refs(metadata["source_refs"])
        required_sources={snapshot["manifest_id"],experiment["bundle_manifest_id"],research_experiment["bundle_manifest_id"],target_manifest["id"]}
        if not required_sources<=set(metadata["source_refs"]):raise CompanionError("V4 decision sources must include Snapshot, research/signal bundles and target_weights")
        if metadata["confirmed_ledger_hash"]!=self.c.financial.confirmed_ledger_hash():raise CompanionError("V4 decision confirmed ledger hash is stale or invalid")
        critic=metadata["critic_review"]
        if not isinstance(critic,dict) or set(critic)!={"invocation_id"}:raise CompanionError("V4 decision critic_review must reference one immutable Agent invocation")
        invocation=self.agent_review_get(critic["invocation_id"])
        if invocation["role"]!="thesis_critic" or invocation["status"]!="succeeded" or not invocation["adopted"]:raise CompanionError("V4 decision requires an adopted thesis_critic review")
        critic_inputs=required_sources|required_calculations|{context_refs["investor_revision_id"],context_refs["mandate_revision_id"],*context_refs["thesis_revision_ids"]}
        if not critic_inputs<=set(invocation["input_refs"]):raise CompanionError("thesis_critic did not review the frozen research, personal and calculation evidence set")
        if invocation["output_manifest_id"] not in metadata["source_refs"]:raise CompanionError("V4 decision sources must include its immutable thesis_critic output")
        if not isinstance(metadata["no_action"],dict) or not metadata["no_action"]:raise CompanionError("V4 decision requires a concrete no-action alternative")
        now=utc_now()
        if parse(knowledge_cutoff)>now:raise CompanionError("V4 decision knowledge_cutoff cannot be in the future")
        if parse(metadata["valid_until"])<=max(parse(knowledge_cutoff),now):raise CompanionError("V4 decision validity must extend beyond knowledge cutoff and publication time")

    def _validate_v4_source_refs(self,refs:Any)->None:
        if not isinstance(refs,list) or not refs or any(not isinstance(item,str) or not item for item in refs) or len(refs)!=len(set(refs)):raise CompanionError("V4 source_refs must be a non-empty unique list")
        for reference in refs:
            if not isinstance(reference,str):raise CompanionError("V4 source_refs must be immutable object identifiers")
            if reference.startswith("manifest_"):
                item=self.c.data.manifest_get(reference,verify=True)
                if item["status"]!="ready":raise CompanionError("V4 source manifest is not ready")
            elif reference.startswith("dataobj_"):
                self.c.data.object_get(reference,verify=True)
            else:raise CompanionError("V4 source_refs accept only verified Manifest or DataObject IDs")

    def revision_get(self,revision_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM cognitive_revisions WHERE id=?",(revision_id,)).fetchone())
        if not item:raise CompanionError(f"cognitive revision not found: {revision_id}")
        return item

    def revision_list(self, object_id:str)->list[dict]:
        self.object_get(object_id)
        with self.db.connect() as con:
            return rows_dict(con.execute("SELECT * FROM cognitive_revisions WHERE object_id=? ORDER BY revision",(object_id,)).fetchall())

    def link(self,from_id:str,to_id:str,link_type:str,metadata:dict|None=None)->dict:
        lid=new_id("link");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO cognitive_links(id,from_id,to_id,link_type,metadata_json,created_at) VALUES(?,?,?,?,?,?)",(lid,from_id,to_id,link_type,canonical(metadata or {}),now));row=con.execute("SELECT * FROM cognitive_links WHERE from_id=? AND to_id=? AND link_type=?",(from_id,to_id,link_type)).fetchone()
        return row_dict(row)

    def execution_create(self,decision_id:str|None,details:dict,decision_revision_id:str|None=None,manual_action_spec_hash:str|None=None,idempotency_key:str|None=None,status:str="proposed",revalidate_action:bool=True)->dict:
        if decision_id:
            obj=self.object_get(decision_id)
            if obj["object_type"]!="decision" or obj["status"]!="issued":raise CompanionError("execution requires an issued decision")
            decision_revision_id=decision_revision_id or obj.get("current_revision_id")
            if decision_revision_id:
                revision=self.revision_get(decision_revision_id)
                if revision["object_id"]!=decision_id:raise CompanionError("execution decision revision belongs to another object")
                if obj.get("current_revision_id")!=decision_revision_id:raise CompanionError("execution requires the current Decision revision")
                if str(revision["metadata"].get("decision_contract_version"))=="4" and not manual_action_spec_hash:raise CompanionError("V4 execution requires ManualActionSpec hash")
                if str(revision["metadata"].get("decision_contract_version"))=="1" and revalidate_action:self.c.actionability.validate_decision(decision_revision_id)
        if status not in {"proposed","presented"}:raise CompanionError("new execution must be proposed or presented")
        if manual_action_spec_hash and not decision_revision_id:raise CompanionError("ManualActionSpec hash requires exact decision_revision_id")
        if idempotency_key is not None and (not isinstance(idempotency_key,str) or not idempotency_key.strip()):raise CompanionError("Execution idempotency_key must be a non-empty string")
        eid=new_id("exe");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO executions(id,decision_id,decision_revision_id,manual_action_spec_hash,status,details_json,idempotency_key,presented_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",(eid,decision_id,decision_revision_id,manual_action_spec_hash,status,canonical(details),idempotency_key,now if status=="presented" else None,now,now))
            if idempotency_key:
                saved=row_dict(con.execute("SELECT * FROM executions WHERE idempotency_key=?",(idempotency_key,)).fetchone())
                if not saved or saved["decision_id"]!=decision_id or saved.get("decision_revision_id")!=decision_revision_id or saved.get("manual_action_spec_hash")!=manual_action_spec_hash or canonical(saved["details"])!=canonical(details):
                    raise CompanionError("Execution idempotency_key is already bound to different immutable inputs")
                eid=saved["id"]
        return self.execution_get(eid)

    def execution_get(self,execution_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM executions WHERE id=?",(execution_id,)).fetchone())
        if not item:raise CompanionError(f"execution not found: {execution_id}")
        return item

    def execution_set_status(self,execution_id:str,status:str,ledger_entry_ids:list[str]|None=None,reason:str|None=None,revalidate_action:bool=True)->dict:
        allowed={"presented","accepted","rejected","ordered","partially_filled","filled","cancelled","expired","superseded","deviated"}
        if status not in allowed:raise CompanionError("invalid execution status")
        if status in {"rejected","cancelled","expired","superseded","deviated"} and (not isinstance(reason,str) or not reason.strip()):raise CompanionError(f"Execution {status} requires a reason")
        item=self.execution_get(execution_id);ids=ledger_entry_ids or item["ledger_entry_ids"]
        revision=self.revision_get(item["decision_revision_id"]) if item.get("decision_revision_id") else None
        contract=str(revision["metadata"].get("decision_contract_version")) if revision else ""
        if status in {"presented","accepted","ordered"}:
            if contract=="1":
                if revalidate_action:self.c.actionability.validate_decision(revision["id"])
            else:self.c.jobs.decision_support_require()
        if item["status"]==status:
            if status=="partially_filled" and ledger_entry_ids is not None and set(item["ledger_entry_ids"])<set(ledger_entry_ids):pass
            else:
                if ledger_entry_ids is not None and canonical(ledger_entry_ids)!=canonical(item["ledger_entry_ids"]):raise CompanionError("idempotent Execution transition supplied different ledger entries")
                if reason is not None and reason!=item.get("status_reason"):raise CompanionError("idempotent Execution transition supplied a different reason")
                return item
        transitions={
            "proposed":{"presented","accepted","cancelled","expired","superseded"},
            "presented":{"accepted","rejected","cancelled","expired","superseded"},
            "accepted":{"ordered","rejected","cancelled","expired","superseded"},
            "ordered":{"partially_filled","filled","cancelled","expired","deviated"},
            "partially_filled":{"partially_filled","filled","cancelled","expired","deviated"},
        }
        if status not in transitions.get(item["status"],set()):raise CompanionError(f"invalid execution transition: {item['status']} -> {status}")
        action=self._manual_action_by_hash(item["manual_action_spec_hash"]) if item.get("manual_action_spec_hash") else None
        if action and status in {"presented","accepted","ordered"}:
            validation=self.manual_action_validate(action["id"])
            action=validation["spec"]
            if not validation["executable"]:raise CompanionError(f"Execution transition requires a currently executable ManualActionSpec: {validation['reasons']}")
            if status=="accepted" and action["status"] not in {"presented","accepted"}:raise CompanionError("Execution acceptance requires a presented ManualActionSpec")
            if status=="ordered" and action["status"]!="accepted":raise CompanionError("Execution order requires an accepted ManualActionSpec")
        if status in {"partially_filled","filled","deviated"}:
            if not ids:raise CompanionError("filled execution requires confirmed ledger entries")
            if not isinstance(ids,list) or any(not isinstance(item,str) or not item for item in ids) or len(ids)!=len(set(ids)):raise CompanionError("execution ledger_entry_ids must be a unique list")
            if action:
                revision=self.revision_get(action["decision_revision_id"])
                if revision["metadata"].get("confirmed_ledger_hash")!=self.c.financial.confirmed_ledger_hash(exclude_ids=ids):raise CompanionError("Execution base ledger changed after the V4 Decision")
            action_quantity=Decimal(str(action["spec"]["quantity"])) if action else None
            matched_quantity=Decimal("0");matched_gross=Decimal("0");matched_fees=Decimal("0")
            for eid in ids:
                ledger=self.c.financial.ledger_get(eid)
                if ledger["status"]!="confirmed":raise CompanionError("execution can only link confirmed ledger entries")
                with self.db.connect() as con:used=con.execute("SELECT e.id FROM executions e,json_each(e.ledger_entry_ids_json) j WHERE j.value=? AND e.id<>? LIMIT 1",(eid,execution_id)).fetchone()
                if used:raise CompanionError(f"confirmed ledger entry is already linked to execution {used[0]}")
                if item.get("manual_action_spec_hash"):
                    spec=action["spec"]
                    if ledger["entry_type"]!="trade" or ledger.get("asset_id")!=spec["asset_id"]:raise CompanionError("confirmed ledger entry does not match ManualActionSpec asset")
                    if ledger["account_id"]!=spec["account_id"]:raise CompanionError("confirmed ledger entry account does not match ManualActionSpec")
                    if parse(ledger["occurred_at"])<parse(spec["quote_at"]) or parse(ledger["occurred_at"])>parse(spec["valid_until"]):raise CompanionError("confirmed ledger entry occurred outside ManualActionSpec validity")
                    quantity=Decimal(str(ledger.get("quantity_text") or 0));matched_quantity+=abs(quantity)
                    if (spec["side"]=="buy" and quantity<=0) or (spec["side"] in {"sell","reduce"} and quantity>=0):raise CompanionError("confirmed ledger entry side does not match ManualActionSpec")
                    price=Decimal(str(ledger.get("price_text") or 0));low=Decimal(str(spec["price_range"]["min"]));high=Decimal(str(spec["price_range"]["max"]))
                    matched_gross+=abs(quantity)*price;matched_fees+=Decimal(str(ledger.get("fee_text") or 0))
                    if status=="filled" and not low<=price<=high:raise CompanionError("actual fill price deviated from ManualActionSpec; use deviated status")
                elif item.get("decision_id"):
                    decision=self.object_get(item["decision_id"]);expected_asset=item["details"].get("asset_id") or decision["subject"].get("asset_id")
                    expected_side=item["details"].get("side")
                    if ledger["entry_type"]!="trade":raise CompanionError("Execution fill requires a confirmed trade ledger entry")
                    if expected_asset and ledger.get("asset_id")!=expected_asset:raise CompanionError("Execution fill ledger asset mismatch")
                    quantity=Decimal(str(ledger.get("quantity_text") or 0))
                    if (expected_side=="buy" and quantity<=0) or (expected_side in {"sell","reduce"} and quantity>=0):raise CompanionError("Execution fill ledger side mismatch")
            if action_quantity is not None:
                if status=="filled" and matched_quantity!=action_quantity:raise CompanionError("filled Execution quantity differs from ManualActionSpec")
                if status=="partially_filled" and not Decimal("0")<matched_quantity<action_quantity:raise CompanionError("partial fill quantity must be between zero and ManualActionSpec quantity")
                if status=="deviated" and matched_quantity<=0:raise CompanionError("deviated Execution requires a non-zero actual fill")
                if status=="filled":
                    revision=self.revision_get(action["decision_revision_id"]);strategy=self.c.research.strategy_get(revision["context_refs"]["strategy_version_id"]);reality=strategy["spec"]["costs"]["reality_spec"]
                    quantum=Decimal(str(reality["money_quantum"]));commission=max(Decimal(str(reality["minimum_commission"])),matched_gross*Decimal(str(reality["commission_rate"])))
                    tax=matched_gross*Decimal(str(reality["sell_stamp_duty_rate"])) if action["spec"]["side"] in {"sell","reduce"} else Decimal("0")
                    expected_fee=(commission+tax).quantize(quantum,rounding=ROUND_HALF_UP)
                    if matched_fees!=expected_fee:raise CompanionError("actual fill fees differ from RealitySpec; use deviated status")
        now=iso();timestamps={"presented":"presented_at","accepted":"accepted_at","ordered":"ordered_at"};terminal={"rejected","filled","cancelled","expired","superseded","deviated"}
        sets=["status=?","ledger_entry_ids_json=?","status_reason=?","updated_at=?"];values=[status,canonical(ids),reason,now]
        if status in timestamps:sets.append(f"{timestamps[status]}=?");values.append(now)
        if status in terminal:sets.append("finished_at=?");values.append(now)
        values.append(execution_id)
        with self.db.transaction() as con:
            con.execute(f"UPDATE executions SET {','.join(sets)} WHERE id=?",values)
            if action and status=="accepted":
                changed=con.execute("UPDATE manual_action_specs SET status='accepted',updated_at=? WHERE id=? AND status IN ('presented','accepted')",(now,action["id"])).rowcount
                if changed!=1:raise CompanionError("ManualActionSpec acceptance transition lost")
        return self.execution_get(execution_id)

    def manual_action_create(self,decision_revision_id:str,spec:dict)->dict:
        revision=self.revision_get(decision_revision_id);obj=self.object_get(revision["object_id"])
        if obj["object_type"]!="decision" or obj["status"]!="issued" or obj["current_revision_id"]!=decision_revision_id:raise CompanionError("ManualActionSpec requires the current issued Decision revision")
        if str(revision["metadata"].get("decision_contract_version"))!="4":raise CompanionError("ManualActionSpec requires a V4 Decision revision")
        self.c.jobs.decision_support_require()
        required={"account_id","asset_id","side","quantity","lot_size","quote_at","valid_until","max_quote_age_seconds","price_range","priority","alternatives","source_refs","revalidate_if","notification_key"}
        missing=required-set(spec)
        if missing:raise CompanionError(f"ManualActionSpec missing: {sorted(missing)}")
        self.c.financial.asset_get(spec["asset_id"])
        self.c.financial.account_get(spec["account_id"])
        if spec["side"] not in {"buy","sell","reduce"}:raise CompanionError("ManualActionSpec must be a concrete manual trade, not hold")
        unknown=set(spec)-required-{"supersedes"}
        if unknown:raise CompanionError(f"ManualActionSpec has unknown fields: {sorted(unknown)}")
        quantity=dec(spec["quantity"],"ManualActionSpec quantity")
        if quantity<=0 or quantity!=quantity.to_integral_value():raise CompanionError("ManualActionSpec quantity must be a positive whole number")
        strategy=self.c.research.strategy_get(revision["context_refs"]["strategy_version_id"])
        reality=strategy["spec"]["costs"]["reality_spec"];lot_size=int(reality["lot_size"])
        declared_lot=dec(spec["lot_size"],"ManualActionSpec lot_size")
        if isinstance(spec["lot_size"],bool) or declared_lot!=declared_lot.to_integral_value() or int(declared_lot)!=lot_size:raise CompanionError("ManualActionSpec lot_size differs from StrategyVersion RealitySpec")
        if spec["side"]=="buy" and int(quantity)%lot_size:raise CompanionError("ManualActionSpec buy quantity must use whole board lots")
        if obj["subject"].get("asset_id") and obj["subject"]["asset_id"]!=spec["asset_id"]:raise CompanionError("ManualActionSpec asset differs from issued Decision")
        if isinstance(spec["max_quote_age_seconds"],bool) or not isinstance(spec["max_quote_age_seconds"],int) or not 1<=spec["max_quote_age_seconds"]<=3600:raise CompanionError("max_quote_age_seconds must be an integer within 1..3600")
        price_range=spec["price_range"]
        if not isinstance(price_range,dict) or set(price_range)!={"min","max"}:raise CompanionError("invalid ManualActionSpec price_range")
        price_min=dec(price_range["min"],"ManualActionSpec minimum price");price_max=dec(price_range["max"],"ManualActionSpec maximum price")
        if price_min<=0 or price_min>price_max:raise CompanionError("invalid ManualActionSpec price_range")
        if spec["priority"] not in {"low","normal","high","urgent"}:raise CompanionError("invalid ManualActionSpec priority")
        if not isinstance(spec["notification_key"],str) or not spec["notification_key"].strip():raise CompanionError("ManualActionSpec notification_key must be non-empty")
        constraint=self.c.financial.calculation_get(revision["context_refs"]["constraint_calculation_id"])
        plan=self.c.financial.calculation_get(revision["context_refs"]["portfolio_plan_calculation_id"])
        signed=quantity if spec["side"]=="buy" else -quantity
        planned_side="sell" if spec["side"] in {"sell","reduce"} else "buy"
        matching=[action for action in plan["outputs"].get("actions",[]) if action.get("asset_id")==spec["asset_id"] and action.get("side")==planned_side and dec(action.get("quantity"),"planned quantity")==quantity]
        if plan["outputs"].get("status")!="feasible" or len(matching)!=1:raise CompanionError("ManualActionSpec does not match exactly one action in the frozen portfolio plan")
        planned_action=matching[0]
        if constraint["inputs"].get("account_id")!=spec["account_id"] or constraint["inputs"].get("asset_id")!=spec["asset_id"] or dec(constraint["inputs"].get("quantity"),"frozen constraint quantity")!=signed:raise CompanionError("ManualActionSpec differs from frozen constraint Calculation")
        frozen_price=dec(constraint["inputs"].get("price"),"frozen constraint price")
        if frozen_price!=dec(planned_action.get("execution_price"),"planned execution price"):raise CompanionError("frozen constraint price differs from portfolio plan execution price")
        if not price_min<=frozen_price<=price_max:raise CompanionError("ManualActionSpec range excludes its frozen constraint price")
        gross=abs(signed)*frozen_price;quantum=dec(reality["money_quantum"],"RealitySpec money_quantum")
        expected_commission=max(dec(reality["minimum_commission"]),gross*dec(reality["commission_rate"]))
        expected_tax=gross*dec(reality["sell_stamp_duty_rate"]) if signed<0 else Decimal("0")
        expected_fee=(expected_commission+expected_tax).quantize(quantum,rounding=ROUND_HALF_UP)
        if dec(constraint["inputs"].get("fee"),"frozen constraint fee")!=expected_fee or expected_fee!=dec(planned_action.get("commission","0"))+dec(planned_action.get("tax","0")):raise CompanionError("frozen constraint Calculation does not use the portfolio plan and StrategyVersion RealitySpec fees")
        if constraint["outputs"].get("blocked") or constraint["warnings"]:raise CompanionError("ManualActionSpec cannot be created from a blocked or incomplete constraint Calculation")
        now=utc_now()
        if parse(spec["quote_at"])>now:raise CompanionError("ManualActionSpec quote_at cannot be in the future")
        if parse(spec["valid_until"])<=max(parse(spec["quote_at"]),now):raise CompanionError("ManualActionSpec must still be valid after quote_at and creation")
        if parse(spec["valid_until"])>parse(revision["metadata"]["valid_until"]):raise CompanionError("ManualActionSpec cannot outlive its Decision")
        for field in ["alternatives","source_refs","revalidate_if"]:
            if not isinstance(spec[field],list):raise CompanionError(f"ManualActionSpec {field} must be a list")
        if not spec["alternatives"] or any(not isinstance(item,str) or not item.strip() for item in spec["alternatives"]):raise CompanionError("ManualActionSpec requires concrete alternatives")
        self._validate_v4_source_refs(spec["source_refs"])
        if not set(spec["source_refs"])<=set(revision["metadata"]["source_refs"]):raise CompanionError("ManualActionSpec sources must be frozen by its Decision")
        required_revalidation={"decision_revision_current","valid_until","mandate_revision_current","investor_revision_current","confirmed_ledger_unchanged","fresh_market_snapshot","price_range"}
        if set(spec["revalidate_if"])!=required_revalidation:raise CompanionError(f"ManualActionSpec revalidate_if must be exactly: {sorted(required_revalidation)}")
        supersedes=spec.get("supersedes")
        if supersedes:self.manual_action_get(supersedes)
        payload={**spec,"decision_revision_id":decision_revision_id,"schema":"investment-companion.manual-action/v1"};h=digest(payload);sid=new_id("action");now=iso()
        with self.db.transaction() as con:
            try:con.execute("INSERT INTO manual_action_specs(id,decision_revision_id,content_hash,spec_json,status,valid_until,supersedes,notification_key,created_at,updated_at) VALUES(?,?,?,?,'draft',?,?,?,?,?)",(sid,decision_revision_id,h,canonical(payload),spec["valid_until"],supersedes,spec["notification_key"],now,now))
            except Exception as exc:
                existing=con.execute("SELECT id,content_hash FROM manual_action_specs WHERE content_hash=? OR notification_key=?",(h,spec["notification_key"])).fetchone()
                if existing and existing["content_hash"]==h:sid=existing["id"]
                else:raise CompanionError("notification_key already belongs to a different ManualActionSpec") from exc
            if supersedes:con.execute("UPDATE manual_action_specs SET status='superseded',updated_at=?,invalidation_reason=? WHERE id=? AND status NOT IN ('rejected','expired','cancelled','superseded')",(now,f"superseded by {sid}",supersedes))
        return self.manual_action_get(sid)

    def manual_action_get(self,spec_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM manual_action_specs WHERE id=?",(spec_id,)).fetchone())
        if not item:raise CompanionError(f"ManualActionSpec not found: {spec_id}")
        return item

    def manual_action_validate(self,spec_id:str,as_of:str|None=None)->dict:
        wall_now=utc_now();now=parse(as_of) if as_of else wall_now
        if abs((now-wall_now).total_seconds())>5:raise CompanionError("ManualActionSpec executable validation must use current time within 5 seconds")
        item=self.manual_action_get(spec_id);reasons=[];revision=self.revision_get(item["decision_revision_id"]);obj=self.object_get(revision["object_id"])
        if obj["current_revision_id"]!=revision["id"]:reasons.append("decision_superseded")
        if now>=parse(item["valid_until"]):reasons.append("expired")
        current_mandate=self.context_current("mandate")
        if not current_mandate or current_mandate["id"]!=revision["context_refs"].get("mandate_revision_id"):reasons.append("mandate_changed")
        current_investor=self.context_current("investor")
        if not current_investor or current_investor["id"]!=revision["context_refs"].get("investor_revision_id"):reasons.append("investor_changed")
        if revision["metadata"].get("confirmed_ledger_hash")!=self.c.financial.confirmed_ledger_hash():reasons.append("confirmed_ledger_changed")
        with self.db.connect() as con:market=row_dict(con.execute("SELECT * FROM market_snapshots WHERE asset_id=? AND metric='close' AND quality='healthy' AND observed_at<=? ORDER BY observed_at DESC LIMIT 1",(item["spec"]["asset_id"],iso(now))).fetchone())
        if not market:reasons.append("market_snapshot_missing")
        elif parse(market["observed_at"])<parse(item["spec"]["quote_at"]) or (now-parse(market["observed_at"])).total_seconds()>int(item["spec"]["max_quote_age_seconds"]):reasons.append("market_snapshot_stale")
        else:
            low=Decimal(str(item["spec"]["price_range"]["min"]));high=Decimal(str(item["spec"]["price_range"]["max"]));price=Decimal(str(market["value_text"]))
            if not low<=price<=high:reasons.append("price_out_of_range")
        portfolio=self.c.financial.portfolio_state(iso(now),item["spec"]["account_id"])
        impact=None
        if not reasons and market:
            signed=Decimal(str(item["spec"]["quantity"]))*(Decimal("1") if item["spec"]["side"]=="buy" else Decimal("-1"))
            reality=self.c.research.strategy_get(revision["context_refs"]["strategy_version_id"])["spec"]["costs"]["reality_spec"]
            price=Decimal(str(market["value_text"]));gross=abs(signed)*price
            quantum=Decimal(str(reality["money_quantum"]));commission=max(Decimal(str(reality["minimum_commission"])),gross*Decimal(str(reality["commission_rate"])))
            tax=gross*Decimal(str(reality["sell_stamp_duty_rate"])) if signed<0 else Decimal("0")
            estimated_fee=(commission+tax).quantize(quantum,rounding=ROUND_HALF_UP)
            impact=self.c.risk.assess_trade(
                as_of=iso(now),
                account_id=item["spec"]["account_id"],
                asset_id=item["spec"]["asset_id"],
                quantity=str(signed),
                price=market["value_text"],
                fee=str(estimated_fee),
                mandate=current_mandate["content"] if current_mandate else None,
                reality_spec=reality,
                market_snapshot_id=market["id"],
                max_market_age_seconds=int(item["spec"]["max_quote_age_seconds"]),
                valid_until=item["valid_until"],
                price_range=item["spec"]["price_range"],
            )
            if impact["blocked"]:reasons.append("current_constraints_block_action")
            if portfolio.get("warnings"):reasons.append("portfolio_state_incomplete")
        if reasons and item["status"] not in {"rejected","cancelled","superseded"}:
            target="expired" if reasons==["expired"] else "invalid"
            with self.db.transaction() as con:con.execute("UPDATE manual_action_specs SET status=?,invalidation_reason=?,updated_at=? WHERE id=?",(target,canonical(reasons),iso(),spec_id))
            item=self.manual_action_get(spec_id)
        return {"spec":item,"executable":not reasons and item["status"] in {"draft","presented","accepted"},"reasons":reasons,"checked_at":iso(now),"market_snapshot_id":market["id"] if market else None,"portfolio_calculation_id":portfolio["calculation_id"],"constraint_calculation_id":impact["calculation_id"] if impact else None}

    def manual_action_set_status(self,spec_id:str,status:str,reason:str|None=None)->dict:
        allowed={"presented","accepted","rejected","expired","cancelled","superseded"}
        if status not in allowed:raise CompanionError("invalid ManualActionSpec status")
        if status in {"rejected","expired","cancelled","superseded"} and (not isinstance(reason,str) or not reason.strip()):raise CompanionError(f"ManualActionSpec {status} requires a reason")
        if status in {"presented","accepted"}:self.c.jobs.decision_support_require()
        validation=self.manual_action_validate(spec_id)
        item=validation["spec"]
        if status in {"presented","accepted"}:
            if not validation["executable"]:raise CompanionError(f"ManualActionSpec is not executable: {validation['reasons']}")
        transitions={"draft":{"presented","rejected","cancelled","expired","superseded"},"presented":{"accepted","rejected","cancelled","expired","superseded"},"accepted":{"cancelled","expired","superseded"}}
        if status!=item["status"] and status not in transitions.get(item["status"],set()):raise CompanionError(f"invalid ManualActionSpec transition: {item['status']} -> {status}")
        with self.db.transaction() as con:con.execute("UPDATE manual_action_specs SET status=?,invalidation_reason=?,updated_at=? WHERE id=?",(status,reason,iso(),spec_id))
        return self.manual_action_get(spec_id)

    def execution_create_from_action(self,spec_id:str,idempotency_key:str)->dict:
        self.c.jobs.decision_support_require()
        if not isinstance(idempotency_key,str) or not idempotency_key.strip():raise CompanionError("Execution idempotency_key must be a non-empty string")
        with self.db.connect() as con:existing=row_dict(con.execute("SELECT * FROM executions WHERE idempotency_key=?",(idempotency_key,)).fetchone())
        if existing:
            item=self.manual_action_get(spec_id)
            if existing.get("manual_action_spec_hash")!=item["content_hash"] or existing.get("decision_revision_id")!=item["decision_revision_id"]:raise CompanionError("Execution idempotency_key is already bound to another ManualActionSpec")
            return existing
        validation=self.manual_action_validate(spec_id)
        if not validation["executable"]:raise CompanionError(f"ManualActionSpec is not executable: {validation['reasons']}")
        item=validation["spec"]
        if item["status"]=="draft":item=self.manual_action_set_status(spec_id,"presented")
        revision=self.revision_get(item["decision_revision_id"])
        return self.execution_create(revision["object_id"],{"manual_action_spec_id":spec_id,"action":item["spec"],"validated_market_snapshot_id":validation["market_snapshot_id"],"validated_portfolio_calculation_id":validation["portfolio_calculation_id"],"validated_constraint_calculation_id":validation["constraint_calculation_id"],"validated_at":validation["checked_at"]},decision_revision_id=revision["id"],manual_action_spec_hash=item["content_hash"],idempotency_key=idempotency_key,status="presented")

    def _manual_action_by_hash(self,content_hash:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM manual_action_specs WHERE content_hash=?",(content_hash,)).fetchone())
        if not item:raise CompanionError("Execution references missing ManualActionSpec")
        return item

    def recovery_package(self,purpose:str,subject:dict,max_handles:int=20)->dict:
        handles=[];reasons={};warnings=[]
        for kind in ["investor","mandate","attention"]:
            item=self.context_current(kind)
            if item:handles.append({"type":"context","id":item["id"],"context_type":kind,"revision":item["revision"]});reasons[item["id"]]="effective context"
            else:warnings.append(f"missing current {kind} context")
        for obj in self.object_list("thesis","active"):
            if subject and not all(obj["subject"].get(k)==v for k,v in subject.items()):continue
            if obj.get("current_revision_id"):
                rev=self.revision_get(obj["current_revision_id"]);handles.append({"type":"thesis_revision","id":rev["id"],"path":rev["path"]});reasons[rev["id"]]="current matching thesis"
        handles=handles[:max_handles];now=iso();payload={"purpose":purpose,"subject":subject,"as_of":now,"handles":handles,"reasons":reasons,"warnings":warnings};pid=new_id("pkg");h=digest(payload)
        with self.db.transaction() as con:con.execute("INSERT INTO recovery_packages(id,purpose,subject_json,as_of,handles_json,included_reason_json,warnings_json,content_hash,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(pid,purpose,canonical(subject),now,canonical(handles),canonical(reasons),canonical(warnings),h,now))
        return {"id":pid,**payload}
