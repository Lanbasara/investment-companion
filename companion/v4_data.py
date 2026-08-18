from __future__ import annotations

"""Deterministic, file-backed primitives for the V4 market-data domain.

This module deliberately has no database, provider, clock, or network dependency.
It owns the small contracts needed before adapters and jobs can be trusted:

* immutable, content-addressed objects under a constrained external data root;
* immutable dataset snapshot manifests and their ready barrier; and
* point-in-time fact normalization and knowledge-cutoff queries.

The control plane may register the returned references in SQLite, but a reference
is not accepted as proof that bytes exist: every read revalidates path, type,
length, and SHA-256.
"""

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ROOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NAMESPACES = frozenset({"raw", "canonical", "manifests", "derived", "experiments"})
_PARTITION_STATES = frozenset({"pending", "ready", "blocked"})
_SNAPSHOT_STATES = frozenset({"building", "ready", "blocked"})
_PIT_REQUIRED_FIELDS = frozenset(
    {
        "effective_at",
        "effective_to",
        "first_known_at",
        "ingested_at",
        "revision_id",
        "supersedes",
        "raw_hash",
        "parser_version",
        "quality",
        "entity_key",
        "fact_key",
    }
)


class V4DataError(ValueError):
    """Base class for a rejected V4 data operation."""


class DataPathError(V4DataError):
    """A data reference or configured root is outside the allowed boundary."""


class DataIntegrityError(V4DataError):
    """Published bytes do not match their immutable reference."""


class ManifestValidationError(V4DataError):
    """A dataset snapshot manifest is incomplete or internally inconsistent."""


