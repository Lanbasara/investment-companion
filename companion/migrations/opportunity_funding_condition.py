from __future__ import annotations

import hashlib


MIGRATION_011_ID = "0011_opportunity_funding_condition"
MIGRATION_011_SQL = r"""
CREATE TABLE opportunity_funding_condition_transitions (
  id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL,
  from_calculation_id TEXT,
  to_calculation_id TEXT NOT NULL,
  from_version INTEGER NOT NULL,
  to_version INTEGER NOT NULL,
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  CHECK(to_version=from_version+1),
  CHECK(from_calculation_id IS NULL OR from_calculation_id<>to_calculation_id),
  UNIQUE(opportunity_id,to_version),
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id),
  FOREIGN KEY(from_calculation_id) REFERENCES calculations(id),
  FOREIGN KEY(to_calculation_id) REFERENCES calculations(id)
);
CREATE INDEX idx_opportunity_funding_condition_history
  ON opportunity_funding_condition_transitions(opportunity_id,to_version);
"""
MIGRATION_011_CHECKSUM = hashlib.sha256(
    MIGRATION_011_SQL.encode("utf-8")
).hexdigest()
