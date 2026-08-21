from __future__ import annotations

import json
import hashlib
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .timeutil import iso

SCHEMA_VERSION = 6

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  before_json TEXT,
  after_json TEXT,
  reason TEXT
);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('patrol','review','maintenance','one_shot')),
  status TEXT NOT NULL CHECK(status IN ('active','paused','archived','expired')),
  mission TEXT NOT NULL,
  scope_json TEXT NOT NULL DEFAULT '{}',
  cadence_json TEXT NOT NULL,
  policy_json TEXT NOT NULL DEFAULT '{}',
  origin_json TEXT NOT NULL DEFAULT '{}',
  timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
  next_run_at TEXT,
  last_run_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(status, next_run_at);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  schedule_id TEXT,
  kind TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','leased','succeeded','failed','cancelled','recoverable')),
  due_at TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL DEFAULT '{}',
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(schedule_id) REFERENCES schedules(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_status_due ON runs(status, due_at);
CREATE TABLE IF NOT EXISTS watches (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','paused','archived','expired')),
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  intent TEXT NOT NULL,
  condition_json TEXT NOT NULL,
  schedule_id TEXT,
  origin_json TEXT NOT NULL DEFAULT '{}',
  ttl_at TEXT,
  max_runs INTEGER,
  run_count INTEGER NOT NULL DEFAULT 0,
  last_observation_json TEXT,
  last_state INTEGER NOT NULL DEFAULT 0,
  last_success_at TEXT,
  last_error TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(schedule_id) REFERENCES schedules(id)
);
CREATE INDEX IF NOT EXISTS idx_watches_subject ON watches(subject_type, subject_id, status);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY,
  watch_id TEXT,
  subject_type TEXT NOT NULL,
  subject_id TEXT NOT NULL,
  metric TEXT NOT NULL,
  value_json TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  source TEXT NOT NULL,
  source_ref TEXT,
  quality_json TEXT NOT NULL DEFAULT '{}',
  fingerprint TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(watch_id) REFERENCES watches(id)
);
CREATE TABLE IF NOT EXISTS source_items (
  id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  source_key TEXT,
  title TEXT NOT NULL,
  url TEXT,
  published_at TEXT,
  captured_at TEXT NOT NULL,
  content_path TEXT,
  content_hash TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','triaged','linked','archived','duplicate')),
  UNIQUE(source, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_source_items_status_time ON source_items(status, captured_at);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  subject_type TEXT,
  subject_id TEXT,
  watch_id TEXT,
  case_id TEXT,
  occurred_at TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  summary TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'detected' CHECK(status IN ('detected','queued','delivered','handled','dead')),
  handled_at TEXT,
  handling_note TEXT,
  FOREIGN KEY(watch_id) REFERENCES watches(id)
);
CREATE INDEX IF NOT EXISTS idx_events_status_time ON events(status, detected_at);
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('proposed','open','investigating','awaiting_evidence','ready_for_judgment','resolved','monitoring','rejected','superseded','expired')),
  subject_json TEXT NOT NULL DEFAULT '{}',
  origin_json TEXT NOT NULL DEFAULT '{}',
  root_path TEXT NOT NULL,
  brief_path TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  close_reason TEXT
);
CREATE TABLE IF NOT EXISTS patrols (
  id TEXT PRIMARY KEY,
  case_id TEXT,
  schedule_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('commissioned','scanning','returned','failed','expired')),
  disposition TEXT,
  brief_path TEXT NOT NULL,
  result_path TEXT,
  budget_json TEXT NOT NULL DEFAULT '{}',
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  FOREIGN KEY(case_id) REFERENCES cases(id),
  FOREIGN KEY(schedule_id) REFERENCES schedules(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  subject_json TEXT NOT NULL DEFAULT '{}',
  case_id TEXT,
  watch_id TEXT,
  event_id TEXT,
  status TEXT NOT NULL DEFAULT 'current' CHECK(status IN ('current','superseded','contradicted','background','unresolved','duplicate','archived')),
  effective_at TEXT,
  supersedes TEXT,
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_case_status ON artifacts(case_id, status);
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY,
  event_id TEXT,
  kind TEXT NOT NULL,
  destination TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sending','sent','retry','dead')),
  attempt INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  sent_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(event_id) REFERENCES events(id)
);
CREATE INDEX IF NOT EXISTS idx_outbox_due ON outbox(status, available_at);
CREATE TABLE IF NOT EXISTS locks (
  name TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

V3_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS accounts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, institution TEXT, base_currency TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','closed')),
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, asset_type TEXT NOT NULL, name TEXT NOT NULL, currency TEXT NOT NULL,
  identifiers_json TEXT NOT NULL DEFAULT '{}', metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_entries (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, entry_type TEXT NOT NULL,
  asset_id TEXT, occurred_at TEXT NOT NULL, settled_at TEXT, quantity_text TEXT,
  price_text TEXT, amount_text TEXT NOT NULL, currency TEXT NOT NULL,
  fee_text TEXT NOT NULL DEFAULT '0', status TEXT NOT NULL CHECK(status IN ('draft','needs_confirmation','confirmed','reversed')),
  source TEXT NOT NULL, external_id TEXT, reversal_of TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
  fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, confirmed_at TEXT,
  FOREIGN KEY(account_id) REFERENCES accounts(id), FOREIGN KEY(asset_id) REFERENCES assets(id),
  FOREIGN KEY(reversal_of) REFERENCES ledger_entries(id)
);
CREATE INDEX IF NOT EXISTS idx_ledger_account_time ON ledger_entries(account_id,status,occurred_at);
CREATE TABLE IF NOT EXISTS market_snapshots (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, metric TEXT NOT NULL, value_text TEXT NOT NULL,
  currency TEXT, observed_at TEXT NOT NULL, source TEXT NOT NULL, quality TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', fingerprint TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(id)
);
CREATE TABLE IF NOT EXISTS calculations (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, purpose TEXT NOT NULL, engine_version TEXT NOT NULL,
  as_of TEXT NOT NULL, inputs_json TEXT NOT NULL, assumptions_json TEXT NOT NULL DEFAULT '{}',
  formulas_json TEXT NOT NULL, outputs_json TEXT NOT NULL, warnings_json TEXT NOT NULL DEFAULT '[]',
  reproducibility_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliations (
  id TEXT PRIMARY KEY, account_id TEXT NOT NULL, as_of TEXT NOT NULL, statement_json TEXT NOT NULL,
  computed_json TEXT NOT NULL, differences_json TEXT NOT NULL, status TEXT NOT NULL,
  source_ref TEXT, created_at TEXT NOT NULL, resolved_at TEXT,
  FOREIGN KEY(account_id) REFERENCES accounts(id)
);
CREATE TABLE IF NOT EXISTS context_revisions (
  id TEXT PRIMARY KEY, context_type TEXT NOT NULL CHECK(context_type IN ('investor','mandate','attention')),
  revision INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('draft','current','trial','superseded','expired')),
  content_json TEXT NOT NULL, effective_from TEXT, expires_at TEXT, parent_id TEXT,
  reason TEXT, content_hash TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT,
  UNIQUE(context_type,revision), FOREIGN KEY(parent_id) REFERENCES context_revisions(id)
);
CREATE INDEX IF NOT EXISTS idx_context_current ON context_revisions(context_type,status);
CREATE TABLE IF NOT EXISTS cognitive_objects (
  id TEXT PRIMARY KEY, object_type TEXT NOT NULL CHECK(object_type IN ('thesis','decision','review')),
  subject_json TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL, current_revision_id TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cognitive_revisions (
  id TEXT PRIMARY KEY, object_id TEXT NOT NULL, revision INTEGER NOT NULL, status TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE, content_hash TEXT NOT NULL, parent_id TEXT, knowledge_cutoff TEXT,
  context_refs_json TEXT NOT NULL DEFAULT '{}', calculation_ids_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(object_id,revision), FOREIGN KEY(object_id) REFERENCES cognitive_objects(id),
  FOREIGN KEY(parent_id) REFERENCES cognitive_revisions(id)
);
CREATE TABLE IF NOT EXISTS cognitive_links (
  id TEXT PRIMARY KEY, from_id TEXT NOT NULL, to_id TEXT NOT NULL, link_type TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  UNIQUE(from_id,to_id,link_type)
);
CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY, decision_id TEXT, status TEXT NOT NULL CHECK(status IN ('proposed','accepted','ordered','partially_filled','filled','cancelled','expired')),
  details_json TEXT NOT NULL DEFAULT '{}', ledger_entry_ids_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(decision_id) REFERENCES cognitive_objects(id)
);
CREATE TABLE IF NOT EXISTS attention_decisions (
  id TEXT PRIMARY KEY, event_id TEXT, policy_revision_id TEXT NOT NULL, action TEXT NOT NULL,
  topic TEXT NOT NULL, materiality TEXT NOT NULL, confidence TEXT NOT NULL,
  reason TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]', notification_key TEXT UNIQUE,
  status TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
  FOREIGN KEY(event_id) REFERENCES events(id), FOREIGN KEY(policy_revision_id) REFERENCES context_revisions(id)
);
CREATE TABLE IF NOT EXISTS attention_feedback (
  id TEXT PRIMARY KEY, attention_decision_id TEXT NOT NULL, feedback TEXT NOT NULL,
  note TEXT, created_at TEXT NOT NULL, FOREIGN KEY(attention_decision_id) REFERENCES attention_decisions(id)
);
CREATE TABLE IF NOT EXISTS recovery_packages (
  id TEXT PRIMARY KEY, purpose TEXT NOT NULL, subject_json TEXT NOT NULL DEFAULT '{}',
  as_of TEXT NOT NULL, handles_json TEXT NOT NULL, included_reason_json TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]', content_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
  source TEXT PRIMARY KEY, status TEXT NOT NULL, last_success_at TEXT, last_attempt_at TEXT,
  cursor TEXT, coverage_json TEXT NOT NULL DEFAULT '{}', consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_error TEXT, updated_at TEXT NOT NULL
);
"""


MIGRATION_TABLE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS schema_migrations (
  migration_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL UNIQUE,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);
"""


