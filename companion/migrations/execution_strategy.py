from __future__ import annotations

import hashlib


MIGRATION_008_ID = "0008_broker_managed_execution_strategies"
MIGRATION_008_SQL = r"""
CREATE TABLE broker_execution_plans (
  id TEXT PRIMARY KEY, program_id TEXT NOT NULL, queue_id TEXT NOT NULL,
  decision_revision_id TEXT NOT NULL, broker TEXT NOT NULL CHECK(broker IN ('cicc_wealth')),
  plan_type TEXT NOT NULL CHECK(plan_type IN ('priced_buy','priced_sell','bracket_exit','moving_grid')),
  account_id TEXT NOT NULL, asset_id TEXT NOT NULL, spec_json TEXT NOT NULL,
  semantics_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','presented','accepted','configured','active','sleeping','termination_pending','terminated','reconciled','exception','expired','cancelled')),
  broker_condition_ref TEXT, valid_until TEXT NOT NULL, content_hash TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE, status_reason TEXT, configured_at TEXT,
  terminated_at TEXT, current_reference_price_text TEXT,
  buy_direction_state TEXT NOT NULL DEFAULT 'active' CHECK(buy_direction_state IN ('active','sleeping')),
  sell_direction_state TEXT NOT NULL DEFAULT 'active' CHECK(sell_direction_state IN ('active','sleeping')),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(queue_id) REFERENCES decision_queue_items(id),
  FOREIGN KEY(decision_revision_id) REFERENCES cognitive_revisions(id),
  FOREIGN KEY(account_id) REFERENCES accounts(id), FOREIGN KEY(asset_id) REFERENCES assets(id)
);
CREATE INDEX idx_broker_execution_plans_status ON broker_execution_plans(status,valid_until);
CREATE TABLE broker_managed_orders (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, broker_order_ref TEXT NOT NULL,
  execution_id TEXT, condition_leg TEXT CHECK(condition_leg IS NULL OR condition_leg IN ('take_profit','stop_loss')),
  side TEXT NOT NULL CHECK(side IN ('buy','sell')),
  status TEXT NOT NULL CHECK(status IN ('triggered','submitted','partially_filled','filled','cancelled','rejected','unknown')),
  quantity_text TEXT NOT NULL, submitted_quantity_text TEXT NOT NULL, cancelled_quantity_text TEXT NOT NULL DEFAULT '0', trigger_price_text TEXT, reference_price_before_text TEXT,
  reference_price_after_text TEXT, reference_update_reason TEXT, triggered_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, UNIQUE(plan_id,broker_order_ref),
  FOREIGN KEY(plan_id) REFERENCES broker_execution_plans(id),
  FOREIGN KEY(execution_id) REFERENCES executions(id)
);
CREATE INDEX idx_broker_managed_orders_plan ON broker_managed_orders(plan_id,triggered_at);
CREATE TABLE broker_execution_events (
  id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, order_id TEXT,
  event_type TEXT NOT NULL CHECK(event_type IN ('configured','activated','sleep_entered','sleep_exited','triggered','order_status','reference_updated','termination_requested','terminated','corporate_action','reconciled','exception','correction')),
  occurred_at TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
  FOREIGN KEY(plan_id) REFERENCES broker_execution_plans(id),
  FOREIGN KEY(order_id) REFERENCES broker_managed_orders(id)
);
CREATE INDEX idx_broker_execution_events_plan ON broker_execution_events(plan_id,occurred_at);
"""
MIGRATION_008_CHECKSUM = hashlib.sha256(MIGRATION_008_SQL.encode("utf-8")).hexdigest()
