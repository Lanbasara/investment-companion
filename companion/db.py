from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .timeutil import iso

SCHEMA_VERSION = 3

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

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            con.executescript(V3_SCHEMA)
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(SCHEMA_VERSION),))
            con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES('created_at',?)", (iso(),))

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
