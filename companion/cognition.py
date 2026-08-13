from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .core import CompanionError, canonical, digest, new_id
from .db import row_dict, rows_dict
from .timeutil import iso


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
        obj=self.object_get(object_id);context_refs=context_refs or {};calculation_ids=calculation_ids or []
        if obj["object_type"]=="decision":
            required={"investor_revision_id","mandate_revision_id","portfolio_calculation_id","thesis_revision_ids"}
            missing=required-set(context_refs)
            if missing:raise CompanionError(f"decision freeze missing context refs: {sorted(missing)}")
            for key in ["investor_revision_id","mandate_revision_id"]:self.context_get(context_refs[key])
            self.c.financial.calculation_get(context_refs["portfolio_calculation_id"])
            for revision_id in context_refs["thesis_revision_ids"]:self.revision_get(revision_id)
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
            con.execute("INSERT INTO cognitive_revisions(id,object_id,revision,status,path,content_hash,parent_id,knowledge_cutoff,context_refs_json,calculation_ids_json,metadata_json,created_at) VALUES(?,?,?,'published',?,?,?,?,?,?,?,?)",(rid,object_id,revision,rel,h,parent,knowledge_cutoff,canonical(context_refs),canonical(calculation_ids),canonical(metadata or {}),now))
            con.execute("UPDATE cognitive_objects SET current_revision_id=?,status=?,updated_at=? WHERE id=?",(rid,"active" if obj["object_type"]=="thesis" else "issued" if obj["object_type"]=="decision" else "completed",now,object_id))
        if obj["object_type"]=="thesis":
            current=self.root/"theses"/object_id/"CURRENT.md";ct=current.with_suffix(".tmp");ct.write_text(body,encoding="utf-8");os.replace(ct,current)
        return self.revision_get(rid)

    def revision_get(self,revision_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM cognitive_revisions WHERE id=?",(revision_id,)).fetchone())
        if not item:raise CompanionError(f"cognitive revision not found: {revision_id}")
        return item

    def link(self,from_id:str,to_id:str,link_type:str,metadata:dict|None=None)->dict:
        lid=new_id("link");now=iso()
        with self.db.transaction() as con:
            con.execute("INSERT OR IGNORE INTO cognitive_links(id,from_id,to_id,link_type,metadata_json,created_at) VALUES(?,?,?,?,?,?)",(lid,from_id,to_id,link_type,canonical(metadata or {}),now));row=con.execute("SELECT * FROM cognitive_links WHERE from_id=? AND to_id=? AND link_type=?",(from_id,to_id,link_type)).fetchone()
        return row_dict(row)

    def execution_create(self,decision_id:str|None,details:dict)->dict:
        if decision_id:
            obj=self.object_get(decision_id)
            if obj["object_type"]!="decision" or obj["status"]!="issued":raise CompanionError("execution requires an issued decision")
        eid=new_id("exe");now=iso()
        with self.db.transaction() as con:con.execute("INSERT INTO executions(id,decision_id,status,details_json,created_at,updated_at) VALUES(?,?,'proposed',?,?,?)",(eid,decision_id,canonical(details),now,now))
        return self.execution_get(eid)

    def execution_get(self,execution_id:str)->dict:
        with self.db.connect() as con:item=row_dict(con.execute("SELECT * FROM executions WHERE id=?",(execution_id,)).fetchone())
        if not item:raise CompanionError(f"execution not found: {execution_id}")
        return item

    def execution_set_status(self,execution_id:str,status:str,ledger_entry_ids:list[str]|None=None)->dict:
        allowed={"accepted","ordered","partially_filled","filled","cancelled","expired"}
        if status not in allowed:raise CompanionError("invalid execution status")
        item=self.execution_get(execution_id);ids=ledger_entry_ids or item["ledger_entry_ids"]
        if status in {"partially_filled","filled"}:
            if not ids:raise CompanionError("filled execution requires confirmed ledger entries")
            for eid in ids:
                if self.c.financial.ledger_get(eid)["status"]!="confirmed":raise CompanionError("execution can only link confirmed ledger entries")
        with self.db.transaction() as con:con.execute("UPDATE executions SET status=?,ledger_entry_ids_json=?,updated_at=? WHERE id=?",(status,canonical(ids),iso(),execution_id))
        return self.execution_get(execution_id)

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