class PITValidationError(V4DataError):
    """A point-in-time fact or revision chain is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    """Return the single canonical JSON representation used for content IDs."""

    try:
        return json.dumps(
            _thaw(value),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise V4DataError(f"value is not canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _validate_sha256(value: Any, field_name: str = "sha256") -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise V4DataError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


def _validate_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V4DataError(f"{field_name} must be a non-empty string")
    if "\x00" in value:
        raise V4DataError(f"{field_name} contains a NUL byte")
    return value


def _validate_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise DataPathError("relative_path must be a non-empty string")
    if "\x00" in value or "\\" in value:
        raise DataPathError("relative_path contains an unsafe path character")
    if PurePosixPath(value).is_absolute() or value.startswith("/"):
        raise DataPathError("absolute data-object paths are forbidden")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DataPathError("relative_path must not contain empty, '.' or '..' segments")
    # Also reject drive-looking first components on non-Windows hosts.  References
    # are portable POSIX paths, not host-native paths.
    if ":" in parts[0]:
        raise DataPathError("relative_path must not contain a drive prefix")
    return value


def _object_relative_path(namespace: str, digest: str) -> str:
    if namespace not in _NAMESPACES:
        raise DataPathError(f"unsupported data namespace: {namespace!r}")
    _validate_sha256(digest)
    return f"{namespace}/sha256/{digest[:2]}/{digest}"


@dataclass(frozen=True)
class DataObjectRef:
    """A portable reference; it never contains a host absolute path."""

    root_id: str
    namespace: str
    relative_path: str
    sha256: str
    size: int
    media_type: str = "application/octet-stream"
    kind: str = "data_object"

    def __post_init__(self) -> None:
        if not isinstance(self.root_id, str) or not _ROOT_ID_RE.fullmatch(self.root_id):
            raise DataPathError("root_id must be a stable identifier, not a path")
        if self.namespace not in _NAMESPACES:
            raise DataPathError(f"unsupported data namespace: {self.namespace!r}")
        digest = _validate_sha256(self.sha256)
        relative_path = _validate_relative_path(self.relative_path)
        if relative_path != _object_relative_path(self.namespace, digest):
            raise DataPathError("relative_path does not match namespace and content hash")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise V4DataError("size must be a non-negative integer")
        _validate_nonempty_string(self.media_type, "media_type")
        _validate_nonempty_string(self.kind, "kind")

    @property
    def object_id(self) -> str:
        return f"sha256:{self.sha256}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "media_type": self.media_type,
            "namespace": self.namespace,
            "relative_path": self.relative_path,
            "root_id": self.root_id,
            "sha256": self.sha256,
            "size": self.size,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DataObjectRef":
        if not isinstance(value, Mapping):
            raise DataPathError("data object reference must be a mapping")
        required = {"root_id", "namespace", "relative_path", "sha256", "size"}
        missing = sorted(required.difference(value))
        if missing:
            raise DataPathError(f"data object reference missing: {', '.join(missing)}")
        return cls(
            root_id=value["root_id"],
            namespace=value["namespace"],
            relative_path=value["relative_path"],
            sha256=value["sha256"],
            size=value["size"],
            media_type=value.get("media_type", "application/octet-stream"),
            kind=value.get("kind", "data_object"),
        )


class ContentAddressedStore:
    """An atomic, fail-closed content-addressed object store.

    ``data_root`` is configuration, while persisted references contain only
    ``root_id`` and a generated relative path.  By default the root is rejected
    when it is inside this source checkout.  Tests or installed deployments may
    pass an explicit ``repository_root`` to define the boundary.
    """

    def __init__(
        self,
        data_root: str | os.PathLike[str],
        *,
        root_id: str = "market-data-v1",
        repository_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if not isinstance(root_id, str) or not _ROOT_ID_RE.fullmatch(root_id):
            raise DataPathError("root_id must be a stable identifier, not a path")
        configured = Path(data_root).expanduser()
        if not configured.is_absolute():
            configured = Path.cwd() / configured
        configured = configured.absolute()
        _reject_symlink_components(configured)
        resolved = configured.resolve(strict=False)

        repo = (
            Path(repository_root).expanduser().resolve(strict=False)
            if repository_root is not None
            else Path(__file__).resolve().parents[1]
        )
        if resolved == repo or repo in resolved.parents:
            raise DataPathError("V4 bulk data_root must be outside the Git repository")

        resolved.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(resolved)
        if not resolved.is_dir():
            raise DataPathError("data_root is not a directory")

        self.root = resolved
        self.root_id = root_id
        tmp_dir = self._secure_path(".tmp")
        tmp_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
        _reject_symlink_components(tmp_dir)

    def put_bytes(
        self,
        content: bytes | bytearray | memoryview,
        *,
        namespace: str = "raw",
        media_type: str = "application/octet-stream",
        kind: str = "data_object",
    ) -> DataObjectRef:
        if not isinstance(content, (bytes, bytearray, memoryview)):
            raise TypeError("content must be bytes-like")
        payload = bytes(content)
        digest = hashlib.sha256(payload).hexdigest()
        relative_path = _object_relative_path(namespace, digest)
        destination = self._secure_path(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _reject_symlink_components(destination.parent)

        ref = DataObjectRef(
            root_id=self.root_id,
            namespace=namespace,
            relative_path=relative_path,
            sha256=digest,
            size=len(payload),
            media_type=media_type,
            kind=kind,
        )
        if _lexists(destination):
            # Existing content is immutable and must already be exactly the same.
            self.read(ref)
            return ref

        tmp_dir = self._secure_path(".tmp")
        fd, temporary_name = tempfile.mkstemp(prefix=".pending-", dir=tmp_dir)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb", closefd=True) as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._verify_file(temporary, digest, len(payload))

            # Revalidate all components immediately before publication.  Temp and
            # destination are on the same configured filesystem, so replace is
            # atomic.  No database state is involved in this primitive.
            destination = self._secure_path(relative_path)
            _reject_symlink_components(destination.parent)
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        finally:
            if _lexists(temporary):
                temporary.unlink()

        self.read(ref)
        return ref

    def put_json(
        self,
        value: Any,
        *,
        namespace: str = "canonical",
        media_type: str = "application/json",
        kind: str = "canonical_json",
    ) -> DataObjectRef:
        return self.put_bytes(
            canonical_json_bytes(value),
            namespace=namespace,
            media_type=media_type,
            kind=kind,
        )

    def read(self, reference: DataObjectRef | Mapping[str, Any]) -> bytes:
        ref = reference if isinstance(reference, DataObjectRef) else DataObjectRef.from_dict(reference)
        if ref.root_id != self.root_id:
            raise DataPathError(
                f"reference root_id {ref.root_id!r} does not match configured root {self.root_id!r}"
            )
        expected = _object_relative_path(ref.namespace, ref.sha256)
        if ref.relative_path != expected:
            raise DataPathError("reference path is not the canonical content path")
        path = self._secure_path(expected)
        return self._read_and_verify(path, ref.sha256, ref.size)

    def verify(self, reference: DataObjectRef | Mapping[str, Any]) -> DataObjectRef:
        ref = reference if isinstance(reference, DataObjectRef) else DataObjectRef.from_dict(reference)
        self.read(ref)
        return ref

    def published_hashes(self, namespace: str) -> tuple[str, ...]:
        """Return only fully published and verified object hashes.

        Files in ``.tmp`` and malformed files under a namespace are never part of
        this result.  A symlink or a corrupt object at an official content path is
        treated as an integrity failure rather than silently skipped.
        """

        if namespace not in _NAMESPACES:
            raise DataPathError(f"unsupported data namespace: {namespace!r}")
        base = self._secure_path(f"{namespace}/sha256")
        if not base.exists():
            return ()
        _reject_symlink_components(base)
        found: list[str] = []
        for prefix in sorted(base.iterdir(), key=lambda path: path.name):
            if prefix.is_symlink():
                raise DataPathError(f"symbolic links are forbidden under data_root: {prefix}")
            if not prefix.is_dir() or not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
                continue
            for path in sorted(prefix.iterdir(), key=lambda item: item.name):
                if not _SHA256_RE.fullmatch(path.name) or not path.name.startswith(prefix.name):
                    continue
                info = os.lstat(path)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise DataIntegrityError(f"published object is not a regular file: {path.name}")
                ref = DataObjectRef(
                    root_id=self.root_id,
                    namespace=namespace,
                    relative_path=_object_relative_path(namespace, path.name),
                    sha256=path.name,
                    size=info.st_size,
                    kind="discovered_object",
                )
                self.read(ref)
                found.append(path.name)
        return tuple(found)

    def _secure_path(self, relative_path: str) -> Path:
        relative_path = _validate_relative_path(relative_path)
        candidate = self.root.joinpath(*relative_path.split("/"))
        _reject_symlink_components(candidate)
        resolved = candidate.resolve(strict=False)
        if resolved != self.root and self.root not in resolved.parents:
            raise DataPathError("data-object path escapes configured data_root")
        return candidate

    def _verify_file(self, path: Path, expected_hash: str, expected_size: int) -> None:
        self._read_and_verify(path, expected_hash, expected_size)

    def _read_and_verify(self, path: Path, expected_hash: str, expected_size: int) -> bytes:
        _reject_symlink_components(path)
        try:
            info = os.lstat(path)
        except FileNotFoundError as exc:
            raise DataIntegrityError(f"published object is missing: {expected_hash}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DataIntegrityError(f"published object is not a regular file: {expected_hash}")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise DataIntegrityError(f"cannot safely open published object: {expected_hash}") from exc
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise DataIntegrityError(f"published object is not a regular file: {expected_hash}")
            chunks: list[bytes] = []
            hasher = hashlib.sha256()
            observed_size = 0
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                observed_size += len(chunk)
                hasher.update(chunk)
        finally:
            os.close(descriptor)
        if observed_size != expected_size:
            raise DataIntegrityError(
                f"object size mismatch for {expected_hash}: expected {expected_size}, got {observed_size}"
            )
        observed_hash = hasher.hexdigest()
        if observed_hash != expected_hash:
            raise DataIntegrityError(
                f"object hash mismatch for {expected_hash}: got {observed_hash}"
            )
        return b"".join(chunks)


@dataclass(frozen=True)
class SnapshotPartition:
    """One immutable partition participating in a dataset snapshot."""

    name: str
    object_ref: DataObjectRef | Mapping[str, Any]
    status: str = "ready"
    row_count: int | None = None
    schema_hash: str | None = None
    quality: Mapping[str, Any] = field(default_factory=dict)
    blocked_reason: str | None = None

    def __post_init__(self) -> None:
        name = _validate_nonempty_string(self.name, "partition name")
        # Partition keys commonly use logical paths such as
        # ``daily/2026-01-05``.  They are metadata, never joined to data_root,
        # but still reject ambiguous traversal/absolute forms.
        try:
            _validate_relative_path(name)
        except DataPathError as exc:
            raise ManifestValidationError(f"invalid partition name: {exc}") from exc
        ref = self.object_ref
        if not isinstance(ref, DataObjectRef):
            try:
                ref = DataObjectRef.from_dict(ref)
            except (TypeError, V4DataError) as exc:
                raise ManifestValidationError(f"invalid partition object_ref: {exc}") from exc
        if self.status not in _PARTITION_STATES:
            raise ManifestValidationError(f"invalid partition status: {self.status!r}")
        if self.row_count is not None and (
            isinstance(self.row_count, bool) or not isinstance(self.row_count, int) or self.row_count < 0
        ):
            raise ManifestValidationError("partition row_count must be a non-negative integer or null")
        if self.schema_hash is not None:
            try:
                _validate_sha256(self.schema_hash, "partition schema_hash")
            except V4DataError as exc:
                raise ManifestValidationError(str(exc)) from exc
        if not isinstance(self.quality, Mapping):
            raise ManifestValidationError("partition quality must be a mapping")
        if self.status == "blocked" and not self.blocked_reason:
            raise ManifestValidationError("blocked partition requires blocked_reason")
        object.__setattr__(self, "object_ref", ref)
        object.__setattr__(self, "quality", _freeze(self.quality))

    def as_dict(self) -> dict[str, Any]:
        return {
            "blocked_reason": self.blocked_reason,
            "name": self.name,
            "object_ref": self.object_ref.as_dict(),
            "quality": _thaw(self.quality),
            "row_count": self.row_count,
            "schema_hash": self.schema_hash,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SnapshotPartition":
        if not isinstance(value, Mapping):
            raise ManifestValidationError("partition must be a mapping")
        return cls(
            name=value.get("name"),
            object_ref=value.get("object_ref"),
            status=value.get("status", "ready"),
            row_count=value.get("row_count"),
            schema_hash=value.get("schema_hash"),
            quality=value.get("quality", {}),
            blocked_reason=value.get("blocked_reason"),
        )


@dataclass(frozen=True)
class DatasetSnapshotManifest:
    """The complete, immutable denominator and lineage for one calculation."""

    knowledge_cutoff: str
    input_objects: Sequence[DataObjectRef | Mapping[str, Any]]
    partitions: Sequence[SnapshotPartition | Mapping[str, Any]]
    denominator: Any
    universe: Any
    exclusions: Any
    calendar: Any
    corporate_actions: Any
    quality: Mapping[str, Any]
    code_version: Any
    status: str = "ready"
    schema_version: str = "investment-companion.dataset-snapshot/v1"
    parent_snapshot_id: str | None = None
    revision_delta: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            knowledge_cutoff = _canonical_instant(self.knowledge_cutoff, "knowledge_cutoff")
        except PITValidationError as exc:
            raise ManifestValidationError(str(exc)) from exc
        if self.status not in _SNAPSHOT_STATES:
            raise ManifestValidationError(f"invalid snapshot status: {self.status!r}")
        _validate_nonempty_string(self.schema_version, "schema_version")
        if self.parent_snapshot_id is not None:
            try:
                _validate_sha256(self.parent_snapshot_id, "parent_snapshot_id")
            except V4DataError as exc:
                raise ManifestValidationError(str(exc)) from exc
        if not isinstance(self.quality, Mapping):
            raise ManifestValidationError("snapshot quality must be a mapping")
        if not isinstance(self.metadata, Mapping):
            raise ManifestValidationError("snapshot metadata must be a mapping")
        if isinstance(self.code_version, str):
            _validate_nonempty_string(self.code_version, "code_version")
        elif not isinstance(self.code_version, Mapping):
            raise ManifestValidationError("code_version must be a string or mapping")

        input_refs: list[DataObjectRef] = []
        for value in self.input_objects:
            try:
                input_refs.append(value if isinstance(value, DataObjectRef) else DataObjectRef.from_dict(value))
            except (TypeError, V4DataError) as exc:
                raise ManifestValidationError(f"invalid input object reference: {exc}") from exc

        partitions: list[SnapshotPartition] = []
        for value in self.partitions:
            partitions.append(value if isinstance(value, SnapshotPartition) else SnapshotPartition.from_dict(value))
        names = [partition.name for partition in partitions]
        if len(names) != len(set(names)):
            raise ManifestValidationError("partition names must be unique")

        if self.status == "ready":
            if not input_refs:
                raise ManifestValidationError("ready snapshot requires at least one immutable input object")
            if not partitions:
                raise ManifestValidationError("ready snapshot requires at least one ready partition")
            blocked = [partition.name for partition in partitions if partition.status != "ready"]
            if blocked:
                raise ManifestValidationError(
                    "ready snapshot contains non-ready partition(s): " + ", ".join(sorted(blocked))
                )
            quality_status = self.quality.get("status")
            if quality_status not in {"passed", "healthy", "qualified"}:
                raise ManifestValidationError(
                    f"ready snapshot requires passed/healthy/qualified quality, got {quality_status!r}"
                )
            blocked_partitions = self.quality.get("blocked_partitions")
            if not isinstance(blocked_partitions, (list, tuple)) or blocked_partitions:
                raise ManifestValidationError("ready snapshot quality must declare blocked_partitions=[]")
            for partition in partitions:
                if partition.quality.get("status") not in {"passed", "healthy", "qualified"}:
                    raise ManifestValidationError(
                        f"ready partition {partition.name} lacks passed quality status"
                    )

        object.__setattr__(self, "knowledge_cutoff", knowledge_cutoff)
        object.__setattr__(self, "input_objects", tuple(input_refs))
        object.__setattr__(self, "partitions", tuple(partitions))
        object.__setattr__(self, "denominator", _freeze(self.denominator))
        object.__setattr__(self, "universe", _freeze(self.universe))
        object.__setattr__(self, "exclusions", _freeze(self.exclusions))
        object.__setattr__(self, "calendar", _freeze(self.calendar))
        object.__setattr__(self, "corporate_actions", _freeze(self.corporate_actions))
        object.__setattr__(self, "quality", _freeze(self.quality))
        object.__setattr__(self, "code_version", _freeze(self.code_version))
        object.__setattr__(self, "revision_delta", _freeze(self.revision_delta))
        object.__setattr__(self, "metadata", _freeze(self.metadata))

        # Force canonical-JSON validation now, not after a snapshot is announced.
        canonical_json_bytes(self.canonical_payload())

    @property
    def snapshot_id(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "calendar": _thaw(self.calendar),
            "code_version": _thaw(self.code_version),
            "corporate_actions": _thaw(self.corporate_actions),
            "denominator": _thaw(self.denominator),
            "exclusions": _thaw(self.exclusions),
            "input_objects": [reference.as_dict() for reference in self.input_objects],
            "knowledge_cutoff": self.knowledge_cutoff,
            "metadata": _thaw(self.metadata),
            "parent_snapshot_id": self.parent_snapshot_id,
            "partitions": [partition.as_dict() for partition in self.partitions],
            "quality": _thaw(self.quality),
            "revision_delta": _thaw(self.revision_delta),
            "schema_version": self.schema_version,
            "status": self.status,
            "universe": _thaw(self.universe),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.canonical_payload())

    def as_dict(self) -> dict[str, Any]:
        value = self.canonical_payload()
        value["snapshot_id"] = self.snapshot_id
        return value

    def referenced_objects(self) -> tuple[DataObjectRef, ...]:
        ordered: list[DataObjectRef] = []
        seen: set[tuple[str, str, str, str, int]] = set()
        values: list[Any] = list(self.input_objects)
        values.extend(partition.object_ref for partition in self.partitions)
        values.extend((self.calendar, self.corporate_actions))
        for value in values:
            for reference in _iter_object_refs(value):
                key = (
                    reference.root_id,
                    reference.namespace,
                    reference.relative_path,
                    reference.sha256,
                    reference.size,
                )
                if key not in seen:
                    seen.add(key)
                    ordered.append(reference)
        return tuple(ordered)

    def publish(self, store: ContentAddressedStore) -> DataObjectRef:
        # Re-run the barrier at the publication boundary.  The dataclass is deeply
        # immutable, so passing this check and verifying references is sufficient
        # before the atomic content-addressed write.
        if self.status == "ready":
            blocked = [partition.name for partition in self.partitions if partition.status != "ready"]
            if blocked:
                raise ManifestValidationError(
                    "ready snapshot contains non-ready partition(s): " + ", ".join(sorted(blocked))
                )
        for reference in self.referenced_objects():
            try:
                store.verify(reference)
            except V4DataError as exc:
                raise ManifestValidationError(
                    f"snapshot input {reference.object_id} failed verification: {exc}"
                ) from exc
        reference = store.put_bytes(
            self.canonical_bytes(),
            namespace="manifests",
            media_type="application/vnd.investment-companion.dataset-snapshot+json",
            kind="dataset_snapshot_manifest",
        )
        if reference.sha256 != self.snapshot_id:
            raise DataIntegrityError("published manifest hash differs from snapshot_id")
        return reference

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DatasetSnapshotManifest":
        if not isinstance(value, Mapping):
            raise ManifestValidationError("snapshot manifest must be a mapping")
        required = {
            "knowledge_cutoff",
            "input_objects",
            "partitions",
            "denominator",
            "universe",
            "exclusions",
            "calendar",
            "corporate_actions",
            "quality",
            "code_version",
        }
        missing = sorted(required.difference(value))
        if missing:
            raise ManifestValidationError(f"snapshot manifest missing: {', '.join(missing)}")
        manifest = cls(
            knowledge_cutoff=value["knowledge_cutoff"],
            input_objects=value["input_objects"],
            partitions=value["partitions"],
            denominator=value["denominator"],
            universe=value["universe"],
            exclusions=value["exclusions"],
            calendar=value["calendar"],
            corporate_actions=value["corporate_actions"],
            quality=value["quality"],
            code_version=value["code_version"],
            status=value.get("status", "ready"),
            schema_version=value.get("schema_version", "investment-companion.dataset-snapshot/v1"),
            parent_snapshot_id=value.get("parent_snapshot_id"),
            revision_delta=value.get("revision_delta"),
            metadata=value.get("metadata", {}),
        )
        supplied_id = value.get("snapshot_id")
        if supplied_id is not None and supplied_id != manifest.snapshot_id:
            raise ManifestValidationError("snapshot_id does not match canonical manifest content")
        return manifest

    @classmethod
    def load(
        cls,
        store: ContentAddressedStore,
        reference: DataObjectRef | Mapping[str, Any],
    ) -> "DatasetSnapshotManifest":
        ref = reference if isinstance(reference, DataObjectRef) else DataObjectRef.from_dict(reference)
        if ref.namespace != "manifests":
            raise ManifestValidationError("dataset snapshot reference must use the manifests namespace")
        try:
            payload = json.loads(store.read(ref).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ManifestValidationError("published snapshot manifest is not valid UTF-8 JSON") from exc
        manifest = cls.from_dict(payload)
        if manifest.snapshot_id != ref.sha256:
            raise ManifestValidationError("manifest object hash does not match computed snapshot_id")
        return manifest


def normalize_pit_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize one append-only point-in-time fact.

    No value is obtained from the current clock.  Callers must supply every time
    boundary explicitly, which makes normalization deterministic and replayable.
    Extra domain fields are retained.
    """

    if not isinstance(fact, Mapping):
        raise PITValidationError("PIT fact must be a mapping")
    missing = sorted(_PIT_REQUIRED_FIELDS.difference(fact))
    if missing:
        raise PITValidationError(f"PIT fact missing: {', '.join(missing)}")
    try:
        normalized = _thaw(_freeze(fact))
        normalized["effective_at"] = _canonical_instant(fact["effective_at"], "effective_at")
        normalized["effective_to"] = (
            None
            if fact["effective_to"] is None
            else _canonical_instant(fact["effective_to"], "effective_to")
        )
        normalized["first_known_at"] = _canonical_instant(fact["first_known_at"], "first_known_at")
        normalized["ingested_at"] = _canonical_instant(fact["ingested_at"], "ingested_at")
    except V4DataError as exc:
        if isinstance(exc, PITValidationError):
            raise
        raise PITValidationError(str(exc)) from exc

    if normalized["effective_to"] is not None and not (
        _instant(normalized["effective_at"]) < _instant(normalized["effective_to"])
    ):
        raise PITValidationError("effective_to must be later than effective_at")
    if _instant(normalized["first_known_at"]) > _instant(normalized["ingested_at"]):
        raise PITValidationError("first_known_at cannot be later than ingested_at")

    try:
        normalized["entity_key"] = _validate_nonempty_string(fact["entity_key"], "entity_key")
        normalized["fact_key"] = _validate_nonempty_string(fact["fact_key"], "fact_key")
        normalized["revision_id"] = _validate_nonempty_string(fact["revision_id"], "revision_id")
        supersedes = fact["supersedes"]
        if supersedes is not None:
            supersedes = _validate_nonempty_string(supersedes, "supersedes")
        if supersedes == normalized["revision_id"]:
            raise PITValidationError("a revision cannot supersede itself")
        normalized["supersedes"] = supersedes
        normalized["raw_hash"] = _validate_sha256(fact["raw_hash"], "raw_hash")
        normalized["parser_version"] = _validate_nonempty_string(
            fact["parser_version"], "parser_version"
        )
    except V4DataError as exc:
        if isinstance(exc, PITValidationError):
            raise
        raise PITValidationError(str(exc)) from exc
    if not isinstance(fact["quality"], Mapping):
        raise PITValidationError("quality must be a mapping")
    normalized["quality"] = _thaw(_freeze(fact["quality"]))
    try:
        canonical_json_bytes(normalized)
    except V4DataError as exc:
        raise PITValidationError(str(exc)) from exc
    return normalized


