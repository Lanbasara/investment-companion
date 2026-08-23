from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from companion.core import Companion, CompanionError
from companion.db import SCHEMA, SCHEMA_VERSION, V3_SCHEMA
from companion.governance import G1_REQUIRED_TUSHARE_CAPABILITIES, GATE_CHECKLISTS, REPORT_VALIDATORS
from companion.quant_runtime import canonical_hash
from companion.timeutil import iso, utc_now
from companion.tushare_adapter import TushareAdapter
from companion.v4_data import DatasetSnapshotManifest, SnapshotPartition


def new_companion(tmp_path: Path) -> Companion:
    companion = Companion(tmp_path, gate_scope="test_fixture")
    companion.initialize()
    return companion


def v3_database(path: Path, version: int = 3) -> None:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.executescript(V3_SCHEMA)
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (str(version),))
    con.commit();con.close()


def pass_gate(companion: Companion, gate: str) -> dict:
    artifact = companion.data.manifest_publish(
        kind="fixture_evidence",
        schema_version="fixture/v1",
        manifest={"gate": gate},
    )
    evidence = companion.gates.evidence_publish(
        gate,
        checks={key: True for key in GATE_CHECKLISTS[gate]},
        artifacts=[artifact["id"]],
        unknowns=[],
        counterevidence=[],
        counterevidence_disposition={},
        code_version="test-fixture",
        scope="test_fixture",
    )
    return companion.gates.assessment_record(
        gate=gate,
        status="go",
        evidence_manifest_id=evidence["id"],
        code_version="test-fixture",
        assessed_by="pytest",
        scope="test_fixture",
    )


def day_text(value: date) -> str:
    return value.isoformat()


def market_today() -> date:
    # Keep market fixtures safely behind the wall clock.  Using the real local
    # date made these tests cross a market-close boundary at Asia/Shanghai
    # midnight even though the production validator was behaving correctly.
    return date(2026, 1, 30)


@pytest.fixture
def stable_market_clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """Use one post-close instant across modules that import utc_now directly."""

    from companion import attention, cognition, core, data_domain, governance, jobs, operating, shadow, timeutil

    fixed = datetime(2026, 1, 30, 9, 0, tzinfo=timezone.utc)
    clock = lambda: fixed
    for module in (
        sys.modules[__name__],
        attention,
        cognition,
        core,
        data_domain,
        governance,
        jobs,
        operating,
        shadow,
        timeutil,
    ):
        monkeypatch.setattr(module, "utc_now", clock)
    return fixed


def bar(asset_id: str, day: date, close: str, cutoff: str) -> dict:
    raw_hash = hashlib.sha256(f"{asset_id}:{day}:{close}".encode()).hexdigest()
    number = float(close)
    known_at = f"{day_text(day)}T16:00:00+08:00"
    return {
        "asset_id": asset_id,
        "date": day_text(day),
        "open": close,
        "high": str(number + 0.5),
        "low": str(number - 0.5),
        "close": close,
        "volume": "100000",
        "suspended": False,
        "at_upper_limit": False,
        "at_lower_limit": False,
        "first_known_at": known_at,
        "ingested_at": known_at,
        "raw_hash": raw_hash,
        "parser_version": "fixture-bars/1",
    }


def build_snapshot(
    companion: Companion,
    assets: list[str],
    role_dates: dict[str, list[date]],
    *,
    benchmark: str,
) -> tuple[dict, dict[str, str]]:
    cutoff = iso(utc_now() - timedelta(seconds=1))
    raw = companion.data.object_put_bytes(
        b"official fixture response",
        namespace="raw",
        kind="fixture_raw_response",
    )
    partitions=[];names={}
    for role, dates in role_dates.items():
        rows=[]
        for offset, day in enumerate(dates):
            rows.extend(
                bar(asset, day, str(10 + offset + index), cutoff)
                for index, asset in enumerate(assets)
            )
        obj=companion.data.object_put_json(rows,kind="daily_partition",metadata={"role":role})
        name=f"daily/{role}"
        report=companion.data.partition_validate(
            object_id=obj["id"],partition_name=name,stream="daily",role=role,knowledge_cutoff=cutoff
        )
        body=report["manifest"]["manifest"]
        assert body["eligible"], body["violations"]
        partitions.append(
            SnapshotPartition(
                name=name,
                object_ref=companion.data._ref_from_row(obj),
                row_count=body["row_count"],
                schema_hash=body["schema_hash"],
                quality={"status":"passed","stream":"daily","role":role,"validator_manifest_id":report["id"]},
            )
        )
        names[role]=name
    all_dates=sorted({day for dates in role_dates.values() for day in dates})
    calendar_dates=[*all_dates,all_dates[-1]+timedelta(days=1)]
    calendar_obj=companion.data.object_put_json(
        [{"date":day_text(day),"exchange":"SSE","is_open":True} for day in calendar_dates],
        kind="trading_calendar",
    )
    calendar_report=companion.data.partition_validate(
        object_id=calendar_obj["id"],partition_name="calendar/sse",stream="trading_calendar",role="reference",knowledge_cutoff=cutoff
    )
    actions_obj=companion.data.object_put_json([],kind="corporate_actions")
    actions_report=companion.data.partition_validate(
        object_id=actions_obj["id"],partition_name="actions/all",stream="corporate_actions",role="reference",knowledge_cutoff=cutoff
    )
    manifest=DatasetSnapshotManifest(
        knowledge_cutoff=cutoff,
        input_objects=[companion.data._ref_from_row(raw)],
        partitions=partitions,
        denominator={"asset_ids":assets,"count":len(assets)},
        universe={"eligible":assets,"definition_version":"fixture-v1"},
        exclusions=[],
        calendar={"object_ref":companion.data._ref_from_row(calendar_obj),"validator_manifest_id":calendar_report["id"],"version":"fixture-v1"},
        corporate_actions={"object_ref":companion.data._ref_from_row(actions_obj),"validator_manifest_id":actions_report["id"],"policy":"known-at-cutoff"},
        quality={"status":"passed","blocked_partitions":[]},
        code_version={"commit":"test-fixture","parser":"fixture/1"},
        metadata={"benchmark_asset_id":benchmark},
    )
    return companion.data.snapshot_validate_and_publish(manifest.as_dict()), names


