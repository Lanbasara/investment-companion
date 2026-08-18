from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from companion.v4_data import (
    ContentAddressedStore,
    DataIntegrityError,
    DataObjectRef,
    DataPathError,
    DatasetSnapshotManifest,
    ManifestValidationError,
    PITValidationError,
    SnapshotPartition,
    canonical_json_bytes,
    normalize_pit_fact,
    pit_fact_hash,
    query_pit_facts,
)


class ContentAddressedStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.store = ContentAddressedStore(self.base / "market-data", root_id="test-market-data")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_atomic_content_addressing_is_canonical_and_idempotent(self) -> None:
        first = self.store.put_json(
            {"b": 2, "a": [1, {"中文": True}]}, namespace="canonical", kind="fixture"
        )
        second = self.store.put_json(
            {"a": [1, {"中文": True}], "b": 2}, namespace="canonical", kind="fixture"
        )
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.relative_path, second.relative_path)
        self.assertEqual(
            self.store.read(first),
            b'{"a":[1,{"\xe4\xb8\xad\xe6\x96\x87":true}],"b":2}',
        )
        self.assertEqual(self.store.published_hashes("canonical"), (first.sha256,))
        self.assertNotIn(str(self.store.root), json.dumps(first.as_dict()))

    def test_read_revalidates_size_and_hash(self) -> None:
        reference = self.store.put_bytes(b"official bytes", namespace="raw")
        path = self.store.root / reference.relative_path
        path.write_bytes(b"tampered bytes")
        with self.assertRaises(DataIntegrityError):
            self.store.read(reference)

        wrong_size = DataObjectRef(
            root_id=reference.root_id,
            namespace=reference.namespace,
            relative_path=reference.relative_path,
            sha256=reference.sha256,
            size=reference.size + 1,
        )
        with self.assertRaises(DataIntegrityError):
            self.store.read(wrong_size)

    def test_absolute_traversal_wrong_root_and_symlink_escape_fail_closed(self) -> None:
        digest = hashlib.sha256(b"x").hexdigest()
        with self.assertRaises(DataPathError):
            DataObjectRef(
                root_id="test-market-data",
                namespace="raw",
                relative_path="/etc/passwd",
                sha256=digest,
                size=1,
            )
        with self.assertRaises(DataPathError):
            DataObjectRef(
                root_id="test-market-data",
                namespace="raw",
                relative_path="raw/sha256/../outside",
                sha256=digest,
                size=1,
            )

        reference = self.store.put_bytes(b"x", namespace="raw")
        wrong_root = dict(reference.as_dict())
        wrong_root["root_id"] = "another-root"
        with self.assertRaises(DataPathError):
            self.store.read(wrong_root)

        symlink_root = self.base / "symlink-market-data"
        symlink_store = ContentAddressedStore(symlink_root, root_id="symlink-test")
        outside = self.base / "outside"
        outside.mkdir()
        os.symlink(outside, symlink_root / "raw")
        with self.assertRaises(DataPathError):
            symlink_store.put_bytes(b"must not escape", namespace="raw")

    def test_replacing_published_file_with_symlink_is_rejected(self) -> None:
        reference = self.store.put_bytes(b"inside", namespace="raw")
        published = self.store.root / reference.relative_path
        outside = self.base / "outside-object"
        outside.write_bytes(b"inside")
        published.unlink()
        os.symlink(outside, published)
        with self.assertRaises(DataPathError):
            self.store.read(reference)

    def test_crashed_temp_file_is_never_a_published_object(self) -> None:
        crashed = self.store.root / ".tmp" / (".pending-" + "a" * 64)
        crashed.write_bytes(b"partial")
        malformed = self.store.root / "raw" / "sha256" / "aa" / (".pending-" + "b" * 64)
        malformed.parent.mkdir(parents=True)
        malformed.write_bytes(b"partial")
        self.assertEqual(self.store.published_hashes("raw"), ())

        official = self.store.put_bytes(b"complete", namespace="raw")
        self.assertEqual(self.store.published_hashes("raw"), (official.sha256,))
        self.assertTrue(crashed.exists(), "cleanup is separate from publication semantics")

    def test_repository_local_data_root_is_rejected_before_creation(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        local_data = repository / ".must-not-create-v4-data-test"
        self.assertFalse(local_data.exists())
        with self.assertRaises(DataPathError):
            ContentAddressedStore(local_data, root_id="repo-local-test")
        self.assertFalse(local_data.exists())


class DatasetSnapshotManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.store = ContentAddressedStore(self.base / "market-data", root_id="manifest-test")
        self.raw = self.store.put_bytes(b"provider response", namespace="raw", kind="raw_response")
        self.partition = self.store.put_json(
            [{"asset_id": "asset_a", "close": "10.00"}],
            namespace="canonical",
            kind="daily_partition",
        )
        self.calendar = self.store.put_json(
            ["2026-01-05"], namespace="canonical", kind="trading_calendar"
        )
        self.actions = self.store.put_json(
            [], namespace="canonical", kind="corporate_actions"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, **changes: object) -> DatasetSnapshotManifest:
        values: dict[str, object] = {
            "knowledge_cutoff": "2026-01-05T16:00:00+08:00",
            "input_objects": [self.raw],
            "partitions": [
                SnapshotPartition(
                    "daily/2026-01-05",
                    self.partition,
                    row_count=1,
                    quality={"status": "passed", "coverage": "1/1"},
                )
            ],
            "denominator": {"asset_ids": ["asset_a", "asset_b"], "count": 2},
            "universe": {"eligible": ["asset_a"], "definition_version": "cn-a-v1"},
            "exclusions": [{"asset_id": "asset_b", "reason": "suspended"}],
            "calendar": {"object_ref": self.calendar, "version": "sse-2026-v1"},
            "corporate_actions": {"object_ref": self.actions, "policy": "known-at-cutoff"},
            "quality": {"status": "passed", "blocked_partitions": []},
            "code_version": {"commit": "abc123", "parser": "daily-v1"},
        }
        values.update(changes)
        return DatasetSnapshotManifest(**values)

    def test_same_content_has_same_id_and_any_fixed_input_change_has_new_id(self) -> None:
        first = self._manifest()
        equivalent = DatasetSnapshotManifest.from_dict(first.as_dict())
        self.assertEqual(first.snapshot_id, equivalent.snapshot_id)
        self.assertEqual(first.knowledge_cutoff, "2026-01-05T08:00:00Z")

        changes = [
            {"knowledge_cutoff": "2026-01-05T08:00:01Z"},
            {"denominator": {"asset_ids": ["asset_a"], "count": 1}},
            {"universe": {"eligible": [], "definition_version": "cn-a-v1"}},
            {"exclusions": []},
            {"calendar": {"object_ref": self.calendar, "version": "sse-2026-v2"}},
            {"corporate_actions": {"object_ref": self.actions, "policy": "revised"}},
            {"quality": {"status": "passed", "coverage": "partial", "blocked_partitions": []}},
            {"code_version": {"commit": "def456", "parser": "daily-v1"}},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertNotEqual(first.snapshot_id, self._manifest(**change).snapshot_id)

    def test_manifest_is_deeply_immutable_and_does_not_share_caller_state(self) -> None:
        denominator = {"asset_ids": ["asset_a"], "count": 1}
        manifest = self._manifest(denominator=denominator)
        denominator["asset_ids"].append("later_mutation")
        self.assertEqual(manifest.denominator["asset_ids"], ("asset_a",))
        with self.assertRaises(TypeError):
            manifest.denominator["count"] = 2
        with self.assertRaises(AttributeError):
            manifest.denominator["asset_ids"].append("x")

    def test_ready_snapshot_rejects_blocked_or_pending_partition(self) -> None:
        blocked = SnapshotPartition(
            "daily/blocked",
            self.partition,
            status="blocked",
            blocked_reason="coverage below threshold",
        )
        with self.assertRaises(ManifestValidationError):
            self._manifest(partitions=[blocked])

        pending = SnapshotPartition("daily/pending", self.partition, status="pending")
        with self.assertRaises(ManifestValidationError):
            self._manifest(partitions=[pending])

        with self.assertRaises(ManifestValidationError):
            self._manifest(quality={"status": "blocked"})

        diagnostic = self._manifest(status="blocked", partitions=[blocked])
        self.assertEqual(diagnostic.status, "blocked")

    def test_publish_verifies_every_input_then_round_trips_immutable_manifest(self) -> None:
        manifest = self._manifest()
        reference = manifest.publish(self.store)
        self.assertEqual(reference.sha256, manifest.snapshot_id)
        self.assertEqual(reference.namespace, "manifests")
        loaded = DatasetSnapshotManifest.load(self.store, reference)
        self.assertEqual(loaded.as_dict(), manifest.as_dict())
        self.assertEqual(manifest.publish(self.store), reference)

    def test_missing_or_tampered_partition_prevents_manifest_publication(self) -> None:
        manifest = self._manifest()
        path = self.store.root / self.partition.relative_path
        path.write_bytes(b"tampered")
        with self.assertRaises(ManifestValidationError):
            manifest.publish(self.store)
        self.assertEqual(self.store.published_hashes("manifests"), ())

    def test_supplied_snapshot_id_is_verified(self) -> None:
        value = self._manifest().as_dict()
        value["snapshot_id"] = "0" * 64
        with self.assertRaises(ManifestValidationError):
            DatasetSnapshotManifest.from_dict(value)


class PITPrimitiveTest(unittest.TestCase):
    RAW_HASH = hashlib.sha256(b"raw fixture").hexdigest()

    def _fact(self, revision_id: str, **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "entity_key": "asset_a",
            "fact_key": "st_status",
            "asset_id": "asset_a",
            "field": "st_status",
            "value": False,
            "effective_at": "2026-01-01T00:00:00+08:00",
            "effective_to": None,
            "first_known_at": "2026-01-02T09:00:00+08:00",
            "ingested_at": "2026-01-02T09:05:00+08:00",
            "revision_id": revision_id,
            "supersedes": None,
            "raw_hash": self.RAW_HASH,
            "parser_version": "status-parser-v1",
            "quality": {"status": "verified"},
        }
        value.update(changes)
        if "field" in changes and "fact_key" not in changes:
            value["fact_key"] = changes["field"]
        return value

    def test_normalization_requires_all_pit_fields_and_timezone(self) -> None:
        fact = self._fact("revision-1")
        del fact["first_known_at"]
        with self.assertRaises(PITValidationError):
            normalize_pit_fact(fact)

        with self.assertRaises(PITValidationError):
            normalize_pit_fact(self._fact("revision-1", effective_at="2026-01-01T00:00:00"))
        with self.assertRaises(PITValidationError):
            normalize_pit_fact(self._fact("revision-1", raw_hash=self.RAW_HASH.upper()))
        with self.assertRaises(PITValidationError):
            normalize_pit_fact(self._fact("revision-1", quality="good"))

    def test_normalization_is_pure_canonical_and_hash_stable(self) -> None:
        original = self._fact("revision-1")
        copy = dict(original)
        normalized = normalize_pit_fact(original)
        self.assertEqual(original, copy)
        self.assertEqual(normalized["effective_at"], "2025-12-31T16:00:00Z")
        self.assertEqual(normalized["first_known_at"], "2026-01-02T01:00:00Z")
        reordered = {key: original[key] for key in reversed(list(original))}
        self.assertEqual(pit_fact_hash(original), pit_fact_hash(reordered))
        self.assertEqual(
            canonical_json_bytes(normalized),
            canonical_json_bytes(normalize_pit_fact(reordered)),
        )

    def test_future_revision_cannot_pollute_old_knowledge_cutoff(self) -> None:
        original = self._fact("revision-1", value=False)
        future = self._fact(
            "revision-2",
            value=True,
            supersedes="revision-1",
            first_known_at="2026-03-01T09:00:00+08:00",
            ingested_at="2026-03-01T09:05:00+08:00",
        )
        before_injection = query_pit_facts(
            [original], knowledge_cutoff="2026-02-01T00:00:00Z"
        )
        after_injection = query_pit_facts(
            [future, original], knowledge_cutoff="2026-02-01T00:00:00Z"
        )
        self.assertEqual(before_injection, after_injection)
        self.assertEqual(after_injection[0]["revision_id"], "revision-1")

        current = query_pit_facts(
            [future, original], knowledge_cutoff="2026-04-01T00:00:00Z"
        )
        self.assertEqual([fact["revision_id"] for fact in current], ["revision-2"])

    def test_effective_as_of_filters_temporal_validity_after_revision_selection(self) -> None:
        ended = self._fact(
            "revision-ended",
            field="board",
            value="main",
            effective_to="2026-02-01T00:00:00+08:00",
        )
        later = self._fact(
            "revision-later",
            field="suspension",
            value=True,
            effective_at="2026-03-01T00:00:00+08:00",
        )
        january = query_pit_facts(
            [later, ended],
            knowledge_cutoff="2026-04-01T00:00:00Z",
            effective_as_of="2026-01-15T00:00:00+08:00",
        )
        self.assertEqual([fact["revision_id"] for fact in january], ["revision-ended"])
        february = query_pit_facts(
            [later, ended],
            knowledge_cutoff="2026-04-01T00:00:00Z",
            effective_as_of="2026-02-15T00:00:00+08:00",
        )
        self.assertEqual(february, [])

    def test_invalid_revision_graphs_fail_closed(self) -> None:
        missing_parent = self._fact("revision-2", supersedes="missing")
        with self.assertRaises(PITValidationError):
            query_pit_facts([missing_parent], knowledge_cutoff="2026-04-01T00:00:00Z")

        root = self._fact("revision-1")
        fork_a = self._fact(
            "revision-2a",
            supersedes="revision-1",
            first_known_at="2026-02-01T00:00:00Z",
            ingested_at="2026-02-01T00:01:00Z",
        )
        fork_b = self._fact(
            "revision-2b",
            supersedes="revision-1",
            first_known_at="2026-02-02T00:00:00Z",
            ingested_at="2026-02-02T00:01:00Z",
        )
        with self.assertRaises(PITValidationError):
            query_pit_facts([root, fork_a, fork_b], knowledge_cutoff="2026-04-01T00:00:00Z")

        collision_a = self._fact("same-id", value=True)
        collision_b = self._fact("same-id", value=False)
        with self.assertRaises(PITValidationError):
            query_pit_facts(
                [collision_a, collision_b], knowledge_cutoff="2026-04-01T00:00:00Z"
            )


if __name__ == "__main__":
    unittest.main()
