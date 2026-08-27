from __future__ import annotations

import hashlib


MIGRATION_009_ID = "0009_research_work_queue"
MIGRATION_009_SQL = r"""
CREATE TABLE research_work_items (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  work_type TEXT NOT NULL CHECK(work_type IN ('candidate_triage','full_research')),
  status TEXT NOT NULL CHECK(status IN ('queued','leased','waiting','monitoring','completed','rejected','failed','expired')),
  priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 100),
  source_manifest_id TEXT NOT NULL,
  parent_id TEXT,
  subject_json TEXT NOT NULL DEFAULT '{}',
  candidate_scope_json TEXT NOT NULL DEFAULT '[]',
  requirements_json TEXT NOT NULL DEFAULT '{}',
  result_refs_json TEXT NOT NULL DEFAULT '[]',
  disposition_json TEXT NOT NULL DEFAULT '{}',
  opportunity_id TEXT,
  due_at TEXT NOT NULL,
  next_check_at TEXT,
  lease_owner TEXT,
  lease_until TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT NOT NULL UNIQUE,
  version INTEGER NOT NULL DEFAULT 1,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY(source_manifest_id) REFERENCES artifact_manifests(id),
  FOREIGN KEY(parent_id) REFERENCES research_work_items(id),
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX idx_research_work_status_due
  ON research_work_items(status,due_at,priority);
CREATE INDEX idx_research_work_program
  ON research_work_items(program_id,status,updated_at);
CREATE INDEX idx_research_work_source
  ON research_work_items(source_manifest_id,work_type);
"""
MIGRATION_009_CHECKSUM = hashlib.sha256(MIGRATION_009_SQL.encode("utf-8")).hexdigest()