def build_research_fixture(companion: Companion):
    for gate in ("G0","G1","G2","G3"):
        pass_gate(companion,gate)
    companion.jobs.feature_set("v4_jobs",True,reason="fixture gates passed")
    companion.v4_bootstrap_jobs(activate=True)
    alpha=companion.financial.asset_upsert("equity","Alpha","CNY",{"fixture":"ALPHA"})
    benchmark=companion.financial.asset_upsert("index","Benchmark","CNY",{"fixture":"BENCH"})
    today=market_today()
    role_dates={
        "development":[today-timedelta(days=10),today-timedelta(days=9),today-timedelta(days=8)],
        "validation":[today-timedelta(days=7),today-timedelta(days=6),today-timedelta(days=5),today-timedelta(days=4)],
        "holdout":[today-timedelta(days=3),today-timedelta(days=2),today-timedelta(days=1),today],
        "production":[today-timedelta(days=21)+timedelta(days=index) for index in range(21)],
    }
    snapshot,names=build_snapshot(companion,[alpha["id"],benchmark["id"]],role_dates,benchmark=benchmark["id"])
    hypothesis=companion.research.hypothesis_create(
        name="transparent momentum fixture",
        experiment_budget=3,
        spec={
            "economic_logic":"cross-sectional persistence",
            "falsifiers":["negative holdout excess return"],
            "universe":"frozen snapshot",
            "data_requirements":["daily"],
            "benchmark":"equal weight",
            "stop_conditions":["budget exhausted"],
        },
    )
    split={"partitions":{"development":[names["development"]],"validation":[names["validation"]],"final_holdout":[names["holdout"]]}}
    reality={"version":"a-share-reality/v1","currency":"CNY","lot_size":100,"t_plus_one":True,"signal_delay":"next_session","commission_rate":"0.0003","minimum_commission":"5","sell_stamp_duty_rate":"0.0005","cash_dividend_tax_rate":"0","slippage_bps":"5","money_quantum":"0.01","price_tick":"0.01"}
    candidate_config={
        "strategy":{"method":"cross_sectional_momentum","lookback_sessions":1,"top_k":1,"rebalance_every_sessions":1,"cash_weight":"0"},
        "simulation":{"initial_cash":"100000","initial_positions":{},"reality_spec":reality},
    }
    benchmark_config={
        "strategy":{"method":"equal_weight","rebalance_every_sessions":1,"cash_weight":"0"},
        "simulation":{"initial_cash":"100000","initial_positions":{},"reality_spec":reality},
    }
    strategy=companion.research.strategy_register(
        hypothesis_id=hypothesis["id"],
        code_ref="test-fixture",
        environment_ref="native-runtime-fixture",
        spec={
            "method":"cross_sectional_momentum","universe":"snapshot","features":["close_momentum"],"label":"next_session_return",
            "availability_lag":"next_session","portfolio":{"top_k":1,"rebalance_every_sessions":1},"costs":{"reality_spec":reality},
            "benchmark":{"method":"equal_weight","asset_id":benchmark["id"]},"candidate_config":candidate_config,"benchmark_config":benchmark_config,"split":split,
            "evaluation_plan":[{"phase":"development","seed":7},{"phase":"validation","seed":7},{"phase":"final_holdout","seed":7}],
            "leakage_controls":{"feature_lag_sessions":1,"purge_sessions":0,"embargo_sessions":0,"point_in_time_universe":True,"labels_excluded_from_features":True},
            "metrics":["net_excess_return","max_drawdown","turnover","total_fees","unfilled_rate","observation_days"],
            "pass_fail":{"rules":[
                {"metric":"net_excess_return","operator":"gt","value":0},
                {"metric":"max_drawdown","operator":"gte","value":"-0.2"},
                {"metric":"turnover","operator":"lte","value":"2"},
                {"metric":"unfilled_rate","operator":"lte","value":"0.2"},
                {"metric":"observation_days","operator":"gte","value":1},
            ]},
            "max_experiments":3,"minimum_experiments":3,"required_evaluations":["development","validation","final_holdout"],"stop_conditions":["immutable three-phase plan"],
        },
    )
    completed_by_phase={};experiment_by_phase={}
    for phase,role in (("development","development"),("validation","validation"),("final_holdout","holdout")):
        experiment=companion.research.experiment_start(
            strategy_version_id=strategy["id"],dataset_snapshot_id=snapshot["id"],split=split,
            params={"evaluation_phase":phase},seed=7,
        )
        spec={
            "schema":"investment-companion.native-experiment-spec/v3","evaluation_phase":phase,
            "partition_names":[names[role]],
            "candidate":candidate_config,
            "benchmark":benchmark_config,
        }
        if phase=="development":
            tampered=json.loads(json.dumps(spec));tampered["candidate"]["strategy"]["top_k"]=2;tampered["candidate"]["simulation"]["reality_spec"]["slippage_bps"]="999"
            with pytest.raises(CompanionError,match="differs from immutable StrategyVersion"):
                companion.research.experiment_submit(experiment["id"],tampered)
        job=companion.research.experiment_submit(experiment["id"],spec)
        completed=companion.jobs.run_once(f"pytest-worker-{phase}",lease_seconds=30)
        assert completed and completed["id"]==job["id"] and completed["status"]=="succeeded", completed
        completed_by_phase[phase]=completed;experiment_by_phase[phase]=companion.research.experiment_get(experiment["id"])
    return {"alpha":alpha,"benchmark":benchmark,"snapshot":snapshot,"names":names,"strategy":strategy,"experiment":experiment_by_phase["final_holdout"],"job":completed_by_phase["final_holdout"],"experiments":experiment_by_phase,"jobs":completed_by_phase}


def enter_shadow_with_forward(companion:Companion,fixture:dict,*,name:str)->dict:
    companion.research.promotion_decide(fixture["experiment"]["id"],"research_passed","fixture thresholds passed")
    pass_gate(companion,"G4");companion.jobs.feature_set("v4_shadow",True,reason="fixture G4")
    companion.research.promotion_decide(fixture["experiment"]["id"],"shadow","fixture enters forward shadow")
    book=companion.shadow.book_create(
        strategy_version_id=fixture["strategy"]["id"],name=name,initial_cash="100000",
        reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],
        sample_gate={"minimum_days":90,"minimum_rebalances":12,"minimum_decisions":5,"minimum_market_regimes":2},
    )
    forward=companion.research.forward_signal_start(
        strategy_version_id=fixture["strategy"]["id"],dataset_snapshot_id=fixture["snapshot"]["id"],
        partition_names=[fixture["names"]["production"]],
    )
    spec={
        "schema":"investment-companion.native-experiment-spec/v3","evaluation_phase":"forward_shadow",
        "partition_names":[fixture["names"]["production"]],
        "candidate":fixture["strategy"]["spec"]["candidate_config"],
        "benchmark":fixture["strategy"]["spec"]["benchmark_config"],
    }
    job=companion.research.experiment_submit(forward["id"],spec)
    completed=companion.jobs.run_once("pytest-forward-worker",lease_seconds=30)
    assert completed and completed["id"]==job["id"] and completed["status"]=="succeeded"
    experiment=companion.research.experiment_get(forward["id"])
    bundle=companion.data.manifest_get(experiment["bundle_manifest_id"])["manifest"]["manifest"]
    target_manifest=next(companion.data.manifest_get(ref) for ref in completed["steps"][-1]["output_refs"] if ref.startswith("manifest_") and companion.data.manifest_get(ref)["kind"]=="target_weights")
    execution_snapshot,_=build_snapshot(companion,[fixture["alpha"]["id"],fixture["benchmark"]["id"]],{"production":[market_today()]},benchmark=fixture["benchmark"]["id"])
    return {"book":book,"experiment":experiment,"job":completed,"bundle":bundle,"target_manifest":target_manifest,"execution_snapshot":execution_snapshot}


def test_latest_schema_requires_explicit_migration_and_is_repeatable(tmp_path: Path):
    root=tmp_path/"workspace";root.mkdir();db=root/".state"/"companion.db";db.parent.mkdir()
    v3_database(db)
    companion=Companion(root,gate_scope="test_fixture")
    with pytest.raises(RuntimeError,match="requires explicit migration"):
        companion.initialize()
    con=sqlite3.connect(db)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=="3"
    assert con.execute("SELECT count(*) FROM sqlite_master WHERE name='schema_migrations'").fetchone()[0]==0
    con.close()
    result=companion.migrate(tmp_path/"backups")
    assert result["schema_version"]==str(SCHEMA_VERSION) and result["integrity"]=="ok"
    assert Path(result["from_backup"]).is_file()
    companion.initialize()
    assert len(companion.system_status()["migrations"])==5


def test_doctor_survives_system_operations_extraction(tmp_path: Path):
    root = tmp_path / "workspace"
    companion = new_companion(root)

    result = companion.doctor()

    assert result["checks"]["database_integrity"] is True
    assert result["checks"]["workspace_writable"] is True

    (root / ".codex" / "agents").mkdir(parents=True)
    (root / ".codex" / "config.toml").write_text("", encoding="utf-8")
    configured_result = companion.doctor()
    assert configured_result["checks"]["custom_agent_config"] is False


