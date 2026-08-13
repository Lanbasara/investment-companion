from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .timeutil import iso

SCHEMA_VERSION = 1

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