MIGRATION_004_ID = "0004_v4_research_platform"
MIGRATION_004_SQL = r"""
ALTER TABLE schedules ADD COLUMN dispatch_type TEXT NOT NULL DEFAULT 'codex_turn'
  CHECK(dispatch_type IN ('codex_turn','deterministic_pipeline'));
ALTER TABLE schedules ADD COLUMN job_definition_id TEXT;
ALTER TABLE runs ADD COLUMN dispatch_type TEXT NOT NULL DEFAULT 'codex_turn'
  CHECK(dispatch_type IN ('codex_turn','deterministic_pipeline'));
ALTER TABLE runs ADD COLUMN job_run_id TEXT;

CREATE TABLE executions_v4 (
  id TEXT PRIMARY KEY,
  decision_id TEXT,
  decision_revision_id TEXT,
  manual_action_spec_hash TEXT,
  status TEXT NOT NULL CHECK(status IN (
    'proposed','presented','accepted','rejected','ordered','partially_filled','filled',
    'cancelled','expired','superseded','deviated'
  )),
  details_json TEXT NOT NULL DEFAULT '{}',
  ledger_entry_ids_json TEXT NOT NULL DEFAULT '[]',
  idempotency_key TEXT UNIQUE,
  status_reason TEXT,
  presented_at TEXT,
  accepted_at TEXT,
  ordered_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(decision_id) REFERENCES cognitive_objects(id),
  FOREIGN KEY(decision_revision_id) REFERENCES cognitive_revisions(id)
);
INSERT INTO executions_v4(
  id,decision_id,status,details_json,ledger_entry_ids_json,created_at,updated_at
) SELECT id,decision_id,status,details_json,ledger_entry_ids_json,created_at,updated_at FROM executions;
DROP TABLE executions;
ALTER TABLE executions_v4 RENAME TO executions;
CREATE INDEX idx_executions_decision_revision ON executions(decision_revision_id,status);

CREATE TABLE feature_flags (
  key TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
  config_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
CREATE TABLE gate_assessments (
  id TEXT PRIMARY KEY,
  gate TEXT NOT NULL CHECK(gate IN ('G0','G1','G2','G3','G4','G5','G6')),
  status TEXT NOT NULL CHECK(status IN ('go','conditional_go','no_go','pending')),
  scope TEXT NOT NULL CHECK(scope IN ('production','test_fixture')),
  evidence_manifest_id TEXT NOT NULL,
  conditions_json TEXT NOT NULL DEFAULT '[]',
  code_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  assessed_by TEXT NOT NULL,
  approval_ref TEXT,
  supersedes TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(evidence_manifest_id) REFERENCES artifact_manifests(id),
  FOREIGN KEY(supersedes) REFERENCES gate_assessments(id)
);
CREATE INDEX idx_gate_assessments_latest ON gate_assessments(gate,created_at);

CREATE TABLE job_definitions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  handler TEXT NOT NULL,
  handler_version TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'inactive' CHECK(status IN ('inactive','active','paused','archived')),
  input_schema_json TEXT NOT NULL DEFAULT '{}',
  output_schema_json TEXT NOT NULL DEFAULT '{}',
  resource_budget_json TEXT NOT NULL DEFAULT '{}',
  config_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE job_runs (
  id TEXT PRIMARY KEY,
  parent_run_id TEXT NOT NULL UNIQUE,
  job_definition_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','leased','running','succeeded','failed','cancelled','recoverable','blocked')),
  idempotency_key TEXT NOT NULL UNIQUE,
  inputs_json TEXT NOT NULL DEFAULT '{}',
  output_manifest_id TEXT,
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(parent_run_id) REFERENCES runs(id),
  FOREIGN KEY(job_definition_id) REFERENCES job_definitions(id)
);
CREATE INDEX idx_job_runs_due ON job_runs(status,created_at);
CREATE TABLE job_steps (
  id TEXT PRIMARY KEY,
  job_run_id TEXT NOT NULL,
  name TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  handler TEXT NOT NULL,
  handler_version TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pending','leased','running','succeeded','failed','cancelled','recoverable','blocked')),
  idempotency_key TEXT NOT NULL UNIQUE,
  input_refs_json TEXT NOT NULL DEFAULT '[]',
  output_refs_json TEXT NOT NULL DEFAULT '[]',
  result_json TEXT NOT NULL DEFAULT '{}',
  attempt INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until TEXT,
  started_at TEXT,
  finished_at TEXT,
  duration_ms INTEGER,
  resource_usage_json TEXT NOT NULL DEFAULT '{}',
  error TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(job_run_id,name),
  FOREIGN KEY(job_run_id) REFERENCES job_runs(id)
);

CREATE TABLE data_objects (
  id TEXT PRIMARY KEY,
  data_root_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  kind TEXT NOT NULL,
  media_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','quarantined','missing')),
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  verified_at TEXT,
  UNIQUE(data_root_id,relative_path,kind,media_type,metadata_json)
);
CREATE INDEX idx_data_objects_hash ON data_objects(content_hash,status);
CREATE TABLE artifact_manifests (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  schema_version TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  supersedes TEXT,
  status TEXT NOT NULL DEFAULT 'ready' CHECK(status IN ('ready','superseded','invalid')),
  created_at TEXT NOT NULL,
  FOREIGN KEY(supersedes) REFERENCES artifact_manifests(id)
);

CREATE TABLE source_capabilities (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  capability TEXT NOT NULL,
  connector TEXT NOT NULL,
  account_scope TEXT NOT NULL DEFAULT 'default',
  status TEXT NOT NULL CHECK(status IN (
    'unknown','healthy','connector_missing','unauthorized','invalid_request','rate_limited',
    'stale','partial','empty_valid','failed'
  )),
  permission_json TEXT NOT NULL DEFAULT '{}',
  limits_json TEXT NOT NULL DEFAULT '{}',
  history_json TEXT NOT NULL DEFAULT '{}',
  latency_json TEXT NOT NULL DEFAULT '{}',
  fields_json TEXT NOT NULL DEFAULT '{}',
  revision_json TEXT NOT NULL DEFAULT '{}',
  license_json TEXT NOT NULL DEFAULT '{}',
  failure_json TEXT NOT NULL DEFAULT '{}',
  evidence_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE adapter_streams (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  capability TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('inactive','canary','active','paused','blocked','archived')),
  schema_version TEXT NOT NULL,
  config_json TEXT NOT NULL DEFAULT '{}',
  cursor TEXT,
  watermark TEXT,
  last_success_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(provider,capability)
);
CREATE TABLE adapter_batches (
  id TEXT PRIMARY KEY,
  stream_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('fetching','raw_ready','normalizing','ready','blocked','failed','empty_valid')),
  idempotency_key TEXT NOT NULL UNIQUE,
  request_range_json TEXT NOT NULL DEFAULT '{}',
  cursor_before TEXT,
  cursor_after TEXT,
  raw_object_ids_json TEXT NOT NULL DEFAULT '[]',
  canonical_object_ids_json TEXT NOT NULL DEFAULT '[]',
  row_count INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  FOREIGN KEY(stream_id) REFERENCES adapter_streams(id)
);
CREATE TABLE asset_identifiers (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  identifier_type TEXT NOT NULL,
  identifier_value TEXT NOT NULL,
  effective_at TEXT NOT NULL,
  effective_to TEXT,
  first_known_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  supersedes TEXT,
  raw_hash TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  quality_json TEXT NOT NULL DEFAULT '{}',
  FOREIGN KEY(asset_id) REFERENCES assets(id),
  FOREIGN KEY(supersedes) REFERENCES asset_identifiers(id),
  UNIQUE(provider,identifier_type,identifier_value,revision_id)
);
CREATE INDEX idx_asset_identifiers_pit ON asset_identifiers(provider,identifier_value,first_known_at,effective_at);
CREATE TABLE dataset_snapshots (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK(status IN ('building','ready','blocked','superseded','invalid')),
  knowledge_cutoff TEXT NOT NULL,
  manifest_id TEXT NOT NULL UNIQUE,
  content_hash TEXT NOT NULL UNIQUE,
  denominator_hash TEXT NOT NULL,
  universe_hash TEXT NOT NULL,
  quality_json TEXT NOT NULL DEFAULT '{}',
  code_version TEXT NOT NULL,
  supersedes TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(manifest_id) REFERENCES artifact_manifests(id),
  FOREIGN KEY(supersedes) REFERENCES dataset_snapshots(id)
);
CREATE TABLE data_quality_issues (
  id TEXT PRIMARY KEY,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','error','critical')),
  code TEXT NOT NULL,
  message TEXT NOT NULL,
  blocker INTEGER NOT NULL DEFAULT 0 CHECK(blocker IN (0,1)),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','acknowledged','resolved','waived')),
  evidence_json TEXT NOT NULL DEFAULT '{}',
  resolved_by TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE research_hypotheses (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','preregistered','rejected','retired')),
  spec_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  experiment_budget INTEGER NOT NULL,
  experiment_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE strategy_versions (
  id TEXT PRIMARY KEY,
  hypothesis_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('preregistered','research_passed','shadow','rejected','retired')),
  spec_json TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  parent_id TEXT,
  code_ref TEXT NOT NULL,
  environment_ref TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(hypothesis_id,version),
  FOREIGN KEY(hypothesis_id) REFERENCES research_hypotheses(id),
  FOREIGN KEY(parent_id) REFERENCES strategy_versions(id)
);
CREATE TABLE experiment_runs (
  id TEXT PRIMARY KEY,
  strategy_version_id TEXT NOT NULL,
  dataset_snapshot_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed','rejected','cancelled')),
  idempotency_key TEXT NOT NULL UNIQUE,
  split_json TEXT NOT NULL,
  params_json TEXT NOT NULL DEFAULT '{}',
  seed INTEGER NOT NULL,
  job_run_id TEXT,
  spec_object_id TEXT,
  execution_lineage_hash TEXT,
  bundle_manifest_id TEXT,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  holdout_accessed_at TEXT,
  started_at TEXT,
  finished_at TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id),
  FOREIGN KEY(dataset_snapshot_id) REFERENCES dataset_snapshots(id),
  FOREIGN KEY(bundle_manifest_id) REFERENCES artifact_manifests(id)
  ,FOREIGN KEY(job_run_id) REFERENCES job_runs(id)
);
CREATE TABLE promotion_decisions (
  id TEXT PRIMARY KEY,
  experiment_run_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('reject','revise','research_passed','shadow','retire')),
  reason TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  actor TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(experiment_run_id) REFERENCES experiment_runs(id)
);
CREATE TABLE agent_invocations (
  id TEXT PRIMARY KEY,
  invocation_ref TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL,
  model TEXT NOT NULL,
  prompt_template TEXT NOT NULL,
  input_refs_json TEXT NOT NULL,
  output_manifest_id TEXT NOT NULL,
  output_hash TEXT NOT NULL,
  token_usage_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('succeeded','failed','rejected')),
  adopted INTEGER NOT NULL DEFAULT 0 CHECK(adopted IN (0,1)),
  adoption_reason TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(output_manifest_id) REFERENCES artifact_manifests(id)
);

CREATE TABLE shadow_books (
  id TEXT PRIMARY KEY,
  strategy_version_id TEXT NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','active','paused','completed','retired','insufficient_evidence')),
  base_currency TEXT NOT NULL,
  initial_cash_text TEXT NOT NULL,
  reality_spec_json TEXT NOT NULL,
  sample_gate_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id)
);
CREATE TABLE shadow_rebalances (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL,
  signal_snapshot_id TEXT NOT NULL,
  execution_snapshot_id TEXT NOT NULL,
  experiment_run_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  target_manifest_id TEXT NOT NULL,
  denominator_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('proposed','simulated','partially_filled','filled','blocked','cancelled')),
  result_json TEXT NOT NULL DEFAULT '{}',
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(book_id) REFERENCES shadow_books(id),
  FOREIGN KEY(signal_snapshot_id) REFERENCES dataset_snapshots(id),
  FOREIGN KEY(execution_snapshot_id) REFERENCES dataset_snapshots(id),
  FOREIGN KEY(experiment_run_id) REFERENCES experiment_runs(id),
  FOREIGN KEY(target_manifest_id) REFERENCES artifact_manifests(id)
);
CREATE TABLE shadow_fills (
  id TEXT PRIMARY KEY,
  rebalance_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy','sell')),
  quantity_text TEXT NOT NULL,
  price_text TEXT NOT NULL,
  gross_text TEXT NOT NULL,
  fee_text TEXT NOT NULL,
  tax_text TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('simulated','blocked','expired')),
  reason TEXT,
  trade_date TEXT NOT NULL,
  settle_date TEXT,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(rebalance_id) REFERENCES shadow_rebalances(id),
  FOREIGN KEY(asset_id) REFERENCES assets(id)
);
CREATE TABLE shadow_metrics (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL,
  as_of TEXT NOT NULL,
  nav_text TEXT NOT NULL,
  cash_text TEXT NOT NULL,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(book_id) REFERENCES shadow_books(id),
  UNIQUE(book_id,as_of)
);

CREATE TABLE manual_action_specs (
  id TEXT PRIMARY KEY,
  decision_revision_id TEXT NOT NULL,
  content_hash TEXT NOT NULL UNIQUE,
  spec_json TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('draft','presented','accepted','rejected','expired','cancelled','superseded','invalid')),
  valid_until TEXT NOT NULL,
  supersedes TEXT,
  notification_key TEXT NOT NULL UNIQUE,
  invalidation_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(decision_revision_id) REFERENCES cognitive_revisions(id),
  FOREIGN KEY(supersedes) REFERENCES manual_action_specs(id)
);

INSERT OR IGNORE INTO feature_flags(key,enabled,config_json,updated_at) VALUES
  ('v4_jobs',0,'{}',CURRENT_TIMESTAMP),
  ('v4_live_data',0,'{}',CURRENT_TIMESTAMP),
  ('v4_shadow',0,'{}',CURRENT_TIMESTAMP),
  ('v4_decision_support',0,'{}',CURRENT_TIMESTAMP),
  ('v4_agent_research',0,'{}',CURRENT_TIMESTAMP);
"""