def test_failed_migration_rolls_back_schema_changes(tmp_path: Path):
    root=tmp_path/"workspace";root.mkdir();db=root/".state"/"companion.db";db.parent.mkdir();v3_database(db)
    con=sqlite3.connect(db);con.execute("CREATE TABLE feature_flags(key TEXT PRIMARY KEY)");con.commit();con.close()
    companion=Companion(root,gate_scope="test_fixture")
    with pytest.raises(CompanionError,match="migration failed"):
        companion.migrate(tmp_path/"backups")
    con=sqlite3.connect(db)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0]=="3"
    columns={row[1] for row in con.execute("PRAGMA table_info(schedules)")}
    assert "dispatch_type" not in columns
    assert con.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    con.close()


def test_fixture_scope_cannot_target_worktree_or_external_state(tmp_path: Path,monkeypatch):
    worktree=tmp_path/"worktree";worktree.mkdir();(worktree/".git").mkdir()
    with pytest.raises(CompanionError,match="cannot target a Git worktree"):
        Companion(worktree,gate_scope="test_fixture")
    isolated=tmp_path/"isolated";isolated.mkdir()
    with pytest.raises(CompanionError,match="database must remain inside"):
        Companion(isolated,db_path=tmp_path/"outside.db",gate_scope="test_fixture")
    monkeypatch.setenv("COMPANION_DATA_ROOT",str(tmp_path/"outside-data"))
    with pytest.raises(CompanionError,match="data root must remain inside"):
        Companion(isolated,gate_scope="test_fixture")


def test_partition_semantics_and_snapshot_fail_closed(tmp_path: Path):
    companion=new_companion(tmp_path)
    bad=companion.data.object_put_json({"bad_data":True},kind="daily_partition")
    report=companion.data.partition_validate(object_id=bad["id"],partition_name="daily/bad",stream="daily",role="development",knowledge_cutoff=iso())
    assert report["manifest"]["manifest"]["eligible"] is False
    with pytest.raises(CompanionError):
        companion.data.capability_record(provider="fake",capability="daily",connector="fake",status="healthy")
    with companion.db.transaction() as con:con.execute("UPDATE data_objects SET status='quarantined' WHERE id=?",(bad["id"],))
    with pytest.raises(CompanionError,match="not ready"):
        companion.data.object_read(bad["id"])
    snapshot,_=build_snapshot(companion,["asset-fixture"],{"development":[market_today()-timedelta(days=1)]},benchmark="asset-fixture")
    with companion.db.transaction() as con:con.execute("UPDATE dataset_snapshots SET status='invalid' WHERE id=?",(snapshot["id"],))
    with pytest.raises(CompanionError,match="not consumable"):
        companion.data.snapshot_manifest(snapshot["id"])


def test_corporate_action_partition_accepts_only_replayable_semantics(tmp_path:Path):
    companion=new_companion(tmp_path);known=iso();raw_hash=hashlib.sha256(b"official-action").hexdigest()
    base={"entity_key":"asset-A","fact_key":"dividend-2026","effective_at":"2026-01-02T00:00:00+08:00","effective_to":None,"first_known_at":known,"ingested_at":known,"revision_id":"action-r1","supersedes":None,"raw_hash":raw_hash,"parser_version":"fixture-action/1","quality":{"status":"passed"}}
    valid=companion.data.object_put_json([{**base,"value":{"action_type":"cash_dividend","asset_id":"asset-A","ex_date":"2026-01-02","cash_per_share":"0.5"}}],kind="corporate_actions")
    report=companion.data.partition_validate(object_id=valid["id"],partition_name="actions/valid",stream="corporate_actions",role="reference",knowledge_cutoff=known)
    assert report["manifest"]["manifest"]["eligible"] is True
    invalid=companion.data.object_put_json([{**base,"revision_id":"action-r2","value":{"action_type":"mystery","asset_id":"asset-A","ex_date":"2026-01-02"}}],kind="corporate_actions")
    rejected=companion.data.partition_validate(object_id=invalid["id"],partition_name="actions/invalid",stream="corporate_actions",role="reference",knowledge_cutoff=known)
    assert rejected["manifest"]["manifest"]["eligible"] is False


def test_gate_validation_reports_are_typed_and_do_not_self_grant_go(tmp_path:Path):
    companion=new_companion(tmp_path)
    assert "daily_basic" in G1_REQUIRED_TUSHARE_CAPABILITIES and len(G1_REQUIRED_TUSHARE_CAPABILITIES)==11
    source=companion.data.manifest_publish(kind="verification_input",schema_version="fixture/v1",manifest={"suite":"v4"})
    checks={key:True for key in REPORT_VALIDATORS["test_report"][1]}
    report=companion.gates.validation_report_publish(kind="test_report",checks=checks,input_refs=[source["id"]],commands=["python3 -m pytest -q"],observations={"passed":58},scope="test_fixture")
    assert report["manifest"]["manifest"]["validator_status"]=="passed"
    assert companion.gates.latest("G0","test_fixture") is None
    with pytest.raises(CompanionError,match="checks must be exactly"):
        companion.gates.validation_report_publish(kind="test_report",checks={"zero_failures":True},input_refs=[source["id"]],commands=["pytest"],observations={"passed":1},scope="test_fixture")


def test_atomic_deterministic_experiment_and_direct_completion_rejected(tmp_path: Path, stable_market_clock: datetime):
    companion=new_companion(tmp_path)
    fixture=build_research_fixture(companion)
    experiment=fixture["experiment"];job=fixture["job"]
    assert experiment["status"]=="succeeded"
    assert experiment["job_run_id"]==job["id"]
    parent=companion.run_get(job["parent_run_id"])
    assert parent["status"]=="succeeded"
    assert all(step["status"]=="succeeded" for step in job["steps"])
    assert job["steps"][-1]["resource_usage"]["isolated_process"] is True
    assert job["steps"][-1]["resource_usage"]["model_tokens"]==0
    bundle=companion.data.manifest_get(experiment["bundle_manifest_id"])["manifest"]["manifest"]
    targets=bundle["candidate_bundle"]["experiment_result"]["target_portfolios"]
    assert len(targets)>=2 and targets[-1]==bundle["candidate_bundle"]["experiment_result"]["target_portfolio"]
    assert bundle["evaluation_pairs"]==bundle["candidate_bundle"]["experiment_result"]["evaluation_pairs"]
    assert bundle["corporate_action_scope"]["denominator_hash"]==bundle["denominator_hash"]
    validation=fixture["experiments"]["validation"]
    original_metrics=validation["metrics"]
    failed_metrics={**original_metrics,"net_excess_return":"-0.01"}
    with companion.db.transaction() as con:
        con.execute("UPDATE experiment_runs SET metrics_json=? WHERE id=?",(json.dumps(failed_metrics,sort_keys=True,separators=(",",":")),validation["id"]))
    with pytest.raises(CompanionError,match="validation: net_excess_return"):
        companion.research.promotion_decide(experiment["id"],"research_passed","must reject failed validation")
    with companion.db.transaction() as con:
        con.execute("UPDATE experiment_runs SET metrics_json=? WHERE id=?",(json.dumps(original_metrics,sort_keys=True,separators=(",",":")),validation["id"]))
    with pytest.raises(CompanionError,match="direct completion is forbidden"):
        companion.research.experiment_complete(experiment["id"],success=True,bundle_manifest_id=experiment["bundle_manifest_id"],job_run_id=job["id"])


def test_forward_signal_is_untuned_and_targets_next_frozen_session(tmp_path: Path, stable_market_clock: datetime):
    companion=new_companion(tmp_path);fixture=build_research_fixture(companion)
    forward=enter_shadow_with_forward(companion,fixture,name="operational fixture")
    bundle=forward["bundle"]
    pair=bundle["evaluation_pairs"][0]
    assert bundle["evaluation_phase"]=="forward_shadow"
    assert pair=={"as_of":day_text(market_today()-timedelta(days=1)),"effective_on":day_text(market_today())}
    assert bundle["candidate_bundle"]["experiment_result"]["target_portfolio"]["effective_on"]==pair["effective_on"]
    assert bundle["model_tokens"]==0