def pit_fact_hash(fact: Mapping[str, Any]) -> str:
    return canonical_sha256(normalize_pit_fact(fact))


def query_pit_facts(
    facts: Iterable[Mapping[str, Any]],
    *,
    knowledge_cutoff: str,
    effective_as_of: str | None = None,
) -> list[dict[str, Any]]:
    """Return the revision visible at ``knowledge_cutoff`` for every chain.

    Visibility is governed by ``first_known_at`` as required by the V4 PIT
    contract.  A future successor is filtered before supersession is applied, so
    it can never erase the predecessor from an older cutoff.  ``ingested_at`` is
    retained for provenance; callers wanting a historical *system ingestion*
    simulation should build a snapshot from the corresponding ingestion batch.
    """

    cutoff = _canonical_instant(knowledge_cutoff, "knowledge_cutoff")
    effective = (
        None if effective_as_of is None else _canonical_instant(effective_as_of, "effective_as_of")
    )
    by_id: dict[str, dict[str, Any]] = {}
    for candidate in facts:
        normalized = normalize_pit_fact(candidate)
        revision_id = normalized["revision_id"]
        previous = by_id.get(revision_id)
        if previous is not None:
            if canonical_json_bytes(previous) != canonical_json_bytes(normalized):
                raise PITValidationError(f"revision_id collision with different content: {revision_id}")
            continue
        by_id[revision_id] = normalized

    children: dict[str, list[str]] = defaultdict(list)
    for revision_id, fact in by_id.items():
        parent = fact["supersedes"]
        if parent is None:
            continue
        if parent not in by_id:
            raise PITValidationError(
                f"revision {revision_id} supersedes missing revision {parent}"
            )
        if _instant(fact["first_known_at"]) < _instant(by_id[parent]["first_known_at"]):
            raise PITValidationError(
                f"revision {revision_id} is known before the revision it supersedes"
            )
        if _pit_entity_identity(fact) != _pit_entity_identity(by_id[parent]):
            raise PITValidationError(
                f"revision {revision_id} cannot supersede a different PIT entity"
            )
        children[parent].append(revision_id)

    _assert_acyclic_revision_graph(by_id)
    visible = {
        revision_id
        for revision_id, fact in by_id.items()
        if _instant(fact["first_known_at"]) <= _instant(cutoff)
    }
    for parent, child_ids in children.items():
        visible_children = [revision_id for revision_id in child_ids if revision_id in visible]
        if len(visible_children) > 1:
            raise PITValidationError(
                f"ambiguous visible revision fork at {parent}: {', '.join(sorted(visible_children))}"
            )
    superseded_visible = {
        by_id[revision_id]["supersedes"]
        for revision_id in visible
        if by_id[revision_id]["supersedes"] in visible
    }
    selected = [by_id[revision_id] for revision_id in visible.difference(superseded_visible)]
    selected_by_fact: dict[tuple[str,str],list[str]]=defaultdict(list)
    for fact in selected:selected_by_fact[(fact["entity_key"],fact["fact_key"])].append(fact["revision_id"])
    ambiguous={key:ids for key,ids in selected_by_fact.items() if len(ids)>1}
    if ambiguous:
        raise PITValidationError(f"multiple visible revision leaves for fact_key: {ambiguous}")

    if effective is not None:
        effective_dt = _instant(effective)
        selected = [
            fact
            for fact in selected
            if _instant(fact["effective_at"]) <= effective_dt
            and (fact["effective_to"] is None or effective_dt < _instant(fact["effective_to"]))
        ]
    selected.sort(key=lambda fact: (fact["effective_at"], fact["revision_id"]))
    return [_thaw(_freeze(fact)) for fact in selected]


