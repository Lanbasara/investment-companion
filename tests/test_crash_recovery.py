from __future__ import annotations

import multiprocessing
import os
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from companion.core import Companion
from companion.timeutil import iso, utc_now


def claim_and_crash(root: str):
    c=Companion(root);c.claim_run("crashed-worker",1);os._exit(17)


class CrashRecoveryTest(unittest.TestCase):
    def test_process_death_recovers_expired_lease_without_duplicate_run(self):
        with tempfile.TemporaryDirectory() as root:
            c=Companion(root);c.initialize();s=c.schedule_create(name="crash",kind="one_shot",mission="crash",cadence={"type":"one_shot","at":iso(utc_now()+timedelta(days=1))});run=c.schedule_run_now(s["id"])
            p=multiprocessing.Process(target=claim_and_crash,args=(root,));p.start();p.join();self.assertEqual(p.exitcode,17)
            time.sleep(1.1);result=c.recover();self.assertEqual(result["recovered"]["runs"],1);self.assertEqual(c.run_get(run["id"])["status"],"recoverable");self.assertEqual(len(c.run_list()),1)


if __name__=="__main__":unittest.main()