def test_shadow_is_continuous_forward_only_and_real_ledger_isolated(tmp_path: Path, stable_market_clock: datetime):
    companion=new_companion(tmp_path);fixture=build_research_fixture(companion)
    forward=enter_shadow_with_forward(companion,fixture,name="forward fixture");book=forward["book"]
    bundle=forward["bundle"]
    target_hash=bundle["candidate_bundle"]["experiment_result"]["target_portfolio"]["artifact_hash"]
    target_manifest=forward["target_manifest"]
    ledger_before=len(companion.financial.ledger_list())
    rebalance=companion.shadow.rebalance_record(
        book_id=book["id"],signal_snapshot_id=fixture["snapshot"]["id"],execution_snapshot_id=forward["execution_snapshot"]["id"],experiment_run_id=forward["experiment"]["id"],
        as_of=day_text(market_today()),target_manifest_id=target_manifest["id"],denominator_hash=fixture["snapshot"]["denominator_hash"],
    )
    assert rebalance["result"]["simulation_hash"]
    assert rebalance["result"]["simulation"]["target_hashes"]==[target_hash]
    assert len(companion.financial.ledger_list())==ledger_before
    with companion.db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM shadow_metrics WHERE book_id=?",(book["id"],)).fetchone()[0]==1
    with pytest.raises(CompanionError,match="advance monotonically"):
        companion.shadow.rebalance_record(book_id=book["id"],signal_snapshot_id=fixture["snapshot"]["id"],execution_snapshot_id=forward["execution_snapshot"]["id"],experiment_run_id=forward["experiment"]["id"],as_of=day_text(market_today()),target_manifest_id=target_manifest["id"],denominator_hash=fixture["snapshot"]["denominator_hash"])
    sample=companion.shadow.sample_status(book["id"])
    assert sample["status"]=="insufficient_evidence" and sample["checks"]["state_continuity"] is True
    with companion.db.transaction() as con:con.execute("UPDATE shadow_books SET created_at=? WHERE id=?",(iso(utc_now()-timedelta(days=120)),book["id"]))
    assert companion.shadow.sample_status(book["id"])["observed"]["days"]==1


