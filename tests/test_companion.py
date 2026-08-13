from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from companion.core import Companion, CompanionError
from companion.timeutil import iso, utc_now


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Companion(self.root);self.c.initialize()

    def tearDown(self):self.tmp.cleanup()

    def test_schedule_tick_is_idempotent_and_patch_uses_version(self):
        s=self.c.schedule_create(name="daily",kind="patrol",mission="scan",cadence={"type":"interval","seconds":1800})
        with self.c.db.transaction() as con:con.execute("UPDATE schedules SET next_run_at=? WHERE id=?",(iso(utc_now()-timedelta(minutes=1)),s["id"]))
        first=self.c.tick();second=self.c.tick()
        self.assertEqual(len(first["created_runs"]),1);self.assertEqual(len(second["created_runs"]),0)
        self.assertEqual(len(self.c.outbox_list()),1)
        current=self.c.schedule_get(s["id"]);patched=self.c.schedule_patch(s["id"],current["version"],{"mission":"better scan"})
        self.assertEqual(patched["mission"],"better scan")
        with self.assertRaises(CompanionError):self.c.schedule_patch(s["id"],current["version"],{"mission":"stale"})

    def test_derived_watch_requires_ttl_and_triggers_on_transition_only(self):
        with self.assertRaises(CompanionError):self.c.watch_create(name="x",subject_type="etf",subject_id="159101.SZ",intent="x",condition={"operator":"lt","threshold":.72},origin={"type":"case","id":"c"})
        w=self.c.watch_create(name="x",subject_type="etf",subject_id="159101.SZ",intent="x",condition={"operator":"lt","threshold":.72},origin={"type":"case","id":"c"},ttl_at=iso(utc_now()+timedelta(days=7)))
        o1=self.c.observation_add(subject_type="etf",subject_id="159101.SZ",metric="close",value=.71,observed_at=iso(),source="test",watch_id=w["id"])
        r1=self.c.evaluate_watch(w["id"],o1["id"]);self.assertTrue(r1["transition"])
        o2=self.c.observation_add(subject_type="etf",subject_id="159101.SZ",metric="close",value=.70,observed_at=iso(utc_now()+timedelta(seconds=1)),source="test",watch_id=w["id"])
        r2=self.c.evaluate_watch(w["id"],o2["id"]);self.assertFalse(r2["transition"])
        self.assertEqual(len(self.c.event_list()),1)

    def test_case_patrol_artifact_and_inbox_deduplicate(self):
        case=self.c.case_create(title="competition",brief="# 委托\n调查变化",subject={"symbol":"159101.SZ"})
        patrol=self.c.patrol_commission(brief_path=case["brief_path"],case_id=case["id"])
        result=self.root/case["root_path"]/"patrols"/"result.md";result.write_text("# 返回\nObservation: test\n",encoding="utf-8")
        done=self.c.patrol_complete(patrol["id"],str(result.relative_to(self.root)),"file_only")
        self.assertEqual(done["status"],"returned");self.assertEqual(len(self.c.artifact_list(case["id"])),2)
        a=self.c.inbox_add(source="user",title="article",content="same")
        b=self.c.inbox_add(source="user",title="article again",content="same")
        self.assertEqual(a["id"],b["id"])

    def test_recover_leases_and_backup(self):
        s=self.c.schedule_create(name="once",kind="one_shot",mission="once",cadence={"type":"one_shot","at":iso(utc_now()+timedelta(hours=1))})
        r=self.c.schedule_run_now(s["id"]);claimed=self.c.claim_run("dead",1);self.assertEqual(claimed["id"],r["id"])
        with self.c.db.transaction() as con:con.execute("UPDATE runs SET lease_until=? WHERE id=?",(iso(utc_now()-timedelta(seconds=1)),r["id"]))
        recovered=self.c.recover();self.assertEqual(recovered["recovered"]["runs"],1)
        target=self.root/"backup.db";self.c.db.backup(target);self.assertTrue(target.is_file())

    def test_outbox_failure_is_retryable_and_idempotent(self):
        event=self.c.event_create(kind="test",occurred_at=iso(),summary="test",payload={"x":1})
        a=self.c.outbox_enqueue(kind="codex_turn",destination="investment-companion",payload={"message":"x"},event_id=event["id"],idempotency_key="same")
        b=self.c.outbox_enqueue(kind="codex_turn",destination="investment-companion",payload={"message":"x"},event_id=event["id"],idempotency_key="same")
        self.assertEqual(a["id"],b["id"])
        claimed=self.c.outbox_claim("test");failed=self.c.outbox_finish(claimed["id"],False,"network")
        self.assertEqual(failed["status"],"retry")

    def test_file_ingestion_is_incremental_and_bootstrap_idempotent(self):
        source=self.root/"source";source.mkdir();self.c.ingest_directory(str(source),"test")
        item=source/"a.md";item.write_text("new article",encoding="utf-8")
        first=self.c.ingest_directory(str(source),"test");second=self.c.ingest_directory(str(source),"test")
        self.assertEqual(first["added"],1);self.assertEqual(second["added"],0)
        a=self.c.bootstrap_defaults();b=self.c.bootstrap_defaults();self.assertEqual(len(a["schedules"]),3);self.assertEqual(len(b["schedules"]),3)


if __name__=="__main__":unittest.main()
