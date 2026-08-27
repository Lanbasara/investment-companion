from __future__ import annotations

import hashlib


MIGRATION_010_ID = "0010_account_continuity"
MIGRATION_010_SQL = r"""
CREATE TABLE account_continuity_confirmations (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  anchor_reconciliation_id TEXT NOT NULL,
  anchor_reconciliation_as_of TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('active','superseded','revoked')),
  scopes_json TEXT NOT NULL,
  reporting_commitment INTEGER NOT NULL CHECK(reporting_commitment IN (0,1)),
  user_confirmation_ref TEXT NOT NULL UNIQUE,
  confirmed_ledger_hash TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  revoked_at TEXT,
  revoke_reason TEXT,
  supersedes TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(account_id) REFERENCES accounts(id),
  FOREIGN KEY(anchor_reconciliation_id) REFERENCES reconciliations(id),
  FOREIGN KEY(supersedes) REFERENCES account_continuity_confirmations(id)
);
CREATE UNIQUE INDEX idx_account_continuity_active
  ON account_continuity_confirmations(account_id) WHERE status='active';
CREATE INDEX idx_account_continuity_account_time
  ON account_continuity_confirmations(account_id,confirmed_at);
"""
MIGRATION_010_CHECKSUM = hashlib.sha256(MIGRATION_010_SQL.encode("utf-8")).hexdigest()