def test_manual_action_revalidates_and_fill_matches_frozen_base_ledger(tmp_path: Path,monkeypatch,stable_market_clock: datetime):
    companion=new_companion(tmp_path);fixture=build_research_fixture(companion)
    forward=enter_shadow_with_forward(companion,fixture,name="decision fixture");book=forward["book"]
    pass_gate(companion,"G5")
    companion.jobs.feature_set("v4_decision_support",True,reason="fixture G5")
    account=companion.financial.account_create("Manual account","CNY")
    now=utc_now();now_text=iso(now)
    cash=companion.financial.ledger_add(account_id=account["id"],entry_type="cash_deposit",occurred_at=iso(now-timedelta(minutes=5)),amount="100000",currency="CNY",source="fixture")
    companion.financial.ledger_confirm(cash["id"])
    contexts={}
    for kind,content in (
        ("investor",{"horizon":"long"}),
        ("mandate",{"minimum_cash":{"CNY":"10000"}}),
        ("attention",{"daily_notification_budget":3,"topic_cooldown_seconds":0}),
    ):
        contexts[kind]=companion.cognition.context_confirm(companion.cognition.context_create(kind,content)["id"])
    market=companion.financial.market_add(fixture["alpha"]["id"],"close","10",now_text,"fixture","healthy","CNY")
    plan=companion.financial.portfolio_rebalance_plan(as_of=now_text,account_id=account["id"],target_manifest_id=forward["target_manifest"]["id"],market_snapshot_ids=[market["id"]],reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],mandate=contexts["mandate"]["content"])
    assert plan["status"]=="feasible" and len(plan["actions"])==1
    malformed_target=dict(forward["target_manifest"]["manifest"]["manifest"]);malformed_target.pop("artifact_hash")
    malformed_target["weights"]=[*malformed_target["weights"],dict(malformed_target["weights"][0])]
    malformed_target["artifact_hash"]=canonical_hash(malformed_target)
    malformed_manifest=companion.data.manifest_publish(kind="target_weights",schema_version=malformed_target["schema"],manifest=malformed_target,_internal=True)
    with pytest.raises(CompanionError,match="duplicate target weight"):
        companion.financial.portfolio_rebalance_plan(as_of=now_text,account_id=account["id"],target_manifest_id=malformed_manifest["id"],market_snapshot_ids=[market["id"]],reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],mandate=contexts["mandate"]["content"])
    blocked_plan=companion.financial.portfolio_rebalance_plan(as_of=now_text,account_id=account["id"],target_manifest_id=forward["target_manifest"]["id"],market_snapshot_ids=[market["id"]],reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],mandate={**contexts["mandate"]["content"],"max_turnover":"0"})
    assert blocked_plan["status"]=="infeasible" and blocked_plan["actions"]==[] and blocked_plan["conflicts"][0]["rule"]=="max_turnover"
    with companion.db.transaction() as con:con.execute("UPDATE calculations SET outputs_json=? WHERE id=?",('{}',blocked_plan["calculation_id"]))
    with pytest.raises(CompanionError,match="reproducibility hash mismatch"):
        companion.financial.calculation_get(blocked_plan["calculation_id"])
    with pytest.raises(CompanionError,match="unsupported Mandate hard constraints"):
        companion.financial.portfolio_rebalance_plan(as_of=now_text,account_id=account["id"],target_manifest_id=forward["target_manifest"]["id"],market_snapshot_ids=[market["id"]],reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],mandate={"hard_constraints":{"max_sector_weight":"0.2"}})
    with pytest.raises(CompanionError,match="currency-to-amount object"):
        companion.financial.portfolio_rebalance_plan(as_of=now_text,account_id=account["id"],target_manifest_id=forward["target_manifest"]["id"],market_snapshot_ids=[market["id"]],reality_spec=fixture["strategy"]["spec"]["costs"]["reality_spec"],mandate={"minimum_cash":"10000"})
    planned=plan["actions"][0];quantity=planned["quantity"]
    execution_price=planned["execution_price"]
    fee=Decimal(planned["commission"])+Decimal(planned["tax"])
    portfolio=plan["before"]
    constraint=companion.financial.trade_impact(now_text,account["id"],fixture["alpha"]["id"],quantity,execution_price,str(fee),contexts["mandate"]["content"])
    thesis=companion.cognition.object_create("thesis",{"asset_id":fixture["alpha"]["id"]})
    thesis_revision=companion.cognition.publish(thesis["id"],"Fixture thesis")
    attention=companion.attention.decide(topic="fixture-action",materiality="high",confidence="high",reason="manual action fixture")
    frozen_sources=[fixture["snapshot"]["manifest_id"],fixture["experiment"]["bundle_manifest_id"],forward["experiment"]["bundle_manifest_id"],forward["target_manifest"]["id"]]
    critic_inputs=[*frozen_sources,portfolio["calculation_id"],plan["calculation_id"],constraint["calculation_id"],contexts["investor"]["id"],contexts["mandate"]["id"],thesis_revision["id"]]
    critic_args={"invocation_ref":"fixture-thesis-critic-call-1","role":"thesis_critic","model":"fixture-model","prompt_template":"thesis-critic/v1","input_refs":critic_inputs,"review":{"challenges":["small fixture"],"resolution":"test only"},"token_usage":{"input_tokens":10,"output_tokens":5,"total_tokens":15},"started_at":iso(now-timedelta(minutes=1)),"finished_at":now_text,"adopted":True,"adoption_reason":"fixture verifies provenance contract"}
    critic=companion.cognition.agent_review_record(**critic_args)
    assert companion.cognition.agent_review_record(**critic_args)["id"]==critic["id"]
    with pytest.raises(CompanionError,match="different immutable provenance"):
        companion.cognition.agent_review_record(**{**critic_args,"review":{"challenges":["different"],"resolution":"must not overwrite"}})
    decision=companion.cognition.object_create("decision",{"asset_id":fixture["alpha"]["id"]})
    decision_refs={
        "investor_revision_id":contexts["investor"]["id"],"mandate_revision_id":contexts["mandate"]["id"],
        "portfolio_calculation_id":portfolio["calculation_id"],"thesis_revision_ids":[thesis_revision["id"]],
        "dataset_snapshot_id":fixture["snapshot"]["id"],"strategy_version_id":fixture["strategy"]["id"],
        "experiment_run_id":forward["experiment"]["id"],"research_experiment_run_id":fixture["experiment"]["id"],
        "target_manifest_id":forward["target_manifest"]["id"],"portfolio_plan_calculation_id":plan["calculation_id"],"constraint_calculation_id":constraint["calculation_id"],
        "attention_decision_id":attention["id"],
    }
    decision_metadata={
        "decision_contract_version":4,"no_action":{"alternative":"hold cash"},"invalidators":["price above 11"],
        "source_refs":[*frozen_sources,critic["output_manifest_id"]],
        "critic_review":{"invocation_id":critic["id"]},
        "decision_mode":"beta","user_opt_in_ref":"fixture-user-opt-in","shadow_book_id":book["id"],
        "valid_until":iso(now+timedelta(hours=1)),"confirmed_ledger_hash":companion.financial.confirmed_ledger_hash(),
    }
    with pytest.raises(CompanionError,match="G6=missing"):
        companion.cognition.publish(
            decision["id"],"Premature formal Decision",knowledge_cutoff=fixture["snapshot"]["knowledge_cutoff"],
            context_refs=decision_refs,calculation_ids=[portfolio["calculation_id"],plan["calculation_id"],constraint["calculation_id"]],
            metadata={**decision_metadata,"decision_mode":"strategy_eligible"},
        )
    decision_revision=companion.cognition.publish(
        decision["id"],"Buy only after manual confirmation",knowledge_cutoff=fixture["snapshot"]["knowledge_cutoff"],
        context_refs=decision_refs,
        calculation_ids=[portfolio["calculation_id"],plan["calculation_id"],constraint["calculation_id"]],
        metadata=decision_metadata,
    )
    action_spec={
        "account_id":account["id"],"asset_id":fixture["alpha"]["id"],"side":"buy","quantity":quantity,
        "lot_size":100,
        "quote_at":now_text,"valid_until":iso(now+timedelta(minutes=30)),"max_quote_age_seconds":600,
        "price_range":{"min":"9.5","max":"10.5"},"priority":"normal","alternatives":["do nothing"],
        "source_refs":[*frozen_sources,critic["output_manifest_id"]],
        "revalidate_if":["decision_revision_current","valid_until","mandate_revision_current","investor_revision_current","confirmed_ledger_unchanged","fresh_market_snapshot","price_range"],
        "notification_key":"fixture-manual-action-1",
    }
    bad_lot={**action_spec,"quantity":"50","notification_key":"fixture-bad-lot"}
    with pytest.raises(CompanionError,match="whole board lots"):
        companion.cognition.manual_action_create(decision_revision["id"],bad_lot)
    bad_plan={**action_spec,"quantity":str(Decimal(quantity)-Decimal("100")),"notification_key":"fixture-bad-plan"}
    with pytest.raises(CompanionError,match="frozen portfolio plan"):
        companion.cognition.manual_action_create(decision_revision["id"],bad_plan)
    bad_source={**action_spec,"source_refs":["https://unfrozen.example"],"notification_key":"fixture-bad-source"}
    with pytest.raises(CompanionError,match="only verified Manifest or DataObject"):
        companion.cognition.manual_action_create(decision_revision["id"],bad_source)
    action=companion.cognition.manual_action_create(decision_revision["id"],action_spec)
    with pytest.raises(CompanionError,match="must use current time"):
        companion.cognition.manual_action_validate(action["id"],iso(now-timedelta(minutes=10)))
    with pytest.raises(CompanionError,match="must use current time"):
        companion.cognition.manual_action_validate(action["id"],iso(now+timedelta(minutes=10)))
    assert companion.cognition.manual_action_validate(action["id"])["executable"] is True
    execution=companion.cognition.execution_create_from_action(action["id"],"fixture-execution-1")
    assert companion.cognition.execution_create_from_action(action["id"],"fixture-execution-1")["id"]==execution["id"]
    with pytest.raises(CompanionError,match="different immutable inputs"):
        companion.cognition.execution_create(decision["id"],{"tampered":True},decision_revision_id=decision_revision["id"],manual_action_spec_hash=action["content_hash"],idempotency_key="fixture-execution-1",status="presented")
    original_validate=companion.cognition.manual_action_validate
    monkeypatch.setattr(companion.cognition,"manual_action_validate",lambda _spec_id,_as_of=None:{"spec":action,"executable":False,"reasons":["fixture_stale"]})
    with pytest.raises(CompanionError,match="currently executable"):
        companion.cognition.execution_set_status(execution["id"],"accepted")
    monkeypatch.setattr(companion.cognition,"manual_action_validate",original_validate)
    execution=companion.cognition.execution_set_status(execution["id"],"accepted")
    assert companion.cognition.manual_action_get(action["id"])["status"]=="accepted"
    execution=companion.cognition.execution_set_status(execution["id"],"ordered")
    with pytest.raises(CompanionError,match="amount must equal"):
        companion.financial.ledger_add(account_id=account["id"],entry_type="trade",asset_id=fixture["alpha"]["id"],occurred_at=iso(now+timedelta(minutes=1)),quantity=quantity,price="10",amount="-1",currency="CNY",source="invalid-fixture")
    actual_gross=Decimal(quantity)*Decimal("10")
    actual_fee=max(Decimal("5"),actual_gross*Decimal("0.0003")).quantize(Decimal("0.01"))
    trade=companion.financial.ledger_add(account_id=account["id"],entry_type="trade",asset_id=fixture["alpha"]["id"],occurred_at=iso(now+timedelta(minutes=1)),quantity=quantity,price="10",amount=str(-actual_gross),fee=str(actual_fee),currency="CNY",source="manual-fixture")
    companion.financial.ledger_confirm(trade["id"])
    with pytest.raises(CompanionError,match="unique list"):
        companion.cognition.execution_set_status(execution["id"],"filled",[trade["id"],trade["id"]])
    execution=companion.cognition.execution_set_status(execution["id"],"filled",[trade["id"]])
    assert execution["status"]=="filled" and execution["ledger_entry_ids"]==[trade["id"]]
    unrelated=companion.financial.ledger_add(account_id=account["id"],entry_type="cash_deposit",occurred_at=iso(now+timedelta(minutes=2)),amount="1",currency="CNY",source="fixture-change")
    companion.financial.ledger_confirm(unrelated["id"])
    with pytest.raises(CompanionError,match="different ledger entries"):
        companion.cognition.execution_set_status(execution["id"],"filled",[unrelated["id"]])
    validation=companion.cognition.manual_action_validate(action["id"])
    assert "confirmed_ledger_changed" in validation["reasons"]


