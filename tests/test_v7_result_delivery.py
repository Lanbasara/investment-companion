from __future__ import annotations

from types import SimpleNamespace

from companion.core import Companion
from companion.timeutil import iso, utc_now


def schedule(companion: Companion, *, name: str, mode: str) -> dict:
    return companion.schedule_create(
        name=name,
        kind="review",
        mission="Produce a bounded user-visible investment result.",
        cadence={"type": "interval", "seconds": 3600},
        policy={"delivery_mode": mode},
    )


def complete(companion: Companion, schedule_id: str) -> dict:
    run = companion.schedule_run_now(schedule_id)
    wake = next(item for item in companion.outbox_list() if item["status"] == "pending")
    companion.outbox_finish(wake["id"], True)
    return companion.complete_run(run["id"], True)


def prepare(companion: Companion, delivery_id: str) -> dict:
    return companion.delivery.prepare(
        delivery_id,
        conclusion="no_action",
        summary="This bounded review found no valid action card.",
        key_evidence=["Two frozen sources were checked."],
        next_step="Continue the approved observation plan.",
        next_check_at=iso(utc_now()),
        source_refs=["fixture_source"],
    )


def test_v7_report_required_is_not_delivered_until_actual_cc_connect_send(tmp_path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    run = complete(companion, schedule(companion, name="Required report", mode="report_required")["id"])
    delivery = companion.delivery.for_run(run["id"])
    assert delivery["status"] == "pending_content"

    prepared = prepare(companion, delivery["id"])
    assert prepared["status"] == "pending_send"
    assert companion.system_status()["delivery"]["overdue_required"] >= 0

    calls = []
    monkeypatch.delenv("COMPANION_CC_WAKE_CRON", raising=False)
    monkeypatch.setattr(
        "companion.platform.outbox.subprocess.run",
        lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0, stdout="sent", stderr=""),
    )
    result = companion.dispatch_outbox(limit=1)
    assert result["results"][0]["sent"] is True
    assert "结论：不行动" in calls[0][-1]
    assert companion.delivery.get(delivery["id"])["status"] == "delivered"


def test_v7_digest_is_one_send_with_receipts_for_all_component_runs(tmp_path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    one = complete(companion, schedule(companion, name="Patrol", mode="digest_required")["id"])
    two = complete(companion, schedule(companion, name="Scan", mode="digest_required")["id"])
    records = [companion.delivery.for_run(one["id"]), companion.delivery.for_run(two["id"])]
    assert [item["status"] for item in records] == ["queued_digest", "queued_digest"]
    for item in records:
        prepare(companion, item["id"])
    queued = companion.delivery.digest_send(
        [item["id"] for item in records],
        conclusion="no_action",
        summary="The close digest contains both completed components.",
        key_evidence=["Patrol coverage passed.", "Quant scan produced no action card."],
        next_step="Read the next close digest.",
        next_check_at=iso(utc_now()),
        source_refs=["brief_fixture"],
    )
    assert all(item["status"] == "pending_send" for item in queued)

    monkeypatch.delenv("COMPANION_CC_WAKE_CRON", raising=False)
    monkeypatch.setattr("companion.platform.outbox.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="sent", stderr=""))
    result = companion.dispatch_outbox(limit=1)
    assert result["results"][0]["sent"] is True
    assert all(companion.delivery.get(item["id"])["status"] == "delivered" for item in records)


def test_v7_failed_result_send_remains_retryable(tmp_path, monkeypatch):
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    run = complete(companion, schedule(companion, name="Retry report", mode="report_required")["id"])
    record = prepare(companion, companion.delivery.for_run(run["id"])["id"])
    monkeypatch.delenv("COMPANION_CC_WAKE_CRON", raising=False)
    monkeypatch.setattr("companion.platform.outbox.subprocess.run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr="bridge unavailable"))
    result = companion.dispatch_outbox(limit=1)
    assert result["results"][0]["sent"] is False
    assert companion.delivery.get(record["id"])["status"] == "retry"