MIGRATION_005_ID = "0005_v5_investment_operating_system"
MIGRATION_005_SQL = r"""
CREATE TABLE investment_programs (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK(status IN ('draft','active','paused','superseded','archived')),
  current_revision_id TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  activated_at TEXT,
  closed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX idx_investment_programs_status ON investment_programs(status,updated_at);
CREATE UNIQUE INDEX idx_investment_program_single_active
  ON investment_programs(status) WHERE status='active';

CREATE TABLE investment_program_revisions (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft'
    CHECK(status IN ('draft','trial','current','superseded','expired')),
  content_json TEXT NOT NULL,
  context_refs_json TEXT NOT NULL,
  parent_id TEXT,
  reason TEXT,
  effective_from TEXT,
  expires_at TEXT,
  user_approval_ref TEXT,
  content_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  confirmed_at TEXT,
  UNIQUE(program_id,revision),
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(parent_id) REFERENCES investment_program_revisions(id)
);
CREATE INDEX idx_program_revisions_current
  ON investment_program_revisions(program_id,status,revision);

CREATE TABLE opportunities (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  subject_json TEXT NOT NULL,
  stage TEXT NOT NULL DEFAULT 'observed'
    CHECK(stage IN ('observed','researching','qualified','actionable')),
  status TEXT NOT NULL DEFAULT 'active'
    CHECK(status IN ('active','rejected','expired','closed')),
  thesis_id TEXT,
  strategy_version_id TEXT,
  decision_revision_id TEXT,
  qualification_json TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(thesis_id) REFERENCES cognitive_objects(id),
  FOREIGN KEY(strategy_version_id) REFERENCES strategy_versions(id),
  FOREIGN KEY(decision_revision_id) REFERENCES cognitive_revisions(id)
);
CREATE INDEX idx_opportunities_program_stage
  ON opportunities(program_id,status,stage,updated_at);

CREATE TABLE opportunity_transitions (
  id TEXT PRIMARY KEY,
  opportunity_id TEXT NOT NULL,
  from_stage TEXT NOT NULL,
  to_stage TEXT NOT NULL,
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  evidence_refs_json TEXT NOT NULL DEFAULT '[]',
  reason TEXT NOT NULL,
  actor TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
CREATE INDEX idx_opportunity_transitions_history
  ON opportunity_transitions(opportunity_id,created_at);

CREATE TABLE decision_queue_items (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  opportunity_id TEXT NOT NULL,
  decision_revision_id TEXT NOT NULL,
  manual_action_spec_id TEXT,
  state TEXT NOT NULL DEFAULT 'ready'
    CHECK(state IN ('ready','presented','snoozed','accepted','rejected','expired','closed')),
  version INTEGER NOT NULL DEFAULT 1,
  attention_decision_id TEXT,
  valid_until TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  response_reason TEXT,
  presented_at TEXT,
  snoozed_until TEXT,
  responded_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id),
  FOREIGN KEY(decision_revision_id) REFERENCES cognitive_revisions(id),
  FOREIGN KEY(manual_action_spec_id) REFERENCES manual_action_specs(id),
  FOREIGN KEY(attention_decision_id) REFERENCES attention_decisions(id)
);
CREATE INDEX idx_decision_queue_ready
  ON decision_queue_items(program_id,state,valid_until,created_at);
CREATE UNIQUE INDEX idx_decision_queue_active_decision
  ON decision_queue_items(decision_revision_id)
  WHERE state IN ('ready','presented','snoozed','accepted');

CREATE TABLE operating_briefs (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  program_revision_id TEXT NOT NULL,
  brief_type TEXT NOT NULL CHECK(brief_type IN ('daily','weekly','monthly')),
  period_key TEXT NOT NULL,
  revision INTEGER NOT NULL,
  as_of TEXT NOT NULL,
  conclusion TEXT NOT NULL
    CHECK(conclusion IN ('no_action','action','review_required','insufficient_evidence')),
  payload_json TEXT NOT NULL,
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  idempotency_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'ready'
    CHECK(status IN ('ready','presented','superseded')),
  supersedes TEXT,
  attention_decision_id TEXT,
  presented_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(program_revision_id) REFERENCES investment_program_revisions(id),
  FOREIGN KEY(attention_decision_id) REFERENCES attention_decisions(id),
  FOREIGN KEY(supersedes) REFERENCES operating_briefs(id),
  UNIQUE(program_id,brief_type,period_key,revision)
);
CREATE INDEX idx_operating_briefs_recent
  ON operating_briefs(program_id,brief_type,as_of);

CREATE TABLE program_scorecards (
  id TEXT PRIMARY KEY,
  program_id TEXT NOT NULL,
  program_revision_id TEXT NOT NULL,
  period_start TEXT NOT NULL,
  period_end TEXT NOT NULL,
  revision INTEGER NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('ready','insufficient_evidence')),
  metrics_json TEXT NOT NULL,
  comparisons_json TEXT NOT NULL DEFAULT '{}',
  source_refs_json TEXT NOT NULL DEFAULT '[]',
  caveats_json TEXT NOT NULL DEFAULT '[]',
  content_hash TEXT NOT NULL UNIQUE,
  supersedes TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(program_id) REFERENCES investment_programs(id),
  FOREIGN KEY(program_revision_id) REFERENCES investment_program_revisions(id),
  FOREIGN KEY(supersedes) REFERENCES program_scorecards(id),
  UNIQUE(program_id,period_start,period_end,revision)
);
CREATE INDEX idx_program_scorecards_period
  ON program_scorecards(program_id,period_end);

INSERT OR IGNORE INTO feature_flags(key,enabled,config_json,updated_at) VALUES
  ('v4_live_data_canary',0,'{}',CURRENT_TIMESTAMP),
  ('v4_decision_support_beta',0,'{}',CURRENT_TIMESTAMP),
  ('v5_operating_system',0,'{}',CURRENT_TIMESTAMP);
"""