def test_tushare_canary_retains_raw_and_semantically_validates(tmp_path: Path,monkeypatch):
    companion=new_companion(tmp_path)
    pass_gate(companion,"G0");companion.jobs.feature_set("v4_live_data_canary",True,reason="fixture canary")
    companion.data.stream_configure(provider="tushare",capability="daily",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    responses={
        "daily":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","open","high","low","close","vol"],"items":[["000001.SZ","20260817",10,11,9,10.5,1000]]}},
        "fund_daily":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","open","high","low","close","vol","amount"],"items":[["510300.SH","20260817",4,4.1,3.9,4.05,2000,8100]]}},
        "fund_nav":{"code":0,"msg":None,"data":{"fields":["ts_code","ann_date","nav_date","unit_nav","accum_nav","accum_div","net_asset","adj_nav","update_flag"],"items":[["510300.SH","20260818","20260817",4.05,4.05,0,1000000,4.05,"0"]]}},
        "fund_share":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","fd_share","fund_type","market"],"items":[["510300.SH","20260817",250000,"股票型","E"]]}},
        "etf_basic":{"code":0,"msg":None,"data":{"fields":["ts_code","csname","extname","cname","index_code","index_name","setup_date","list_date","list_status","exchange","mgr_name","custod_name","mgt_fee","etf_type"],"items":[["510300.SH","沪深300ETF","沪深300ETF","沪深300ETF","000300.SH","沪深300","20120504","20120528","L","SSE","fixture manager","fixture custodian",0.15,"股票型"]]}},
        "dividend":{"code":0,"msg":None,"data":{"fields":["ts_code","end_date","ann_date","div_proc","stk_div","cash_div_tax","ex_date"],"items":[["000001.SZ","20251231","20260301","实施",0.2,0.5,"20260601"]]}},
    }
    seen={}
    def transport(body:bytes,_timeout:float)->bytes:
        request=json.loads(body.decode());seen.update(request)
        return json.dumps(responses[request["api_name"]]).encode()
    adapter=TushareAdapter(companion,token="fixture-token-123",transport=transport)
    batch=adapter.ingest_canary("daily",params={"trade_date":"20260817"})
    assert batch["status"]=="ready" and batch["row_count"]==1
    assert seen["token"]=="fixture-token-123"
    raw=companion.data.object_read(batch["raw_object_ids"][0]).decode()
    assert "fixture-token-123" not in raw
    canonical=json.loads(companion.data.object_read(batch["canonical_object_ids"][0]))
    assert canonical[0]["asset_id"]=="tushare:000001.SZ" and canonical[0]["parser_version"]=="tushare-daily/2"
    companion.data.stream_configure(provider="tushare",capability="fund_daily",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    fund_batch=adapter.ingest_canary("fund_daily",params={"ts_code":"510300.SH","start_date":"20260817","end_date":"20260817"})
    fund_rows=json.loads(companion.data.object_read(fund_batch["canonical_object_ids"][0]))
    assert fund_batch["status"]=="ready" and fund_batch["row_count"]==1
    assert fund_rows[0]["asset_id"]=="tushare:510300.SH" and fund_rows[0]["amount"]=="8100" and fund_rows[0]["parser_version"]=="tushare-fund-daily/1"
    companion.data.stream_configure(provider="tushare",capability="fund_nav",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    nav_batch=adapter.ingest_canary("fund_nav",params={"ts_code":"510300.SH","start_date":"20260817","end_date":"20260817"})
    nav=json.loads(companion.data.object_read(nav_batch["canonical_object_ids"][0]))
    assert nav_batch["status"]=="ready" and nav["stream"]=="fund_nav" and nav["facts"][0]["value"]["unit_nav"]==4.05
    companion.data.stream_configure(provider="tushare",capability="fund_share",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    share_batch=adapter.ingest_canary("fund_share",params={"ts_code":"510300.SH","start_date":"20260817","end_date":"20260817"})
    shares=json.loads(companion.data.object_read(share_batch["canonical_object_ids"][0]))
    assert share_batch["status"]=="ready" and shares["stream"]=="fund_share" and shares["facts"][0]["value"]["fd_share"]==250000
    companion.data.stream_configure(provider="tushare",capability="etf_basic",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    universe_batch=adapter.ingest_canary("etf_basic",params={})
    universe=json.loads(companion.data.object_read(universe_batch["canonical_object_ids"][0]))
    assert universe_batch["status"]=="ready" and universe_batch["row_count"]==1
    assert universe["stream"]=="etf_basic" and universe["facts"][0]["value"]["csname"]=="沪深300ETF"
    companion.data.stream_configure(provider="tushare",capability="dividend",schema_version="tushare-normalizer/2",config={"mode":"canary"})
    dividend_batch=adapter.ingest_canary("dividend",params={"ts_code":"000001.SZ"})
    actions=json.loads(companion.data.object_read(dividend_batch["canonical_object_ids"][0]))
    assert dividend_batch["status"]=="ready" and dividend_batch["row_count"]==2
    assert [item["value"]["action_type"] for item in actions]==["cash_dividend","split"]
    assert actions[0]["value"]["cash_per_share"]=="0.5" and actions[1]["value"]["split_ratio"]=="1.2"
    echoing=TushareAdapter(companion,token="fixture-token-echo",transport=lambda _body,_timeout:b'{"token":"fixture-token-echo"}')
    with pytest.raises(CompanionError,match="echoed the credential"):
        echoing.fetch("daily",params={"trade_date":"20260817"})
    with pytest.raises(CompanionError,match="credential-like field"):
        adapter.fetch("daily",params={"access_token":"must-not-be-persisted"})
    weak_token=tmp_path/"weak-tushare-token";weak_token.write_text("fixture-token-file",encoding="utf-8");weak_token.chmod(0o644)
    with pytest.raises(CompanionError,match="group or other users"):
        TushareAdapter(companion,token_file=weak_token,transport=transport)
    secure_token=tmp_path/"secure-tushare-token";secure_token.write_text("fixture-file-token",encoding="utf-8");secure_token.chmod(0o600)
    monkeypatch.setenv("TUSHARE_TOKEN","wrong-environment-token")
    assert TushareAdapter(companion,token_file=secure_token,transport=transport)._token=="fixture-file-token"
    malformed=TushareAdapter(companion,token="fixture-malformed-token",transport=lambda _body,_timeout:b"not-json")
    malformed_record=malformed.probe("daily",params={"trade_date":"20260817"})
    assert malformed_record["status"]=="failed"
    malformed_evidence=malformed_record["evidence"]
    assert companion.data.object_read(malformed_evidence["raw_object_id"])==b"not-json"
    malformed_code_raw=b'{"code":"not-an-integer","msg":"broken","data":null}'
    malformed_code=TushareAdapter(companion,token="fixture-malformed-code",transport=lambda _body,_timeout:malformed_code_raw).probe("daily",params={"trade_date":"20260817"})
    assert malformed_code["status"]=="failed" and companion.data.object_read(malformed_code["evidence"]["raw_object_id"])==malformed_code_raw
    duplicated_fields={"code":0,"msg":None,"data":{"fields":["ts_code","ts_code"],"items":[["000001.SZ","000001.SZ"]]}}
    assert TushareAdapter(companion,token="fixture-duplicated-fields",transport=lambda _body,_timeout:json.dumps(duplicated_fields).encode()).fetch("daily",params={"trade_date":"20260817"}).status=="failed"
    def offline(_body,_timeout):raise OSError("fixture-offline-token")
    offline_record=TushareAdapter(companion,token="fixture-offline-token",transport=offline).probe("daily",params={"trade_date":"20260817"})
    assert offline_record["status"]=="connector_missing"
    assert b"fixture-offline-token" not in companion.data.object_read(offline_record["evidence"]["raw_object_id"])
    status_cases={
        "unauthorized":{"code":-2001,"msg":"无权限","data":None},
        "rate_limited":{"code":-1,"msg":"每分钟最多访问一次","data":None},
        "invalid_request":{"code":-1,"msg":"参数错误","data":None},
        "partial":{"code":0,"msg":None,"data":{"fields":["ts_code"],"items":[["000001.SZ"]]}},
        "empty_valid":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","open","high","low","close","vol"],"items":[]}},
        "failed":{"code":999,"msg":"provider failure","data":None},
    }
    for expected,response in status_cases.items():
        classified=TushareAdapter(companion,token=f"fixture-{expected}-token",transport=lambda _body,_timeout,response=response:json.dumps(response).encode()).fetch("daily",params={"trade_date":"20260817"})
        assert classified.status==expected


def test_active_tushare_batches_require_gates_and_resolve_asset_identity(tmp_path:Path,monkeypatch):
    companion=new_companion(tmp_path)
    for gate in ("G0","G1"):pass_gate(companion,gate)
    companion.jobs.feature_set("v4_jobs",True,reason="fixture G0")
    companion.jobs.feature_set("v4_live_data",True,reason="fixture G1")
    responses={
        "stock_basic":{"code":0,"msg":None,"data":{"fields":["ts_code","symbol","name","list_date","list_status"],"items":[["000001.SZ","000001","平安银行","19910403","L"]]}},
        "daily":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","open","high","low","close","vol"],"items":[["000001.SZ","20260817",10,11,9,10.5,1000]]}},
        "etf_basic":{"code":0,"msg":None,"data":{"fields":["ts_code","csname","index_code","index_name","list_date","list_status","exchange","mgt_fee","etf_type"],"items":[["510300.SH","沪深300ETF","000300.SH","沪深300","20120528","L","SSE",0.15,"股票型"]]}},
        "fund_daily":{"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","open","high","low","close","vol"],"items":[["510300.SH","20260817",4,4.1,3.9,4.05,1000]]}},
    }
    def transport(body:bytes,_timeout:float)->bytes:
        request=json.loads(body.decode())
        return json.dumps(responses[request["api_name"]]).encode()
    adapter=TushareAdapter(companion,token="fixture-token-value",transport=transport)
    for capability,params in (("stock_basic",{}),("daily",{"trade_date":"20260817"}),("etf_basic",{}),("fund_daily",{"trade_date":"20260817"})):
        companion.data.stream_configure(provider="tushare",capability=capability,schema_version="tushare-normalizer/2",config={"mode":"active-fixture"})
        companion.data.stream_set_status("tushare",capability,"active")
        batch=adapter.ingest(capability,params=params)
        assert batch["status"]=="ready" and batch["raw_object_ids"] and batch["canonical_object_ids"]
    revised=adapter.ingest("daily",params={"trade_date":"20260817"},ingestion_key="overlap-window-2")
    replay=adapter.ingest("daily",params={"trade_date":"20260817"},ingestion_key="overlap-window-2")
    assert revised["status"]=="ready" and revised["id"]!=batch["id"] and replay["id"]==revised["id"]
    identity=companion.data.identity_list("tushare","000001.SZ")
    assert len(identity)==1
    daily_stream=companion.data.stream_get("tushare","daily")
    daily_batch=companion.data.batch_list(daily_stream["id"])[0]
    rows=json.loads(companion.data.object_read(daily_batch["canonical_object_ids"][0]).decode())
    assert rows[0]["asset_id"]==identity[0]["asset_id"]
    etf_identity=companion.data.identity_list("tushare","510300.SH")
    assert len(etf_identity)==1
    fund_stream=companion.data.stream_get("tushare","fund_daily")
    fund_batch=companion.data.batch_list(fund_stream["id"])[0]
    fund_rows=json.loads(companion.data.object_read(fund_batch["canonical_object_ids"][0]).decode())
    assert fund_rows[0]["asset_id"]==etf_identity[0]["asset_id"]
    companion.data.stream_configure(provider="tushare",capability="suspend_d",schema_version="tushare-normalizer/2",config={"mode":"active-empty-fixture"})
    companion.data.stream_set_status("tushare","suspend_d","active")
    monkeypatch.setattr(TushareAdapter,"_load_token",staticmethod(lambda _token,_token_file:"fixture-job-token"))
    monkeypatch.setattr(TushareAdapter,"_http_transport",lambda _self,_body,_timeout:json.dumps({"code":0,"msg":None,"data":{"fields":["ts_code","trade_date","suspend_type"],"items":[]}}).encode())
    companion.v4_bootstrap_jobs()
    definition=companion.jobs.definition_for_handler("data.tushare_ingest",status="inactive")
    companion.jobs.definition_set_status(definition["id"],"active",reason="empty-valid fixture")
    empty_job=companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs={"refs":[],"parameters":{"capability":"suspend_d","params":{"trade_date":"20260817"}},"knowledge_cutoff":iso()},idempotency_key="fixture-empty-valid-active")
    empty_result=companion.jobs.run_once("pytest-empty-valid",lease_seconds=30)
    assert empty_result["id"]==empty_job["id"] and empty_result["status"]=="succeeded"
    empty_report=companion.data.manifest_get(empty_result["output_manifest_id"])["manifest"]["manifest"]
    assert empty_report["status"]=="empty_valid" and empty_report["row_count"]==0


def test_isolated_job_denies_network_and_fails_parent_atomically(tmp_path: Path):
    companion=new_companion(tmp_path);pass_gate(companion,"G0");companion.jobs.feature_set("v4_jobs",True)
    def tries_network(_context):
        import socket
        socket.create_connection(("127.0.0.1",9),timeout=0.1)
        return {}
    companion.jobs.register_handler("fixture.network","1",tries_network)
    with pytest.raises(CompanionError,match="restricted to the allow-listed"):
        companion.jobs.definition_create(name="forbidden network profile",handler="fixture.network",handler_version="1",resource_budget={"max_wall_seconds":5,"max_cpu_seconds":3,"max_memory_mb":512,"max_input_bytes":10000,"lease_seconds":10,"network":"tushare_official","model_tokens":0})
    definition=companion.jobs.definition_create(name="network denial",handler="fixture.network",handler_version="1",status="active",resource_budget={"max_wall_seconds":5,"max_cpu_seconds":3,"max_memory_mb":512,"max_input_bytes":10000,"lease_seconds":10,"network":"deny","model_tokens":0})
    job=companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs={"refs":[]},idempotency_key="fixture-network")
    result=companion.jobs.run_once("pytest-network",lease_seconds=10)
    assert result["status"]=="failed" and "denied audit event" in result["error"]
    assert companion.run_get(job["parent_run_id"])["status"]=="failed"
    def tries_fork(_context):
        import os
        os.fork()
        return {}
    companion.jobs.register_handler("fixture.fork","1",tries_fork)
    fork_definition=companion.jobs.definition_create(name="fork denial",handler="fixture.fork",handler_version="1",status="active",resource_budget={"max_wall_seconds":5,"max_cpu_seconds":3,"max_memory_mb":512,"max_input_bytes":10000,"lease_seconds":10,"network":"deny","model_tokens":0})
    fork_job=companion.jobs.enqueue_new_parent(definition_id=fork_definition["id"],inputs={"refs":[]},idempotency_key="fixture-fork")
    fork_result=companion.jobs.run_once("pytest-fork",lease_seconds=10)
    assert fork_result["status"]=="failed" and "os.fork" in fork_result["error"]
    assert companion.run_get(fork_job["parent_run_id"])["status"]=="failed"
    def returns_large_result(_context):
        report=companion.data.manifest_publish(kind="fixture_large_job",schema_version="fixture/v1",manifest={"purpose":"pipe backpressure regression"})
        return {"manifest_id":report["id"],"output_refs":[report["id"]],"padding":"x"*200_000,"model_tokens":0}
    companion.jobs.register_handler("fixture.large","1",returns_large_result)
    large_definition=companion.jobs.definition_create(name="large result",handler="fixture.large",handler_version="1",status="active",resource_budget={"max_wall_seconds":5,"max_cpu_seconds":3,"max_memory_mb":512,"max_input_bytes":10000,"max_output_bytes":300000,"lease_seconds":10,"network":"deny","model_tokens":0})
    large_job=companion.jobs.enqueue_new_parent(definition_id=large_definition["id"],inputs={"refs":[]},idempotency_key="fixture-large-result")
    large_result=companion.jobs.run_once("pytest-large-result",lease_seconds=10)
    assert large_result["id"]==large_job["id"] and large_result["status"]=="succeeded"


def test_job_idempotency_crash_recovery_and_post_claim_failure(tmp_path: Path,monkeypatch):
    companion=new_companion(tmp_path);pass_gate(companion,"G0");companion.jobs.feature_set("v4_jobs",True)
    definition=companion.jobs.definition_create(name="recovery fixture",handler="system.echo_manifest",handler_version="1",status="active")
    inputs={"refs":[],"parameters":{"version":1},"knowledge_cutoff":iso()}
    job=companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs=inputs,idempotency_key="fixture-recovery")
    assert companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs=inputs,idempotency_key="fixture-recovery")["id"]==job["id"]
    with pytest.raises(CompanionError,match="idempotency key"):
        companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs={**inputs,"parameters":{"version":2}},idempotency_key="fixture-recovery")
    claimed=companion.jobs.claim("crashed-worker",lease_seconds=30);assert claimed and claimed["id"]==job["id"]
    expired=iso(utc_now()-timedelta(seconds=1))
    with companion.db.transaction() as con:
        con.execute("UPDATE runs SET lease_until=? WHERE id=?",(expired,job["parent_run_id"]))
        con.execute("UPDATE job_runs SET status='running',lease_until=? WHERE id=?",(expired,job["id"]))
        con.execute("UPDATE job_steps SET status='running',lease_owner='crashed-worker',lease_until=? WHERE job_run_id=?",(expired,job["id"]))
    recovered=companion.recover()["recovered"]
    assert recovered["runs"]==1 and recovered["job_runs"]==1 and recovered["job_steps"]==1
    completed=companion.jobs.run_once("replacement-worker",lease_seconds=30)
    assert completed and completed["id"]==job["id"] and completed["status"]=="succeeded"

    post_claim=companion.jobs.enqueue_new_parent(definition_id=definition["id"],inputs=inputs,idempotency_key="fixture-post-claim-failure")
    def reject_after_claim(_handler):raise CompanionError("fixture post-claim validation failure")
    monkeypatch.setattr(companion.jobs,"_require_handler_feature",reject_after_claim)
    failed=companion.jobs.run_once("validation-failure-worker",lease_seconds=30)
    assert failed and failed["id"]==post_claim["id"] and failed["status"]=="failed"
    assert companion.run_get(post_claim["parent_run_id"])["status"]=="failed"


