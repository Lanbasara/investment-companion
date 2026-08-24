from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from companion.core import Companion, CompanionError
from companion.timeutil import iso


class V3Test(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.c=Companion(self.root);self.c.workspace_init()
        self.account=self.c.financial.account_create("Broker","CNY")
        self.asset=self.c.financial.asset_upsert("etf","测试ETF","CNY",{"ts_code":"159101.SZ"})

    def tearDown(self):self.tmp.cleanup()

    def test_unconfirmed_entry_does_not_change_portfolio_and_replay_is_exact(self):
        entry=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="trade",asset_id=self.asset["id"],occurred_at="2026-01-02T07:00:00Z",quantity="100.125",price="1.2345",amount="-123.6043125",currency="CNY",fee="0.12",source="user")
        before=self.c.financial.portfolio_state("2026-01-03T00:00:00Z",self.account["id"],{self.asset["id"]:"1.25"})
        self.assertEqual(before["positions"],[])
        self.c.financial.ledger_confirm(entry["id"])
        first=self.c.financial.portfolio_state("2026-01-03T00:00:00Z",self.account["id"],{self.asset["id"]:"1.25"})
        second=self.c.financial.portfolio_state("2026-01-03T00:00:00Z",self.account["id"],{self.asset["id"]:"1.25"})
        self.assertEqual(first["positions"][0]["quantity"],"100.125")
        self.assertEqual(first["cash"]["CNY"],"-123.7243125")
        self.assertEqual(first["calculation_id"],second["calculation_id"])

    def test_reversal_restores_state_without_editing_original_facts(self):
        opening=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="opening_balance",occurred_at="2026-01-01T00:00:00Z",amount="1000",currency="CNY",source="statement")
        self.c.financial.ledger_confirm(opening["id"]);self.c.financial.ledger_reverse(opening["id"],"wrong account","2026-01-02T00:00:00Z")
        state=self.c.financial.portfolio_state("2026-01-03T00:00:00Z",self.account["id"])
        self.assertEqual(state["cash"]["CNY"],"0")
        self.assertEqual(self.c.financial.ledger_get(opening["id"])["status"],"reversed")

    def test_simulation_never_changes_ledger_and_enforces_cash_constraint(self):
        opening=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="opening_balance",occurred_at="2026-01-01T00:00:00Z",amount="1000",currency="CNY",source="statement");self.c.financial.ledger_confirm(opening["id"])
        count=len(self.c.financial.ledger_list())
        result=self.c.financial.trade_impact("2026-01-03T00:00:00Z",self.account["id"],self.asset["id"],"900","1.1","1",{"minimum_cash":{"CNY":"100"}})
        self.assertTrue(result["blocked"]);self.assertEqual(len(self.c.financial.ledger_list()),count)

    def test_context_versions_attention_policy_and_no_silent_drift(self):
        original=self.c.cognition.context_current("attention")
        draft=self.c.cognition.context_create("attention",{"timezone":"UTC","daily_notification_budget":0,"topic_cooldown_seconds":0,"channels":{}},"user asks for silence")
        self.assertEqual(self.c.cognition.context_current("attention")["id"],original["id"])
        self.c.cognition.context_confirm(draft["id"])
        decision=self.c.attention.decide(topic="ordinary",materiality="normal",confidence="high",reason="test")
        self.assertEqual(decision["action"],"queue_digest")
        feedback=self.c.attention.feedback(decision["id"],"too_frequent")
        self.assertEqual(feedback["policy_change"],"proposal_required")
        self.assertEqual(self.c.cognition.context_current("attention")["id"],draft["id"])

    def test_decision_freezes_context_and_execution_requires_confirmed_ledger(self):
        investor=self.c.cognition.context_create("investor",{"confirmed_facts":["test"]});self.c.cognition.context_confirm(investor["id"])
        mandate=self.c.cognition.context_create("mandate",{"hard_constraints":{}});self.c.cognition.context_confirm(mandate["id"])
        calc=self.c.financial.portfolio_state("2026-01-03T00:00:00Z",self.account["id"])
        thesis=self.c.cognition.object_create("thesis",{"asset_id":self.asset["id"]});tr=self.c.cognition.publish(thesis["id"],"# Thesis R1\n",iso())
        decision=self.c.cognition.object_create("decision",{"asset_id":self.asset["id"]})
        with self.assertRaises(CompanionError):self.c.cognition.publish(decision["id"],"# bad\n")
        dr=self.c.cognition.publish(decision["id"],"# frozen decision\n",iso(),{"investor_revision_id":investor["id"],"mandate_revision_id":mandate["id"],"portfolio_calculation_id":calc["calculation_id"],"thesis_revision_ids":[tr["id"]]},[calc["calculation_id"]])
        execution=self.c.cognition.execution_create(decision["id"],{"side":"buy"})
        with self.assertRaises(CompanionError):self.c.cognition.execution_set_status(execution["id"],"filled",[])
        package=self.c.cognition.recovery_package("review",{"asset_id":self.asset["id"]})
        self.assertTrue(any(h["type"]=="thesis_revision" for h in package["handles"]))

    def test_reconciliation_does_not_auto_balance(self):
        opening=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="opening_balance",occurred_at="2026-01-01T00:00:00Z",amount="1000",currency="CNY",source="statement");self.c.financial.ledger_confirm(opening["id"])
        result=self.c.financial.reconcile(self.account["id"],"2026-01-02T00:00:00Z",{"cash":{"CNY":"900"},"positions":{}})
        self.assertEqual(result["status"],"needs_review")
        self.assertEqual(self.c.financial.portfolio_state("2026-01-02T00:00:00Z",self.account["id"])["cash"]["CNY"],"1000")

    def test_reconciliation_matches_only_with_cash_positions_valuations_and_total(self):
        opening=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="opening_balance",occurred_at="2026-01-01T00:00:00Z",amount="1000",currency="CNY",source="statement");self.c.financial.ledger_confirm(opening["id"])
        trade=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="trade",asset_id=self.asset["id"],occurred_at="2026-01-01T01:00:00Z",quantity="100",price="1",amount="-100",currency="CNY",source="statement");self.c.financial.ledger_confirm(trade["id"])
        self.c.financial.market_add(self.asset["id"],"close","2","2026-01-01T02:00:00Z","statement")

        incomplete=self.c.financial.reconcile(self.account["id"],"2026-01-02T00:00:00Z",{"cash":{"CNY":"900"},"positions":{self.asset["id"]:"100"}})
        self.assertEqual(incomplete["status"],"needs_review")
        self.assertEqual(incomplete["reconciliation"]["scope_status"]["valuations"]["status"],"unverified")
        self.assertEqual(incomplete["reconciliation"]["scope_status"]["total_value"]["status"],"unverified")

        wrong_value=self.c.financial.reconcile(self.account["id"],"2026-01-02T00:00:00Z",{
            "cash":{"CNY":"900"},
            "positions":{self.asset["id"]:"100"},
            "position_values":{self.asset["id"]:"199"},
            "position_total_by_currency":{"CNY":"199"},
            "total_by_currency":{"CNY":"1099"},
        })
        self.assertEqual(wrong_value["status"],"needs_review")
        self.assertIn("position_value",{item["kind"] for item in wrong_value["differences"]})
        self.assertIn("total_value",{item["kind"] for item in wrong_value["differences"]})

        matched=self.c.financial.reconcile(self.account["id"],"2026-01-02T00:00:00Z",{
            "cash":{"CNY":"900"},
            "positions":{self.asset["id"]:"100"},
            "position_values":{self.asset["id"]:"200"},
            "position_total_by_currency":{"CNY":"200"},
            "total_by_currency":{"CNY":"1100"},
        })
        self.assertEqual(matched["status"],"matched")
        self.assertTrue(matched["reconciliation"]["full_scope_matched"])
        self.assertEqual(matched["differences"],[])

    def test_csv_import_stays_unconfirmed_and_source_health_is_explicit(self):
        content=f"account_id,entry_type,occurred_at,amount,currency,external_id\n{self.account['id']},cash_deposit,2026-01-01T00:00:00Z,500,CNY,x1\n"
        imported=self.c.financial.ledger_import_csv(content)
        self.assertEqual(imported["requires_confirmation"],1)
        self.assertEqual(self.c.financial.portfolio_state("2026-01-02T00:00:00Z",self.account["id"])["cash"],{})
        health=self.c.source_health_record("broker","failed","timeout")
        self.assertEqual(health["consecutive_failures"],1)
        self.assertEqual(self.c.source_health_record("broker","healthy")["consecutive_failures"],0)

    def test_max_purchase_uses_decimal_and_lot_size(self):
        opening=self.c.financial.ledger_add(account_id=self.account["id"],entry_type="opening_balance",occurred_at="2026-01-01T00:00:00Z",amount="1000",currency="CNY",source="statement");self.c.financial.ledger_confirm(opening["id"])
        result=self.c.financial.max_purchase("2026-01-02T00:00:00Z",self.account["id"],self.asset["id"],"3.1","100","1","100")
        self.assertEqual(result["max_quantity"],"200")
        self.assertEqual(result["remaining_cash"],"379")


if __name__=="__main__":unittest.main()
