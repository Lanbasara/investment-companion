from __future__ import annotations

from datetime import timedelta
from typing import Any

from ..db import row_dict, rows_dict
from ..foundation import CompanionError, canonical, new_id
from ..timeutil import iso, parse, utc_now


class ResearchWorkService:
    """Persistent coordination between candidate discovery and governed research.

    Work items never create Decisions, Executions or Ledger entries. Candidate
    manifests remain immutable inputs; Opportunities remain the research funnel.
    """

    CANDIDATE_KINDS = {
        "v6_fund_research_candidates",
        "v6_stock_research_candidates",
    }
    OPEN = {"queued", "leased", "waiting", "monitoring"}

    def __init__(self, companion):
        self.c = companion

    def enqueue_candidate_manifest(
        self,
        manifest_id: str,
        *,
        due_at: str | None = None,
        priority: int = 50,
        actor: str = "candidate-pipeline",
    ) -> dict[str, Any]:
        manifest = self.c.data.manifest_get(manifest_id, verify=True)
        if manifest["kind"] not in self.CANDIDATE_KINDS:
            raise CompanionError("research work requires a supported candidate manifest")
        body = manifest["manifest"].get("manifest", {})
        program_id = body.get("program_id")
        if not isinstance(program_id, str) or not program_id:
            raise CompanionError("candidate manifest lacks program_id")
        candidates = body.get("candidates", [])
        if not isinstance(candidates, list):
            raise CompanionError("candidate manifest candidates must be a list")
        scope = [self._candidate_identity(item) for item in candidates]
        if len(scope) != len(set(scope)):
            raise CompanionError("candidate manifest contains duplicate candidate identities")
        if not scope:
            return {"created": False, "item": None, "reason": "no_candidates"}
        if isinstance(priority, bool) or not isinstance(priority, int) or not 1 <= priority <= 100:
            raise CompanionError("research work priority must be within 1..100")
        deadline = due_at or iso(utc_now() + timedelta(hours=24))
        parse(deadline)
        key = f"candidate-triage:{manifest_id}"
        comparison_groups = self._comparison_groups(manifest["kind"], candidates)
        now = iso()
        with self.c.db.transaction() as con:
            existing = row_dict(con.execute(
                "SELECT * FROM research_work_items WHERE idempotency_key=?", (key,)
            ).fetchone())
            if existing:
                return {"created": False, "item": self._decode(existing), "reason": "idempotent"}
            item_id = new_id("researchwork")
            con.execute(
                "INSERT INTO research_work_items(id,program_id,work_type,status,priority,source_manifest_id,"
                "subject_json,candidate_scope_json,requirements_json,due_at,idempotency_key,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id, program_id, "candidate_triage", "queued", priority, manifest_id,
                    canonical({"cohort_kind": manifest["kind"]}), canonical(scope),
                    canonical({"complete_coverage": True, "outcomes": ["research", "reject", "monitor"],
                               "comparison_groups": comparison_groups, "max_research_per_group": 1}),
                    deadline, key, now, now,
                ),
            )
            self.c.audit.record(con, actor, "enqueue", "research_work_item", item_id, None,
                                {"source_manifest_id": manifest_id, "candidate_count": len(scope)},
                                "candidate cohort requires explicit disposition")
        return {"created": True, "item": self.get(item_id), "reason": "candidate_cohort"}

    def backfill_latest_candidates(self, *, actor: str = "research-work-backfill") -> dict[str, Any]:
        """Attach latest legacy cohorts and every post-migration cohort idempotently."""
        import json
        results = []
        with self.c.db.connect() as con:
            migration = con.execute(
                "SELECT applied_at FROM schema_migrations WHERE migration_id='0009_research_work_queue'"
            ).fetchone()
            activated_at = migration["applied_at"] if migration else iso()
            for kind in sorted(self.CANDIDATE_KINDS):
                rows = con.execute(
                    "SELECT id,created_at,manifest_json FROM artifact_manifests WHERE kind=? AND status='ready' ORDER BY created_at DESC,id DESC",
                    (kind,),
                ).fetchall()
                selected = []
                for row in rows:
                    body = json.loads(row["manifest_json"]).get("manifest", {})
                    if body.get("status") == "waiting_upstream" or not body.get("candidates"):
                        continue
                    if not selected or row["created_at"] >= activated_at:
                        selected.append(row["id"])
                for manifest_id in selected:
                    results.append(self.enqueue_candidate_manifest(manifest_id, actor=actor))
        return {
            "ok": True,
            "cohorts_checked": len(results),
            "created": sum(bool(item["created"]) for item in results),
            "items": [item["item"] for item in results if item["item"]],
        }

    def get(self, item_id: str) -> dict[str, Any]:
        with self.c.db.connect() as con:
            item = row_dict(con.execute("SELECT * FROM research_work_items WHERE id=?", (item_id,)).fetchone())
        if not item:
            raise CompanionError("research work item not found")
        return self._decode(item)

    def list(
        self, *, status: str | None = None, work_type: str | None = None,
        program_id: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise CompanionError("research work limit must be within 1..200")
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (("status", status), ("work_type", work_type), ("program_id", program_id)):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.c.db.connect() as con:
            rows = rows_dict(con.execute(
                f"SELECT * FROM research_work_items{where} ORDER BY "
                "CASE status WHEN 'leased' THEN 0 WHEN 'queued' THEN 1 WHEN 'waiting' THEN 2 "
                "WHEN 'monitoring' THEN 3 ELSE 4 END, priority DESC, due_at, created_at LIMIT ?",
                (*params, limit),
            ).fetchall())
        return [self._decode(item) for item in rows]

    def summary(self, *, program_id: str | None = None) -> dict[str, Any]:
        now = iso()
        where = " WHERE program_id=?" if program_id else ""
        params = (program_id,) if program_id else ()
        with self.c.db.connect() as con:
            counts = {row["status"]: row["n"] for row in con.execute(
                f"SELECT status,COUNT(*) AS n FROM research_work_items{where} GROUP BY status", params,
            ).fetchall()}
            overdue_where = " AND program_id=?" if program_id else ""
            overdue = con.execute(
                f"SELECT COUNT(*) FROM research_work_items WHERE status IN ('queued','leased','waiting') AND due_at<?{overdue_where}",
                (now, *params),
            ).fetchone()[0]
        return {
            "counts": counts,
            "open": sum(counts.get(state, 0) for state in self.OPEN),
            "overdue": overdue,
            "next": self.list(program_id=program_id, limit=5),
        }

    def claim(self, item_id: str, *, owner: str, lease_seconds: int = 1800) -> dict[str, Any]:
        if not owner.strip():
            raise CompanionError("research work claim requires owner")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 60 <= lease_seconds <= 7200:
            raise CompanionError("research work lease_seconds must be within 60..7200")
        now = iso(); lease_until = iso(utc_now() + timedelta(seconds=lease_seconds))
        with self.c.db.transaction() as con:
            before = row_dict(con.execute("SELECT * FROM research_work_items WHERE id=?", (item_id,)).fetchone())
            if not before or before["status"] not in {"queued", "waiting"}:
                raise CompanionError("research work item is not claimable")
            changed = con.execute(
                "UPDATE research_work_items SET status='leased',lease_owner=?,lease_until=?,"
                "attempt_count=attempt_count+1,version=version+1,updated_at=? WHERE id=? AND version=?",
                (owner, lease_until, now, item_id, before["version"]),
            ).rowcount
            if changed != 1:
                raise CompanionError("research work claim lost optimistic lock")
            self.c.audit.record(con, owner, "claim", "research_work_item", item_id, before,
                                {"lease_until": lease_until}, "research work claimed")
        return self.get(item_id)

    def complete_triage(
        self, item_id: str, *, owner: str, dispositions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        item = self.get(item_id)
        self._require_lease(item, owner, "candidate_triage")
        if not isinstance(dispositions, list):
            raise CompanionError("triage dispositions must be a list")
        by_candidate: dict[str, dict[str, Any]] = {}
        for raw in dispositions:
            if not isinstance(raw, dict):
                raise CompanionError("each triage disposition must be an object")
            candidate_id = raw.get("candidate_id")
            outcome = raw.get("outcome")
            reason = raw.get("reason")
            if candidate_id in by_candidate:
                raise CompanionError(f"triage has duplicate candidate_id: {candidate_id}")
            if candidate_id not in item["candidate_scope"]:
                raise CompanionError(f"triage has unknown candidate_id: {candidate_id}")
            if outcome not in {"research", "reject", "monitor"} or not isinstance(reason, str) or not reason.strip():
                raise CompanionError("triage disposition requires outcome and reason")
            by_candidate[candidate_id] = raw
        if set(by_candidate) != set(item["candidate_scope"]):
            missing = sorted(set(item["candidate_scope"]) - set(by_candidate))
            raise CompanionError(f"triage must explicitly cover every candidate; missing={missing}")
        for group, members in item["requirements"].get("comparison_groups", {}).items():
            selected = [candidate_id for candidate_id in members if by_candidate[candidate_id]["outcome"] in {"research", "monitor"}]
            if len(selected) > 1:
                raise CompanionError(f"ETF comparison group {group} may retain at most one research or monitoring representative: {selected}")
        now = iso(); children: list[str] = []
        with self.c.db.transaction() as con:
            current = row_dict(con.execute("SELECT * FROM research_work_items WHERE id=?", (item_id,)).fetchone())
            if not current or current["status"] != "leased" or current["lease_owner"] != owner or not current["lease_until"] or parse(current["lease_until"]) <= utc_now():
                raise CompanionError("research work lease is no longer owned")
            for candidate_id, disposition in by_candidate.items():
                outcome = disposition["outcome"]
                if outcome == "reject":
                    continue
                subject = disposition.get("subject") or {"asset_id": candidate_id}
                if not isinstance(subject, dict) or not subject:
                    raise CompanionError("research or monitor disposition requires subject")
                if self._subject_identity(subject) != candidate_id:
                    raise CompanionError(f"research subject must retain candidate identity: {candidate_id}")
                child_due = disposition.get("due_at") or iso(utc_now() + timedelta(hours=48))
                parse(child_due)
                next_check = disposition.get("next_check_at") if outcome == "monitor" else None
                if outcome == "monitor":
                    if not next_check or parse(next_check) <= utc_now():
                        raise CompanionError("monitor disposition requires a future next_check_at")
                child_id = new_id("researchwork")
                child_status = "monitoring" if outcome == "monitor" else "queued"
                child_key = f"full-research:{item['source_manifest_id']}:{candidate_id}"
                con.execute(
                    "INSERT INTO research_work_items(id,program_id,work_type,status,priority,source_manifest_id,parent_id,"
                    "subject_json,requirements_json,due_at,next_check_at,idempotency_key,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (child_id, item["program_id"], "full_research", child_status, item["priority"],
                     item["source_manifest_id"], item_id, canonical(subject),
                     canonical({"candidate_id": candidate_id, "required_outcome": ["promoted", "rejected", "monitoring"]}),
                     child_due, next_check, child_key, now, now),
                )
                children.append(child_id)
            con.execute(
                "UPDATE research_work_items SET status='completed',disposition_json=?,result_refs_json=?,"
                "lease_owner=NULL,lease_until=NULL,finished_at=?,updated_at=?,version=version+1 WHERE id=?",
                (canonical(dispositions), canonical(children), now, now, item_id),
            )
            self.c.audit.record(con, owner, "complete_triage", "research_work_item", item_id, current,
                                {"dispositions": dispositions, "child_ids": children},
                                "all candidates received an explicit disposition")
        return {"item": self.get(item_id), "children": [self.get(child_id) for child_id in children]}

    def complete_research(
        self, item_id: str, *, owner: str, outcome: str, reason: str,
        result_refs: list[str] | None = None, opportunity_id: str | None = None,
        next_check_at: str | None = None,
    ) -> dict[str, Any]:
        item = self.get(item_id)
        self._require_lease(item, owner, "full_research")
        if outcome not in {"promoted", "rejected", "monitoring"} or not reason.strip():
            raise CompanionError("research completion requires a supported outcome and reason")
        refs = result_refs or []
        if any(not isinstance(ref, str) or not ref for ref in refs):
            raise CompanionError("research result_refs must be non-empty strings")
        refs = self.c.operating._validate_evidence_refs(refs, immutable_only=True)
        promotion_snapshot = None
        if outcome == "promoted":
            if not opportunity_id:
                raise CompanionError("promoted research requires opportunity_id")
            with self.c.db.connect() as con:
                opportunity = row_dict(con.execute("SELECT * FROM opportunities WHERE id=?", (opportunity_id,)).fetchone())
            if not opportunity or opportunity["program_id"] != item["program_id"] or opportunity["status"] != "active":
                raise CompanionError("research opportunity must exist in the same program")
            candidate_id = item["requirements"].get("candidate_id")
            if not candidate_id or self._subject_identity(opportunity["subject"]) != candidate_id:
                raise CompanionError("research opportunity subject does not match the work item")
            if not opportunity.get("thesis_id"):
                raise CompanionError("promoted research opportunity requires an active Thesis")
            thesis = self.c.cognition.object_get(opportunity["thesis_id"])
            if thesis["status"] != "active":
                raise CompanionError("promoted research opportunity requires an active Thesis")
            if self._subject_identity(thesis["subject"]) != candidate_id:
                raise CompanionError("Opportunity Thesis subject does not match the research candidate")
            validation_refs = []
            for ref in refs:
                if not ref.startswith("calc_"):
                    continue
                calculation = self.c.financial.calculation_get(ref)
                revision_id = calculation.get("inputs", {}).get("thesis_revision_id")
                revision = self.c.cognition.revision_get(revision_id) if revision_id else None
                if (calculation["kind"] == "thesis_validation" and revision
                    and revision["object_id"] == opportunity["thesis_id"]
                    and revision["id"] == thesis.get("current_revision_id")
                    and self._subject_identity(calculation.get("inputs", {}).get("subject", {})) == candidate_id):
                    validation_refs.append(calculation)
            if not validation_refs or not any(
                item["outputs"].get("status") in {"eligible_for_bounded_action", "eligible_for_decision"}
                for item in validation_refs
            ):
                raise CompanionError("promoted research requires an eligible formal Thesis Validation")
            if item["source_manifest_id"] not in refs:
                raise CompanionError("promoted research must retain its source candidate manifest")
            original = validation_refs[-1]
            refreshed = self.c.research_validation.validate_thesis(
                thesis_revision_id=original["inputs"]["thesis_revision_id"],
                evidence_manifest_ids=original["inputs"]["evidence_manifest_ids"],
                knowledge_cutoff=iso(), validation_spec=original["assumptions"],
            )
            if refreshed["status"] not in {"eligible_for_bounded_action", "eligible_for_decision"}:
                raise CompanionError("formal Thesis Validation is no longer current or eligible")
            refs = list(dict.fromkeys([*refs, refreshed["calculation_id"]]))
            promotion_snapshot = {
                "opportunity_id": opportunity["id"], "opportunity_version": opportunity["version"],
                "thesis_id": thesis["id"], "thesis_current_revision_id": thesis["current_revision_id"],
            }
        elif opportunity_id:
            raise CompanionError("only promoted research may bind an opportunity")
        if outcome == "monitoring":
            if not next_check_at or parse(next_check_at) <= utc_now():
                raise CompanionError("monitoring research requires a future next_check_at")
            status = "monitoring"; finished_at = None
        else:
            status = "completed" if outcome == "promoted" else "rejected"
            finished_at = iso()
        now = iso()
        with self.c.db.transaction() as con:
            current = row_dict(con.execute("SELECT * FROM research_work_items WHERE id=?", (item_id,)).fetchone())
            if not current or current["status"] != "leased" or current["lease_owner"] != owner or not current["lease_until"] or parse(current["lease_until"]) <= utc_now():
                raise CompanionError("research work lease is no longer owned")
            if promotion_snapshot:
                current_opportunity = row_dict(con.execute(
                    "SELECT * FROM opportunities WHERE id=?", (promotion_snapshot["opportunity_id"],)
                ).fetchone())
                current_thesis = row_dict(con.execute(
                    "SELECT * FROM cognitive_objects WHERE id=?", (promotion_snapshot["thesis_id"],)
                ).fetchone())
                if (not current_opportunity or current_opportunity["status"] != "active"
                    or current_opportunity["version"] != promotion_snapshot["opportunity_version"]
                    or current_opportunity.get("thesis_id") != promotion_snapshot["thesis_id"]
                    or not current_thesis or current_thesis["status"] != "active"
                    or current_thesis.get("current_revision_id") != promotion_snapshot["thesis_current_revision_id"]):
                    raise CompanionError("Opportunity or Thesis changed during research completion; re-run validation")
            con.execute(
                "UPDATE research_work_items SET status=?,result_refs_json=?,disposition_json=?,opportunity_id=?,"
                "next_check_at=?,lease_owner=NULL,lease_until=NULL,finished_at=?,updated_at=?,version=version+1 WHERE id=?",
                (status, canonical(refs), canonical({"outcome": outcome, "reason": reason}), opportunity_id,
                 next_check_at, finished_at, now, item_id),
            )
            self.c.audit.record(con, owner, "complete_research", "research_work_item", item_id, current,
                                {"outcome": outcome, "opportunity_id": opportunity_id, "result_refs": refs}, reason)
        return self.get(item_id)

    def refresh(self) -> dict[str, int]:
        now = iso()
        with self.c.db.transaction() as con:
            leases = con.execute(
                "UPDATE research_work_items SET status='queued',lease_owner=NULL,lease_until=NULL,updated_at=?,"
                "version=version+1 WHERE status='leased' AND lease_until<=?", (now, now)
            ).rowcount
            monitors = con.execute(
                "UPDATE research_work_items SET status='queued',updated_at=?,version=version+1 "
                "WHERE status='monitoring' AND next_check_at<=?", (now, now)
            ).rowcount
        return {"research_work_leases": leases, "research_work_monitors": monitors}

    @staticmethod
    def _candidate_identity(item: Any) -> str:
        if not isinstance(item, dict):
            raise CompanionError("candidate entry must be an object")
        value = item.get("asset_id") or item.get("ts_code")
        if not isinstance(value, str) or not value.strip():
            raise CompanionError("candidate entry lacks asset_id or ts_code")
        return value.strip()

    @classmethod
    def _comparison_groups(cls, kind: str, candidates: list[dict[str, Any]]) -> dict[str, list[str]]:
        if kind != "v6_fund_research_candidates":
            return {}
        grouped: dict[str, list[str]] = {}
        for item in candidates:
            candidate_id = cls._candidate_identity(item)
            labels = (item.get("classification") or {}).get("labels", [])
            tracking = next((label for label in labels if isinstance(label, str) and label.startswith("tracking_index:")), None)
            if tracking:
                grouped.setdefault(tracking, []).append(candidate_id)
        return {key: sorted(value) for key, value in sorted(grouped.items()) if len(value) > 1}

    @staticmethod
    def _subject_identity(subject: Any) -> str | None:
        if not isinstance(subject, dict): return None
        value = subject.get("asset_id") or subject.get("ts_code")
        return value.strip() if isinstance(value, str) and value.strip() else None

    @staticmethod
    def _require_lease(item: dict[str, Any], owner: str, work_type: str) -> None:
        if item["work_type"] != work_type or item["status"] != "leased" or item["lease_owner"] != owner:
            raise CompanionError(f"research work requires an active {work_type} lease owned by {owner}; current status={item['status']}, owner={item.get('lease_owner')}")
        if not item["lease_until"] or parse(item["lease_until"]) <= utc_now():
            raise CompanionError("research work lease expired; call work_claim again")

    @staticmethod
    def _decode(item: dict[str, Any]) -> dict[str, Any]:
        # db.row_dict already decodes every *_json column into its public name.
        return dict(item)