def test_deterministic_schedule_routes_strict_typed_inputs(tmp_path: Path):
    companion=new_companion(tmp_path);pass_gate(companion,"G0");companion.jobs.feature_set("v4_jobs",True)
    definition=companion.jobs.definition_create(
        name="strict scheduled fixture",handler="system.echo_manifest",handler_version="1",status="active",
        input_schema={
            "type":"object","required":["refs","parameters","knowledge_cutoff"],
            "properties":{"refs":{"type":"array"},"parameters":{"type":"object"},"knowledge_cutoff":{"type":"string"}},
            "additionalProperties":False,
        },
    )
    schedule=companion.schedule_create(
        name="deterministic fixture",kind="one_shot",mission="route typed inputs",
        cadence={"type":"one_shot","at":iso(utc_now()-timedelta(seconds=1))},
        scope={"refs":[],"parameters":{"fixture":True}},dispatch_type="deterministic_pipeline",
        job_definition_id=definition["id"],
    )
    tick=companion.tick(limit=1)
    assert tick["ok"] is True and len(tick["queued_job_runs"])==1 and not tick["queued_outbox"]
    job=companion.jobs.run_get(tick["queued_job_runs"][0])
    assert set(job["inputs"])=={"refs","parameters","knowledge_cutoff"}
    with companion.db.transaction() as con:con.execute("UPDATE gate_assessments SET schema_version=3 WHERE gate='G0' AND scope='test_fixture'")
    with pytest.raises(CompanionError,match="release gates not satisfied"):
        companion.jobs.run_once("pytest-stale-gate",lease_seconds=10)
    assert companion.jobs.run_get(job["id"])["status"]=="queued"
    with companion.db.transaction() as con:con.execute("UPDATE gate_assessments SET schema_version=? WHERE gate='G0' AND scope='test_fixture'",(SCHEMA_VERSION,))
    completed=companion.jobs.run_once("pytest-schedule",lease_seconds=10)
    assert completed["status"]=="succeeded"
    assert companion.run_get(completed["parent_run_id"])["status"]=="succeeded"
    assert companion.schedule_get(schedule["id"])["status"]=="expired"
    second=companion.jobs.definition_create(name="changed route fixture",handler="system.echo_manifest",handler_version="1",status="active")
    frozen_run_id="run_frozen_route_fixture";frozen_payload={"mission":"frozen","scope":{"refs":[],"parameters":{"version":1}},"policy":{},"dispatch_type":"deterministic_pipeline","job_definition_id":definition["id"]}
    with companion.db.transaction() as con:
        con.execute("INSERT INTO runs(id,schedule_id,kind,status,due_at,idempotency_key,payload_json,created_at,dispatch_type) VALUES(?,?,?,'queued',?,?,?,?,'deterministic_pipeline')",(frozen_run_id,schedule["id"],"one_shot",iso(),"fixture-frozen-route",json.dumps(frozen_payload,sort_keys=True,separators=(',',':')),iso()))
    companion.schedule_patch(schedule["id"],companion.schedule_get(schedule["id"])["version"],{"scope":{"refs":[],"parameters":{"version":2}},"job_definition_id":second["id"]})
    routed=companion.route_pending_runs(limit=5)
    assert frozen_run_id in routed["routed_runs"]
    frozen_job=companion.jobs.run_get(companion.run_get(frozen_run_id)["job_run_id"])
    assert frozen_job["job_definition_id"]==definition["id"] and frozen_job["inputs"]["parameters"]=={"version":1}