MIGRATION_006_ID = "0006_v6_predictive_recommendations"
MIGRATION_006_SQL = r"""
INSERT OR IGNORE INTO feature_flags(key,enabled,config_json,updated_at) VALUES
  ('v6_predictive_recommendations',0,'{}',CURRENT_TIMESTAMP);
"""


BASELINE_MIGRATION_ID = "0003_v3_baseline"
BASELINE_CHECKSUM = hashlib.sha256((SCHEMA + "\n" + V3_SCHEMA).encode("utf-8")).hexdigest()
MIGRATION_004_CHECKSUM = hashlib.sha256(MIGRATION_004_SQL.encode("utf-8")).hexdigest()
MIGRATION_005_CHECKSUM = hashlib.sha256(MIGRATION_005_SQL.encode("utf-8")).hexdigest()
MIGRATION_006_CHECKSUM = hashlib.sha256(MIGRATION_006_SQL.encode("utf-8")).hexdigest()
MIGRATIONS = (
    (4, MIGRATION_004_ID, MIGRATION_004_SQL, MIGRATION_004_CHECKSUM),
    (5, MIGRATION_005_ID, MIGRATION_005_SQL, MIGRATION_005_CHECKSUM),
    (6, MIGRATION_006_ID, MIGRATION_006_SQL, MIGRATION_006_CHECKSUM),
)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def initialize(self, *, allow_migrate: bool = False) -> None:
        """Create or migrate the database without ever lowering an unknown schema.

        V1-V3 predated ordered migrations.  We register their exact DDL as a
        baseline, then apply every later migration atomically and verify its
        checksum on each startup.
        """
        with self.connect() as con:
            has_meta = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
            ).fetchone()
            current = None
            if has_meta:
                row = con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                current = int(row[0]) if row else None
                if current is not None and current > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"database schema {current} is newer than supported {SCHEMA_VERSION}"
                    )
                if current is not None and current < SCHEMA_VERSION and not allow_migrate:
                    raise RuntimeError(
                        f"database schema {current} requires explicit migration to {SCHEMA_VERSION}"
                    )

            con.executescript(SCHEMA)
            con.executescript(V3_SCHEMA)
            con.executescript(MIGRATION_TABLE_SCHEMA)
            con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)", (iso(),))
            if current is None:
                current = 3
                con.execute(
                    "INSERT INTO meta(key,value) VALUES('schema_version',?)",
                    (str(current),),
                )

            baseline = con.execute(
                "SELECT checksum FROM schema_migrations WHERE migration_id=?",
                (BASELINE_MIGRATION_ID,),
            ).fetchone()
            if baseline and baseline[0] != BASELINE_CHECKSUM:
                raise RuntimeError("V3 baseline migration checksum mismatch")
            if not baseline:
                con.execute(
                    "INSERT INTO schema_migrations(migration_id,version,checksum,applied_at) VALUES(?,?,?,?)",
                    (BASELINE_MIGRATION_ID, 3, BASELINE_CHECKSUM, iso()),
                )

            if current not in {3, 4, 5, 6}:
                raise RuntimeError(f"unsupported source schema version: {current}")

            for version, migration_id, _sql, checksum in MIGRATIONS:
                applied = con.execute(
                    "SELECT version,checksum FROM schema_migrations WHERE migration_id=?",
                    (migration_id,),
                ).fetchone()
                if applied and (applied[0] != version or applied[1] != checksum):
                    raise RuntimeError(f"Schema {version} migration checksum mismatch")
                if version <= current and not applied:
                    raise RuntimeError(
                        f"schema version {current} is missing ordered migration {migration_id}"
                    )

            if current == SCHEMA_VERSION:
                return

            pending = [item for item in MIGRATIONS if item[0] > current]
            if not pending or pending[-1][0] != SCHEMA_VERSION:
                raise RuntimeError(
                    f"no complete migration path from schema {current} to {SCHEMA_VERSION}"
                )
            script_parts = ["BEGIN IMMEDIATE;"]
            for version, migration_id, sql, checksum in pending:
                safe_id = migration_id.replace("'", "''")
                safe_checksum = checksum.replace("'", "''")
                applied_at = iso().replace("'", "''")
                script_parts.extend(
                    [
                        sql,
                        "INSERT INTO schema_migrations(migration_id,version,checksum,applied_at) "
                        f"VALUES('{safe_id}',{version},'{safe_checksum}','{applied_at}');",
                        f"UPDATE meta SET value='{version}' WHERE key='schema_version';",
                    ]
                )
            script_parts.append("COMMIT;")
            script = "\n".join(script_parts)
            try:
                con.executescript(script)
            except Exception:
                if con.in_transaction:
                    con.execute("ROLLBACK")
                raise

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield con
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

    def integrity_check(self) -> str:
        with self.connect() as con:
            return str(con.execute("PRAGMA integrity_check").fetchone()[0])

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_suffix(target.suffix + f".{os.getpid()}.tmp")
        with self.connect() as source, sqlite3.connect(temp) as dest:
            source.backup(dest)
        os.replace(temp, target)
        return target


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in list(result):
        if key.endswith("_json") and result[key] is not None:
            result[key[:-5]] = json.loads(result.pop(key))
    return result


def rows_dict(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [row_dict(row) for row in rows if row is not None]