def _pit_entity_identity(fact: Mapping[str, Any]) -> bytes:
    return canonical_json_bytes({
        "entity_key":_validate_nonempty_string(fact.get("entity_key"),"entity_key"),
        "fact_key":_validate_nonempty_string(fact.get("fact_key"),"fact_key"),
    })


def _assert_acyclic_revision_graph(by_id: Mapping[str, Mapping[str, Any]]) -> None:
    for start in by_id:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor is not None:
            if cursor in seen:
                raise PITValidationError(f"revision supersedes cycle includes {cursor}")
            seen.add(cursor)
            cursor = by_id[cursor]["supersedes"]


def _canonical_instant(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise PITValidationError(f"{field_name} must be an ISO-8601 timestamp with timezone")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise PITValidationError(f"{field_name} must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PITValidationError(f"{field_name} must include a timezone")
    parsed = parsed.astimezone(timezone.utc)
    if parsed.microsecond:
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _freeze(value: Any) -> Any:
    if isinstance(value, DataObjectRef):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        # Sets have no stable JSON order and therefore no canonical content ID.
        raise V4DataError("sets are not valid canonical JSON values")
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, DataObjectRef):
        return value.as_dict()
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def _iter_object_refs(value: Any) -> Iterator[DataObjectRef]:
    if isinstance(value, DataObjectRef):
        yield value
        return
    if isinstance(value, Mapping):
        # A canonical DataObjectRef serialized into an auxiliary field remains a
        # reference and must be verified at manifest publication.
        required = {"root_id", "namespace", "relative_path", "sha256", "size"}
        if required.issubset(value):
            yield DataObjectRef.from_dict(value)
            return
        for item in value.values():
            yield from _iter_object_refs(item)
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from _iter_object_refs(item)


def _lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _reject_symlink_components(path: Path) -> None:
    absolute = path if path.is_absolute() else path.absolute()
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise DataPathError(f"symbolic links are forbidden under data_root: {current}")


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    except OSError as exc:
        if exc.errno not in {errno.EINVAL, getattr(errno, "ENOTSUP", errno.EINVAL)}:
            raise
    finally:
        os.close(descriptor)


# Concise aliases for callers that use the architecture-document terminology.
ContentAddressedObjectStore = ContentAddressedStore
DatasetManifest = DatasetSnapshotManifest
pit_query_as_of = query_pit_facts


__all__ = [
    "ContentAddressedObjectStore",
    "ContentAddressedStore",
    "DataIntegrityError",
    "DataObjectRef",
    "DataPathError",
    "DatasetManifest",
    "DatasetSnapshotManifest",
    "ManifestValidationError",
    "PITValidationError",
    "SnapshotPartition",
    "V4DataError",
    "canonical_json_bytes",
    "canonical_sha256",
    "normalize_pit_fact",
    "pit_fact_hash",
    "pit_query_as_of",
    "query_pit_facts",
]