def test_mcp_advertises_safe_v4_v5_surface(tmp_path: Path):
    request="\n".join([
        json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
        json.dumps({"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}),
        json.dumps({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"v4_status","arguments":{"unexpected":True}}}),
        json.dumps({"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"v5_today","arguments":{}}}),
    ])+"\n"
    proc=subprocess.run(
        [sys.executable,"-m","companion.mcp_server"],input=request,text=True,capture_output=True,cwd=Path(__file__).parents[1],
        env={**__import__("os").environ,"COMPANION_ROOT":str(tmp_path),"COMPANION_GATE_SCOPE":"test_fixture"},timeout=30,check=True,
    )
    responses=[json.loads(line) for line in proc.stdout.splitlines()]
    names={tool["name"] for tool in responses[1]["result"]["tools"]}
    assert responses[0]["result"]["serverInfo"]["version"]=="7.0.0"
    assert {"v4_status","v4_experiment_submit","v4_shadow_rebalance","v4_manual_action_validate"}<=names
    assert {"v5_today","v5_program_create","v5_opportunity_transition","v5_action_card","v5_program_metrics_calculate","v5_scorecard_publish","wake_claim","wake_complete","v4_manifest_get","v5_quant_research_status","v5_quant_experiment_status","v5_quant_scan_get","v5_quant_review_get","v5_research_quality_status"}<=names
    assert "manifest_publish" not in names and "experiment_complete" not in names
    assert responses[2]["result"]["isError"] is True and "unsupported fields" in responses[2]["result"]["content"][0]["text"]
    assert responses[3]["result"]["structuredContent"]["result"]["mode"]=="setup_required"
