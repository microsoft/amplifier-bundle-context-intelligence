"""Bounded native-capture recovery with compact V2 checkpoints.

The checkpoint deliberately records artifact facts and one cursor per artifact
and configured destination. Event bodies are reconstructed from native JSONL
immediately before delivery; they are never checkpointed.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from amplifier_module_hook_context_intelligence.config_resolver import (
    Destination,
    HookConfigResolver,
)
from amplifier_module_hook_context_intelligence.fanout import (
    destination_is_active,
    normalize_match_key,
)
from amplifier_module_hook_context_intelligence.upload import build_payload
from context_intelligence.auth import build_auth_strategy
from context_intelligence.config import (
    DEFAULT_BASE_PATH,
    SETTINGS_PATH,
    _expand_env_placeholders,
    read_hook_config_block,
)

_NATIVE_FORMAT = "context-intelligence"
_NATIVE_VERSION = "1.0.0"
_TERMINAL_STATUSES = frozenset({"cancelled", "completed", "failed"})
_JOURNAL_LIMIT_BYTES = 4 * 1024 * 1024
_WAL_AUTOCHECKPOINT_PAGES = 256
_MAX_BACKOFF_S = 60.0
_MAX_SOURCE_LINE_BYTES = 16 * 1024 * 1024
_HELD_LOCK_PATHS: set[str] = set()
_NEXT_CURSOR_SQL = """
    SELECT c.*,a.path,a.session_id,a.workspace,a.working_dir,a.parent_id,a.record_count,
           a.source_stream_sha256,a.source_sha256,a.metadata_sha256,
           a.source_size,a.source_mtime_ns,a.source_inode,a.metadata_size,
           a.metadata_mtime_ns,a.metadata_inode
    FROM cursor c JOIN artifact a ON a.artifact_id=c.artifact_id
    WHERE c.target_fingerprint=? AND c.state='pending' AND a.state='eligible'
    ORDER BY c.source_order_key,c.artifact_id LIMIT 1
"""


@dataclass(frozen=True)
class DestinationConfig:
    """Resolved destinations plus names which the hook resolver rejected."""

    destinations: dict[str, Destination]
    invalid_names: tuple[str, ...]
    base_path: Path


@dataclass(frozen=True)
class ArtifactFact:
    """The constant-size facts obtained while streaming one artifact."""

    artifact_id: str
    path: Path
    state: str
    reason: str
    session_id: str = ""
    workspace: str = ""
    working_dir: str = ""
    parent_id: str = ""
    record_count: int = 0
    source_stream_sha256: str = ""
    source_sha256: str = ""
    metadata_sha256: str = ""
    source_size: int = 0
    source_mtime_ns: int = 0
    source_inode: int = 0
    metadata_size: int = 0
    metadata_mtime_ns: int = 0
    metadata_inode: int = 0


@dataclass(frozen=True)
class RecoverySummary:
    """Local summary. It deliberately has no source-path field."""

    inventory: dict[str, int]
    delivered: int
    pending: bool
    retry_after_s: float | None = None
    legacy_checkpoint_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "inventory": dict(sorted(self.inventory.items())),
            "delivered": self.delivered,
            "pending": self.pending,
            "legacy_checkpoint_present": self.legacy_checkpoint_present,
        }
        if self.retry_after_s is not None:
            result["retry_after_s"] = self.retry_after_s
        return result


def _expand_config(config: dict[str, Any]) -> dict[str, Any]:
    """Expand connection fields exactly as the standalone hook consumer does."""

    expanded = dict(config)
    for key in ("base_path", "context_intelligence_server_url", "context_intelligence_api_key"):
        if isinstance(expanded.get(key), str):
            expanded[key] = _expand_env_placeholders(expanded[key])
    raw_destinations = expanded.get("destinations")
    if isinstance(raw_destinations, dict):
        rebuilt: dict[str, Any] = {}
        for name, spec in raw_destinations.items():
            if not isinstance(spec, dict):
                rebuilt[name] = spec
                continue
            copied = dict(spec)
            for key in ("url", "api_key", "auth_resource"):
                if isinstance(copied.get(key), str):
                    copied[key] = _expand_env_placeholders(copied[key])
            rebuilt[name] = copied
        expanded["destinations"] = rebuilt
    return expanded


def _read_settings_document(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        path_stat = path.stat()
    except FileNotFoundError:
        return {}, None
    except OSError:
        return {}, "settings-unreadable"
    if not stat.S_ISREG(path_stat.st_mode):
        return {}, "settings-path-is-not-a-file"
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return {}, "settings-unreadable"
    except yaml.YAMLError:
        return {}, "settings-malformed"
    if loaded is None:
        return {}, None
    return (loaded, None) if isinstance(loaded, dict) else ({}, "settings-not-a-mapping")


def _recorded_project_dir(working_dir: str) -> tuple[Path | None, str | None]:
    project = Path(working_dir)
    try:
        if not stat.S_ISDIR(project.stat().st_mode):
            return None, "recorded-working-dir-unavailable"
    except (OSError, UnicodeError):
        return None, "recorded-working-dir-unavailable"
    return project, None


def _deep_merge_settings(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        existing = merged.get(key)
        merged[key] = (
            _deep_merge_settings(existing, value)
            if (isinstance(existing, dict) and isinstance(value, dict))
            else value
        )
    return merged


def _hook_config_from_settings(settings: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    overrides = settings.get("overrides", {})
    if not isinstance(overrides, dict):
        return {}, "overrides-not-a-mapping"
    hook_override = overrides.get("hook-context-intelligence")
    if hook_override is None:
        return {}, None
    if not isinstance(hook_override, dict):
        return {}, "hook-override-not-a-mapping"
    config = hook_override.get("config", {})
    return (config, None) if isinstance(config, dict) else ({}, "hook-config-not-a-mapping")


def _destination_config_from_hook_config(config: dict[str, Any]) -> DestinationConfig:
    expanded = _expand_config(config)
    resolver = HookConfigResolver(expanded, None)
    raw = expanded.get("destinations")
    raw_names = set(raw) if isinstance(raw, dict) else set()
    valid = resolver.validate_destinations()
    return DestinationConfig(
        destinations=valid,
        invalid_names=tuple(sorted(raw_names - set(valid))),
        base_path=resolver.base_path,
    )


def load_destination_config(settings_path: Path = SETTINGS_PATH) -> DestinationConfig:
    return _destination_config_from_hook_config(
        _expand_config(read_hook_config_block(settings_path))
    )


def load_effective_destination_config(
    working_dir: str, settings_path: Path = SETTINGS_PATH
) -> tuple[DestinationConfig | None, str | None]:
    """Resolve global plus project config, failing closed on unreadable layers."""

    global_settings, error = _read_settings_document(settings_path)
    if error is not None:
        return None, f"global-{error}"
    project, error = _recorded_project_dir(working_dir)
    if error is not None or project is None:
        return None, error
    local_settings, error = _read_settings_document(project / ".amplifier" / "settings.yaml")
    if error is not None:
        return None, f"project-{error}"
    hook_config, error = _hook_config_from_settings(
        _deep_merge_settings(global_settings, local_settings)
    )
    return (
        (_destination_config_from_hook_config(hook_config), None)
        if error is None
        else (None, error)
    )


class _RecoveryKeysEnv:
    """Refresh only values this runner injected; launcher variables always win."""

    def __init__(self) -> None:
        self._injected: dict[str, str] = {}

    def refresh(self) -> None:
        for key, value in tuple(self._injected.items()):
            if os.environ.get(key) == value:
                del os.environ[key]
            self._injected.pop(key, None)
        path = Path.home() / ".amplifier" / "keys.env"
        try:
            content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeError):
            return
        for raw_line in content.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value
            self._injected[key] = value


def resolve_scan_roots(
    destination_config: DestinationConfig, explicit_paths: Iterable[Path] = ()
) -> list[Path]:
    """Resolve the small caller-controlled roots without other-home traversal."""

    roots: list[Path] = []
    for raw in (destination_config.base_path, DEFAULT_BASE_PATH, *explicit_paths):
        path = Path(raw).expanduser().absolute()
        if not _is_other_users_home(path) and path not in roots:
            roots.append(path)
    return roots


def _is_other_users_home(path: Path) -> bool:
    home = Path.home().resolve()
    try:
        relative = path.relative_to(home.parent)
    except ValueError:
        return False
    return not relative.parts or relative.parts[0] != home.name


def _iter_dir_entries(path: Path) -> Iterator[os.DirEntry[str]]:
    """Yield one directory's entries without buffering a discovery generation."""

    try:
        with os.scandir(path) as entries:
            yield from entries
    except OSError:
        return


def discover_native_artifacts(roots: Iterable[Path]) -> Iterator[Path]:
    """File-led exact-layout discovery, without ``rglob`` or global collections."""

    for root in roots:
        root = Path(root)
        try:
            if root.is_file():
                if _is_native_capture_path(root):
                    yield root
                continue
            if not root.is_dir():
                continue
        except OSError:
            continue
        for project in _iter_dir_entries(root):
            if not project.is_dir(follow_symlinks=False):
                continue
            for session in _iter_dir_entries(Path(project.path) / "sessions"):
                if not session.is_dir(follow_symlinks=False):
                    continue
                candidate = Path(session.path) / _NATIVE_FORMAT / "events.jsonl"
                try:
                    if candidate.exists() or candidate.is_symlink():
                        yield candidate
                except OSError:
                    yield candidate


def _is_native_capture_path(path: Path) -> bool:
    return (
        path.name == "events.jsonl"
        and path.parent.name == _NATIVE_FORMAT
        and path.parent.parent.parent.name == "sessions"
    )


def _target_is_outside_owner_home(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path.home().resolve())
    except (OSError, ValueError):
        return True
    return False


def _stat_facts(path: Path) -> tuple[int, int, int]:
    """Return the compact stat facts used only to diagnose immutable sources."""

    try:
        value = path.stat()
    except OSError:
        return 0, 0, 0
    return value.st_size, value.st_mtime_ns, value.st_ino


def _stat_facts_from_result(value: os.stat_result) -> tuple[int, int, int]:
    return value.st_size, value.st_mtime_ns, value.st_ino


def _snapshot_matches(path: Path, row: sqlite3.Row, prefix: str) -> bool:
    try:
        actual = _stat_facts_from_result(path.stat())
    except OSError:
        return False
    return actual == (
        int(row[f"{prefix}_size"]),
        int(row[f"{prefix}_mtime_ns"]),
        int(row[f"{prefix}_inode"]),
    )


def _bounded_readline(handle: Any) -> tuple[bytes | None, str | None]:
    """Read exactly one native record without permitting unbounded allocation."""

    line = handle.readline(_MAX_SOURCE_LINE_BYTES + 1)
    if len(line) > _MAX_SOURCE_LINE_BYTES:
        return None, "oversize-line"
    if not line:
        return None, "end-of-file"
    if not line.endswith(b"\n"):
        return None, "incomplete-line"
    return line, None


def _artifact_id(path: Path) -> str:
    return hashlib.sha256(str(path.absolute()).encode("utf-8")).hexdigest()


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _target_fingerprint(destination: Destination) -> str:
    """Endpoint identity excludes credentials."""

    return hashlib.sha256(
        _canonical_bytes(
            {
                "url": destination.url.rstrip("/"),
                "auth_mode": destination.auth_mode,
                "auth_resource": destination.auth_resource,
            }
        )
    ).hexdigest()


def _logical_target_fingerprint(destination: Destination) -> str:
    """Keep independent configured destinations independent without storing secrets."""

    return hashlib.sha256(
        _canonical_bytes(
            {
                "destination": destination.name,
                "endpoint": _target_fingerprint(destination),
            }
        )
    ).hexdigest()


def _policy_fingerprint(destination: Destination) -> str:
    return hashlib.sha256(
        _canonical_bytes({"include": destination.include, "exclude": destination.exclude})
    ).hexdigest()


def _stream_fingerprint(path: Path, metadata: dict[str, Any]) -> tuple[int, str, str, str | None]:
    """Validate and hash raw lines while retaining no event record or payload."""

    if _target_is_outside_owner_home(path):
        return 0, "", "", "target-outside-owner-home"
    from context_intelligence.prune.coordination import open_capture

    stream = hashlib.sha256()
    source = hashlib.sha256()
    count = 0
    try:
        with open_capture(path, binary=True) as handle:
            while True:
                raw_line, line_error = _bounded_readline(handle)
                if line_error == "end-of-file":
                    break
                if line_error is not None or raw_line is None:
                    return count, "", "", line_error or "incomplete-line"
                source.update(raw_line)
                line_digest = hashlib.sha256(raw_line).hexdigest()
                stream.update(line_digest.encode("ascii"))
                stream.update(b"\n")
                error = _validate_raw_record(raw_line, metadata)
                if error is not None:
                    return count, "", "", error
                count += 1
    except (OSError, UnicodeError):
        return 0, "", "", "inaccessible-file"
    return (
        (count, stream.hexdigest(), source.hexdigest(), None)
        if count
        else (0, "", "", "empty-event-stream")
    )


def _validate_raw_record(raw_line: bytes, metadata: dict[str, Any]) -> str | None:
    body = raw_line.rstrip(b"\r\n")
    if not body:
        return "malformed-line"
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "malformed-line"
    if not isinstance(parsed, dict):
        return "malformed-line"
    if not isinstance(parsed.get("event"), str) or not parsed["event"]:
        return "invalid-event"
    if not isinstance(parsed.get("workspace"), str) or parsed["workspace"] != metadata["workspace"]:
        return "inconsistent-workspace"
    data = parsed.get("data")
    if not isinstance(data, dict):
        return "invalid-data"
    if not isinstance(data.get("timestamp"), str) or not data["timestamp"]:
        return "invalid-timestamp"
    session_id = data.get("session_id")
    return (
        None
        if session_id is None or session_id == metadata["session_id"]
        else "inconsistent-session-id"
    )


def _read_metadata(
    path: Path, session_dir: str, project_dir: str
) -> tuple[dict[str, Any] | None, str, str, str | None]:
    if _target_is_outside_owner_home(path):
        return None, "", "", "target-outside-owner-home"
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "", "", "missing-metadata"
    except OSError:
        return None, "", "", "inaccessible-metadata"
    digest = hashlib.sha256(raw).hexdigest()
    try:
        metadata = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, digest, "", "corrupt-metadata"
    if not isinstance(metadata, dict):
        return None, digest, "", "corrupt-metadata"
    if metadata.get("format") != _NATIVE_FORMAT or metadata.get("version") != _NATIVE_VERSION:
        return None, digest, "", "invalid-native-metadata"
    if metadata.get("session_id") != session_dir:
        return None, digest, "", "inconsistent-session-id"
    if metadata.get("workspace") != project_dir or not project_dir:
        return None, digest, "", "inconsistent-workspace"
    if (
        metadata.get("status") not in _TERMINAL_STATUSES
        or not isinstance(metadata.get("ended_at"), str)
        or not metadata["ended_at"]
    ):
        return None, digest, "", "session-incomplete"
    working_dir = metadata.get("working_dir")
    if not isinstance(working_dir, str) or not working_dir:
        return None, digest, "", "routing-undecidable"
    if not isinstance(metadata.get("parent_id"), str):
        return None, digest, "", "invalid-parent-order"
    try:
        normalized = normalize_match_key(working_dir)
    except (OSError, ValueError):
        return None, digest, "", "routing-undecidable"
    return metadata, digest, normalized, None


class RecoveryCheckpoint:
    """Private V2 state: artifact rows plus per-artifact destination cursors."""

    def __init__(self, state_dir: Path, job_id: str) -> None:
        self.state_dir = state_dir.expanduser()
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.state_dir.chmod(0o700)
        except OSError:
            pass
        self.legacy_checkpoint_present = (self.state_dir / f"{job_id}.sqlite3").exists()
        self.path = self.state_dir / f"{job_id}.v2.sqlite3"
        self._lock_path = self.state_dir / f"{job_id}.v2.sqlite3.lock"
        self._lock_key = str(self._lock_path.absolute())
        if self._lock_key in _HELD_LOCK_PATHS:
            raise RuntimeError(f"recovery job is already locked: {job_id}")
        self._lock_fd = os.open(self._lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            os.close(self._lock_fd)
            raise RuntimeError(f"recovery job is already locked: {job_id}") from error
        _HELD_LOCK_PATHS.add(self._lock_key)
        try:
            self.connection = sqlite3.connect(self.path)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute(f"PRAGMA journal_size_limit = {_JOURNAL_LIMIT_BYTES}")
            self.connection.execute(f"PRAGMA wal_autocheckpoint = {_WAL_AUTOCHECKPOINT_PAGES}")
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS artifact (
                  artifact_id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                  state TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
                  session_id TEXT NOT NULL DEFAULT '', workspace TEXT NOT NULL DEFAULT '',
                  working_dir TEXT NOT NULL DEFAULT '', parent_id TEXT NOT NULL DEFAULT '',
                  record_count INTEGER NOT NULL DEFAULT 0,
                  source_stream_sha256 TEXT NOT NULL DEFAULT '',
                  source_sha256 TEXT NOT NULL DEFAULT '', metadata_sha256 TEXT NOT NULL DEFAULT '',
                  source_size INTEGER NOT NULL DEFAULT 0, source_mtime_ns INTEGER NOT NULL DEFAULT 0,
                  source_inode INTEGER NOT NULL DEFAULT 0, metadata_size INTEGER NOT NULL DEFAULT 0,
                  metadata_mtime_ns INTEGER NOT NULL DEFAULT 0,
                  metadata_inode INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS cursor (
                  artifact_id TEXT NOT NULL REFERENCES artifact(artifact_id),
                  destination TEXT NOT NULL, target_fingerprint TEXT NOT NULL,
                  policy_fingerprint TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
                  byte_offset INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL,
                  retry_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
                  error TEXT NOT NULL DEFAULT '', source_order_key TEXT NOT NULL DEFAULT '',
                  PRIMARY KEY (artifact_id, destination)
                );
                CREATE INDEX IF NOT EXISTS cursor_artifact_target_state
                  ON cursor(artifact_id, target_fingerprint, state);
                CREATE INDEX IF NOT EXISTS artifact_session
                  ON artifact(session_id);
                CREATE INDEX IF NOT EXISTS artifact_parent
                  ON artifact(parent_id);
                CREATE INDEX IF NOT EXISTS artifact_state
                  ON artifact(state);
                """
            )
            existing_artifact_columns = {
                str(row["name"]) for row in self.connection.execute("PRAGMA table_info(artifact)")
            }
            for column in (
                "source_mtime_ns",
                "source_inode",
                "metadata_size",
                "metadata_mtime_ns",
                "metadata_inode",
            ):
                if column not in existing_artifact_columns:
                    self.connection.execute(
                        f"ALTER TABLE artifact ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )
            cursor_columns = {
                str(row["name"]) for row in self.connection.execute("PRAGMA table_info(cursor)")
            }
            if "source_order_key" not in cursor_columns:
                self.connection.execute(
                    "ALTER TABLE cursor ADD COLUMN source_order_key TEXT NOT NULL DEFAULT ''"
                )
            self.connection.execute(
                "UPDATE cursor SET source_order_key=artifact_id WHERE source_order_key=''"
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS cursor_target_pending_order
                ON cursor(target_fingerprint, state, source_order_key, artifact_id)
                """
            )
            self.connection.execute(
                """
                CREATE INDEX IF NOT EXISTS cursor_target_state_retry
                ON cursor(target_fingerprint, state, retry_at)
                """
            )
            self.connection.commit()
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
        except Exception:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            _HELD_LOCK_PATHS.discard(self._lock_key)
            raise

    def close(self) -> None:
        self.connection.close()
        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        os.close(self._lock_fd)
        _HELD_LOCK_PATHS.discard(self._lock_key)

    def prior(self, artifact_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM artifact WHERE artifact_id = ?", (artifact_id,)
        ).fetchone()

    def save_artifact(self, fact: ArtifactFact) -> None:
        self.connection.execute(
            """
            INSERT INTO artifact (
              artifact_id,path,state,reason,session_id,workspace,working_dir,parent_id,
              record_count,source_stream_sha256,source_sha256,metadata_sha256,source_size
              ,source_mtime_ns,source_inode,metadata_size,metadata_mtime_ns,metadata_inode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(artifact_id) DO UPDATE SET
              path=excluded.path,state=excluded.state,reason=excluded.reason,
              session_id=excluded.session_id,workspace=excluded.workspace,
              working_dir=excluded.working_dir,parent_id=excluded.parent_id,
              record_count=excluded.record_count,source_stream_sha256=excluded.source_stream_sha256,
              source_sha256=excluded.source_sha256,metadata_sha256=excluded.metadata_sha256,
              source_size=excluded.source_size,source_mtime_ns=excluded.source_mtime_ns,
              source_inode=excluded.source_inode,metadata_size=excluded.metadata_size,
              metadata_mtime_ns=excluded.metadata_mtime_ns,metadata_inode=excluded.metadata_inode
            """,
            (
                fact.artifact_id,
                str(fact.path),
                fact.state,
                fact.reason,
                fact.session_id,
                fact.workspace,
                fact.working_dir,
                fact.parent_id,
                fact.record_count,
                fact.source_stream_sha256,
                fact.source_sha256,
                fact.metadata_sha256,
                fact.source_size,
                fact.source_mtime_ns,
                fact.source_inode,
                fact.metadata_size,
                fact.metadata_mtime_ns,
                fact.metadata_inode,
            ),
        )

    def reconcile_cursor(
        self, artifact_id: str, destination: str, target: str, policy: str, active: bool
    ) -> None:
        source_order_key = artifact_id
        row = self.connection.execute(
            "SELECT * FROM cursor WHERE artifact_id=? AND destination=?", (artifact_id, destination)
        ).fetchone()
        if not active:
            if row is None:
                self.connection.execute(
                    """
                    INSERT INTO cursor (
                      artifact_id,destination,target_fingerprint,policy_fingerprint,state,source_order_key
                    )
                    VALUES (?, ?, ?, ?, 'filtered', ?)
                    """,
                    (artifact_id, destination, target, policy, source_order_key),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE cursor SET target_fingerprint=?,policy_fingerprint=?,source_order_key=?,state='filtered',
                      retry_at=NULL,error='' WHERE artifact_id=? AND destination=?
                    """,
                    (target, policy, source_order_key, artifact_id, destination),
                )
            return
        if (
            row is None
            or row["target_fingerprint"] != target
            or row["policy_fingerprint"] != policy
        ):
            self.connection.execute(
                """
                INSERT INTO cursor (
                  artifact_id,destination,target_fingerprint,policy_fingerprint,
                  ordinal,byte_offset,state,retry_at,attempts,error,source_order_key
                ) VALUES (?, ?, ?, ?, 0, 0, 'pending', NULL, 0, '', ?)
                ON CONFLICT(artifact_id,destination) DO UPDATE SET
                  target_fingerprint=excluded.target_fingerprint,
                  policy_fingerprint=excluded.policy_fingerprint,ordinal=0,byte_offset=0,
                  state='pending',retry_at=NULL,attempts=0,error='',
                  source_order_key=excluded.source_order_key
                """,
                (artifact_id, destination, target, policy, source_order_key),
            )
        elif row["state"] in {"filtered", "not-required-by-current-policy"}:
            self.connection.execute(
                """
                UPDATE cursor SET ordinal=0,byte_offset=0,state='pending',
                  retry_at=NULL,attempts=0,error='' WHERE artifact_id=? AND destination=?
                """,
                (artifact_id, destination),
            )
        elif row["state"] == "blocked":
            self.connection.execute(
                """
                UPDATE cursor SET state='pending',retry_at=NULL,attempts=0,error=''
                WHERE artifact_id=? AND destination=?
                """,
                (artifact_id, destination),
            )

    def cursor_destinations(self, artifact_id: str) -> Iterator[str]:
        query = "SELECT destination FROM cursor WHERE artifact_id=?"
        for row in self.connection.execute(query, (artifact_id,)):
            yield str(row["destination"])

    def retire_missing_destinations(self, artifact_id: str, names: Iterable[str]) -> None:
        for name in names:
            self.connection.execute(
                """
                UPDATE cursor SET state='not-required-by-current-policy',retry_at=NULL,
                  error='not-required-by-current-policy'
                WHERE artifact_id=? AND destination=?
                """,
                (artifact_id, name),
            )

    def reconcile_invalid_destination(
        self, artifact_id: str, destination: str, target: str, policy: str
    ) -> None:
        """Checkpoint invalid configuration as actionable blocked work, never as a filter."""

        self.connection.execute(
            """
            INSERT INTO cursor (
              artifact_id,destination,target_fingerprint,policy_fingerprint,state,error,source_order_key
            ) VALUES (?, ?, ?, ?, 'blocked', 'invalid-destination', '')
            ON CONFLICT(artifact_id,destination) DO UPDATE SET
              target_fingerprint=excluded.target_fingerprint,
              policy_fingerprint=excluded.policy_fingerprint,
              state='blocked',retry_at=NULL,error='invalid-destination'
            """,
            (artifact_id, destination, target, policy),
        )

    def row_for_target(self, target: str) -> sqlite3.Row | None:
        """Choose one ready cursor from its deterministic, indexed logical-target queue."""

        pending = self.connection.execute(
            _NEXT_CURSOR_SQL,
            (target,),
        ).fetchone()
        if pending is not None:
            return pending
        return self.connection.execute(
            """
            SELECT c.*,a.path,a.session_id,a.workspace,a.working_dir,a.parent_id,a.record_count,
                   a.source_stream_sha256,a.source_sha256,a.metadata_sha256,
                   a.source_size,a.source_mtime_ns,a.source_inode,a.metadata_size,
                   a.metadata_mtime_ns,a.metadata_inode
            FROM cursor c JOIN artifact a ON a.artifact_id=c.artifact_id
            WHERE c.target_fingerprint=? AND c.state='deferred' AND c.retry_at<=?
              AND a.state='eligible'
            ORDER BY c.retry_at,c.source_order_key,c.artifact_id LIMIT 1
            """,
            (target, time.time()),
        ).fetchone()

    def defer_children_pending_parent(self) -> None:
        """Keep children out of selection until an exact parent cursor completes."""

        self.connection.execute(
            """
            UPDATE cursor AS child_cursor
            SET state='waiting-parent'
            FROM artifact AS child
            WHERE child_cursor.artifact_id=child.artifact_id
              AND child_cursor.state='pending'
              AND EXISTS (
                SELECT 1 FROM artifact AS parent
                JOIN cursor AS parent_cursor
                  ON parent_cursor.artifact_id=parent.artifact_id
                 AND parent_cursor.target_fingerprint=child_cursor.target_fingerprint
                 AND parent_cursor.state IN ('pending','deferred','blocked','waiting-parent')
                WHERE parent.session_id=child.parent_id
              )
            """
        )

    def defer_cursor(self, row: sqlite3.Row, retry_at: float, error: str) -> None:
        self.connection.execute(
            """
            UPDATE cursor SET state='deferred',retry_at=?,attempts=attempts+1,error=?
            WHERE artifact_id=? AND destination=? AND state IN ('pending','deferred')
            """,
            (retry_at, error, row["artifact_id"], row["destination"]),
        )

    def set_cursor_state(self, row: sqlite3.Row, state: str, error: str) -> None:
        self.connection.execute(
            """
            UPDATE cursor SET state=?,retry_at=NULL,attempts=attempts+1,error=?
            WHERE artifact_id=? AND destination=? AND state IN ('pending','deferred')
            """,
            (state, error, row["artifact_id"], row["destination"]),
        )

    def advance(self, row: sqlite3.Row, next_offset: int) -> None:
        state = "complete" if int(row["ordinal"]) + 1 >= int(row["record_count"]) else "pending"
        self.connection.execute(
            """
            UPDATE cursor SET ordinal=?,byte_offset=?,state=?,retry_at=NULL,attempts=0,error=''
            WHERE artifact_id=? AND destination=?
            """,
            (int(row["ordinal"]) + 1, next_offset, state, row["artifact_id"], row["destination"]),
        )
        if state == "complete":
            self.connection.execute(
                """
                UPDATE cursor AS child_cursor
                SET state='pending'
                FROM artifact AS child
                WHERE child_cursor.artifact_id=child.artifact_id
                  AND child.parent_id=?
                  AND child_cursor.target_fingerprint=?
                  AND child_cursor.state='waiting-parent'
                """,
                (row["session_id"], row["target_fingerprint"]),
            )

    def update_stat_facts(
        self,
        artifact_id: str,
        source: tuple[int, int, int],
        metadata: tuple[int, int, int],
    ) -> None:
        self.connection.execute(
            """
            UPDATE artifact SET source_size=?,source_mtime_ns=?,source_inode=?,
              metadata_size=?,metadata_mtime_ns=?,metadata_inode=?
            WHERE artifact_id=?
            """,
            (*source, *metadata, artifact_id),
        )

    def quarantine_cursor(self, row: sqlite3.Row, error: str) -> None:
        self.connection.execute(
            """
            UPDATE cursor SET state='quarantined',retry_at=NULL,attempts=attempts+1,error=?
            WHERE artifact_id=? AND destination=?
            """,
            (error, row["artifact_id"], row["destination"]),
        )

    def quarantine_artifact(self, artifact_id: str, error: str) -> None:
        """Stop every route after an immutable source fails revalidation."""

        self.connection.execute(
            """
            UPDATE artifact SET state='quarantined',reason=? WHERE artifact_id=?
            """,
            (error, artifact_id),
        )
        self.connection.execute(
            """
            UPDATE cursor SET state='quarantined',retry_at=NULL,attempts=attempts+1,error=?
            WHERE artifact_id=? AND state IN ('pending','deferred','blocked')
            """,
            (error, artifact_id),
        )

    def artifact_state_from_cursors(self, artifact_id: str) -> tuple[str, str]:
        counts = {
            row["state"]: int(row["count"])
            for row in self.connection.execute(
                "SELECT state,COUNT(*) AS count FROM cursor WHERE artifact_id=? GROUP BY state",
                (artifact_id,),
            )
        }
        if not counts:
            return "blocked", "no-valid-destinations"
        if counts.get("pending", 0) or counts.get("deferred", 0) or counts.get("waiting-parent", 0):
            return "eligible", ""
        if counts.get("blocked", 0):
            return "blocked", "destination-blocked"
        if counts.get("quarantined", 0):
            return "quarantined", "destination-quarantined"
        if set(counts) <= {"filtered"}:
            return "filtered", ""
        if set(counts) <= {"not-required-by-current-policy", "complete", "filtered"}:
            return (
                ("not-required-by-current-policy", "")
                if counts.get("not-required-by-current-policy", 0) and not counts.get("complete", 0)
                else ("accepted", "")
            )
        return "accepted", ""

    def update_artifact_state(self, artifact_id: str, state: str, reason: str) -> None:
        self.connection.execute(
            """
            UPDATE artifact SET state=?,reason=?
            WHERE artifact_id=? AND (state != ? OR reason != ?)
            """,
            (state, reason, artifact_id, state, reason),
        )

    def clear_effective_config_block(self, artifact_id: str) -> None:
        """Allow a repaired effective configuration to derive the artifact state again."""

        self.connection.execute(
            """
            UPDATE artifact SET state='eligible',reason=''
            WHERE artifact_id=? AND state='blocked'
              AND reason LIKE 'effective-config-unavailable:%'
            """,
            (artifact_id,),
        )

    def quarantine_collisions_and_cycles(self) -> None:
        """Quarantine only bounded, provable parent-order failures."""

        self.connection.execute(
            """
            UPDATE artifact SET state='quarantined',reason='session-id-collision'
            WHERE session_id IN (
              SELECT session_id FROM artifact WHERE session_id != ''
              GROUP BY session_id HAVING COUNT(*) > 1
            )
            """
        )
        self.connection.execute(
            """
            UPDATE cursor AS candidate
            SET state='quarantined',retry_at=NULL,error='invalid-parent-order'
            FROM artifact AS artifact
            WHERE candidate.artifact_id=artifact.artifact_id
              AND candidate.state IN ('pending','deferred','blocked','waiting-parent')
              AND artifact.state='eligible'
              AND artifact.parent_id=artifact.session_id
            """
        )
        self.connection.execute(
            """
            UPDATE cursor SET state='quarantined',retry_at=NULL,error='artifact-quarantined'
            WHERE artifact_id IN (
              SELECT artifact_id FROM artifact WHERE state='quarantined'
            ) AND state IN ('pending','deferred','blocked','waiting-parent')
            """
        )
        self.connection.execute(
            """
            UPDATE cursor AS candidate
            SET state='quarantined',retry_at=NULL,error='invalid-parent-order'
            WHERE candidate.state IN ('pending','deferred','waiting-parent')
              AND candidate.target_fingerprint IN (
                SELECT root_cursor.target_fingerprint
                FROM cursor AS root_cursor
                JOIN artifact AS root ON root.artifact_id=root_cursor.artifact_id
                WHERE root_cursor.state IN ('pending','deferred','blocked','waiting-parent')
                  AND root.state='eligible'
                GROUP BY root_cursor.target_fingerprint
                HAVING SUM(
                  CASE WHEN root.parent_id=''
                    OR NOT EXISTS (
                      SELECT 1
                      FROM artifact AS parent INDEXED BY artifact_session
                      CROSS JOIN cursor AS parent_cursor
                        INDEXED BY cursor_artifact_target_state
                      WHERE parent.session_id=root.parent_id
                        AND parent_cursor.artifact_id=parent.artifact_id
                        AND parent_cursor.target_fingerprint=root_cursor.target_fingerprint
                        AND parent_cursor.state IN ('pending','deferred','blocked','waiting-parent')
                        AND parent.state='eligible'
                    )
                  THEN 1 ELSE 0 END
                )=0
              )
            """
        )

    def targets_with_work(self) -> Iterator[str]:
        for row in self.connection.execute(
            """
            SELECT DISTINCT target_fingerprint FROM cursor
            WHERE state IN ('pending','deferred','waiting-parent','blocked','quarantined')
            ORDER BY target_fingerprint
            """,
        ):
            yield str(row["target_fingerprint"])

    def target_has_work(self, target: str) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM cursor
                WHERE target_fingerprint=?
                  AND state IN ('pending','deferred','waiting-parent','blocked','quarantined')
                LIMIT 1
                """,
                (target,),
            ).fetchone()
            is not None
        )

    def target_retry_at(self, target: str) -> float | None:
        row = self.connection.execute(
            """
            SELECT retry_at FROM cursor
            WHERE target_fingerprint=? AND state='deferred'
            ORDER BY retry_at LIMIT 1
            """,
            (target,),
        ).fetchone()
        return None if row is None else float(row["retry_at"])

    def has_unrouted_work(self) -> bool:
        return (
            self.connection.execute(
                """
                SELECT 1 FROM artifact
                WHERE state IN ('deferred','blocked','quarantined')
                LIMIT 1
                """
            ).fetchone()
            is not None
        )

    def counts(self) -> dict[str, int]:
        return {
            str(row["state"]): int(row["count"])
            for row in self.connection.execute(
                "SELECT state,COUNT(*) AS count FROM artifact GROUP BY state"
            )
        }

    def outstanding(self) -> tuple[bool, float | None]:
        row = self.connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM cursor
               WHERE state IN ('pending','deferred','waiting-parent','blocked','quarantined'))
              +(SELECT COUNT(*) FROM artifact
                WHERE state IN ('deferred','blocked','quarantined')) AS work,
              (SELECT MIN(retry_at) FROM cursor WHERE state='deferred') AS retry_at
            """
        ).fetchone()
        return bool(row["work"]), row["retry_at"]

    def commit(self) -> None:
        self.connection.commit()


class RecoveryRunner:
    """Stream inventory and submit at most one source record to each target per run."""

    def __init__(
        self,
        *,
        state_dir: Path,
        job_id: str | None = None,
        settings_path: Path = SETTINGS_PATH,
        client: httpx.Client | None = None,
    ) -> None:
        self.job_id = job_id or str(uuid.uuid4())
        self.settings_path = settings_path
        self.checkpoint = RecoveryCheckpoint(state_dir, self.job_id)
        self.client = client
        self._owns_client = client is None
        self._keys_env = _RecoveryKeysEnv()
        self._target_cache: set[str] = set()
        self._available_targets: set[str] = set()
        self._inventory_cache: dict[str, int] = {}
        self._artifact_state_cache: dict[str, str] = {}
        self._unrouted_pending = False
        self._maintenance_busy: set[str] = set()
        self._routing_config_cache: (
            dict[str, tuple[DestinationConfig | None, str | None]] | None
        ) = None

    def close(self) -> None:
        try:
            self.checkpoint.close()
        finally:
            if self._owns_client and self.client is not None:
                self.client.close()

    def run(
        self,
        explicit_paths: Iterable[Path] = (),
        *,
        dry_run: bool = False,
        refresh: bool = True,
    ) -> RecoverySummary:
        """Optionally refresh the plan, then make bounded one-record target progress."""

        if refresh:
            self._maintenance_busy.clear()
            self._keys_env.refresh()
            self._available_targets.clear()
            try:
                scan_config = load_destination_config(self.settings_path)
            except Exception:  # noqa: BLE001 - malformed settings preserve checkpoint cursors
                scan_config = DestinationConfig({}, (), DEFAULT_BASE_PATH)
            for path in discover_native_artifacts(resolve_scan_roots(scan_config, explicit_paths)):
                self._scan_artifact(path)
            self.checkpoint.quarantine_collisions_and_cycles()
            self._routing_config_cache = {}
            try:
                for row in self.checkpoint.connection.execute(
                    """SELECT artifact_id,working_dir FROM artifact
                       WHERE state IN (
                         'eligible','accepted','filtered','not-required-by-current-policy','blocked'
                       )"""
                ):
                    if str(row["artifact_id"]) not in self._maintenance_busy:
                        self._route(str(row["artifact_id"]), str(row["working_dir"]))
            finally:
                self._routing_config_cache = None
            self.checkpoint.defer_children_pending_parent()
            self.checkpoint.quarantine_collisions_and_cycles()
            self._refresh_artifact_states()
            self.checkpoint.commit()
            self._target_cache = set(self.checkpoint.targets_with_work())
            self._inventory_cache = self.checkpoint.counts()
            self._artifact_state_cache = {
                str(row["artifact_id"]): str(row["state"])
                for row in self.checkpoint.connection.execute(
                    "SELECT artifact_id,state FROM artifact"
                )
            }
            self._unrouted_pending = self.checkpoint.has_unrouted_work()

        delivered = 0
        retry_after: float | None = None
        if not dry_run:
            if self.client is None:
                self.client = httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))
            touched_artifacts: list[str] = []
            for target in tuple(self._target_cache):
                sent, retry, artifact_id = self._deliver_target(self.client, target)
                delivered += sent
                if artifact_id is not None:
                    touched_artifacts.append(artifact_id)
                if retry is not None:
                    retry_after = retry if retry_after is None else min(retry_after, retry)
            self._refresh_artifact_states(touched_artifacts)
            self._refresh_inventory_cache(touched_artifacts)
            self.checkpoint.commit()
            self._unrouted_pending = self.checkpoint.has_unrouted_work()
            for target in tuple(self._target_cache):
                if not self.checkpoint.target_has_work(target):
                    self._target_cache.discard(target)
                    continue
                retry_at = self.checkpoint.target_retry_at(target)
                if retry_at is not None and retry_at > time.time():
                    wait = retry_at - time.time()
                    retry_after = wait if retry_after is None else min(retry_after, wait)
        if self._maintenance_busy:
            retry_after = 1.0 if retry_after is None else min(retry_after, 1.0)
        pending = bool(self._target_cache) or self._unrouted_pending or bool(self._maintenance_busy)
        return RecoverySummary(
            self._inventory_cache,
            delivered,
            pending,
            retry_after,
            self.checkpoint.legacy_checkpoint_present,
        )

    def _scan_artifact(self, path: Path) -> None:
        from context_intelligence.prune.coordination import CaptureBusy, PruneError, replay_guard

        # Preserve the existing storage boundary before opening any sidecar.
        if _target_is_outside_owner_home(path):
            self.checkpoint.save_artifact(
                ArtifactFact(_artifact_id(path), path, "quarantined", "target-outside-owner-home")
            )
            return
        try:
            with replay_guard(path):
                self._scan_unmarked_artifact(path)
        except CaptureBusy:
            # Contention is not a new snapshot. Never erase validated identity,
            # reroute from an empty working_dir, or reset acknowledged offsets.
            self._maintenance_busy.add(_artifact_id(path))
        except (PruneError, OSError) as exc:
            artifact_id = _artifact_id(path)
            updated = self.checkpoint.connection.execute(
                """UPDATE artifact SET state='quarantined',
                   reason=CASE WHEN state='quarantined' AND reason='source-changed'
                               THEN reason ELSE ? END WHERE artifact_id=?""",
                (str(exc), artifact_id),
            )
            if not updated.rowcount:
                self.checkpoint.save_artifact(
                    ArtifactFact(artifact_id, path, "quarantined", str(exc))
                )

    def _scan_unmarked_artifact(self, path: Path) -> None:
        artifact_id = _artifact_id(path)
        if _target_is_outside_owner_home(path):
            self.checkpoint.save_artifact(
                ArtifactFact(artifact_id, path, "quarantined", "target-outside-owner-home")
            )
            return
        try:
            source_stat = path.stat()
            if not stat.S_ISREG(source_stat.st_mode):
                self.checkpoint.save_artifact(
                    ArtifactFact(artifact_id, path, "quarantined", "not-a-regular-file")
                )
                return
        except OSError:
            self.checkpoint.save_artifact(
                ArtifactFact(artifact_id, path, "quarantined", "inaccessible-file")
            )
            return
        metadata_path = path.parent / "metadata.json"
        if _target_is_outside_owner_home(metadata_path):
            self.checkpoint.save_artifact(
                ArtifactFact(artifact_id, path, "quarantined", "target-outside-owner-home")
            )
            return
        prior = self.checkpoint.prior(artifact_id)
        if (
            prior is not None
            and prior["state"]
            in {"eligible", "accepted", "filtered", "not-required-by-current-policy", "blocked"}
            and all(
                prior[column]
                for column in ("source_stream_sha256", "source_sha256", "metadata_sha256")
            )
            and _stat_facts_from_result(source_stat)
            == (
                int(prior["source_size"]),
                int(prior["source_mtime_ns"]),
                int(prior["source_inode"]),
            )
            and _stat_facts(metadata_path)
            == (
                int(prior["metadata_size"]),
                int(prior["metadata_mtime_ns"]),
                int(prior["metadata_inode"]),
            )
        ):
            return
        metadata_before = _stat_facts(metadata_path)
        metadata, metadata_sha, working_dir, metadata_error = _read_metadata(
            metadata_path,
            path.parent.parent.name,
            path.parent.parent.parent.parent.name,
        )
        if metadata_error is not None or metadata is None:
            state = (
                "deferred"
                if metadata_error in {"missing-metadata", "session-incomplete"}
                else "quarantined"
            )
            self.checkpoint.save_artifact(
                ArtifactFact(artifact_id, path, state, metadata_error or "metadata")
            )
            return
        record_count, stream_sha, source_sha, stream_error = _stream_fingerprint(path, metadata)
        metadata_size, metadata_mtime_ns, metadata_inode = _stat_facts(metadata_path)
        source_after = _stat_facts(path)
        if source_after != _stat_facts_from_result(source_stat) or (
            (metadata_size, metadata_mtime_ns, metadata_inode) != metadata_before
        ):
            self.checkpoint.save_artifact(
                ArtifactFact(artifact_id, path, "deferred", "source-changing")
            )
            return
        if stream_error is not None:
            self.checkpoint.save_artifact(
                ArtifactFact(
                    artifact_id,
                    path,
                    "quarantined",
                    stream_error,
                    metadata_sha256=metadata_sha,
                    source_size=source_stat.st_size,
                    source_mtime_ns=source_stat.st_mtime_ns,
                    source_inode=source_stat.st_ino,
                    metadata_size=metadata_size,
                    metadata_mtime_ns=metadata_mtime_ns,
                    metadata_inode=metadata_inode,
                )
            )
            return
        changed = prior is not None and (
            prior["source_sha256"] not in {"", source_sha}
            or prior["metadata_sha256"] not in {"", metadata_sha}
        )
        permanently_changed = (
            prior is not None
            and prior["state"] == "quarantined"
            and prior["reason"] == "source-changed"
        )
        if changed or permanently_changed:
            self.checkpoint.save_artifact(
                ArtifactFact(
                    artifact_id,
                    path,
                    "quarantined",
                    "source-changed",
                    metadata["session_id"],
                    metadata["workspace"],
                    working_dir,
                    metadata["parent_id"],
                    record_count,
                    stream_sha,
                    source_sha,
                    metadata_sha,
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                    source_stat.st_ino,
                    metadata_size,
                    metadata_mtime_ns,
                    metadata_inode,
                )
            )
            self.checkpoint.quarantine_artifact(artifact_id, "source-changed")
            return
        self.checkpoint.save_artifact(
            ArtifactFact(
                artifact_id,
                path,
                "eligible",
                "",
                metadata["session_id"],
                metadata["workspace"],
                working_dir,
                metadata["parent_id"],
                record_count,
                stream_sha,
                source_sha,
                metadata_sha,
                source_stat.st_size,
                source_stat.st_mtime_ns,
                source_stat.st_ino,
                metadata_size,
                metadata_mtime_ns,
                metadata_inode,
            )
        )

    def _effective_route_config(
        self, working_dir: str
    ) -> tuple[DestinationConfig | None, str | None]:
        cache = self._routing_config_cache
        if cache is not None and working_dir in cache:
            return cache[working_dir]
        try:
            config, error = load_effective_destination_config(
                working_dir.removesuffix("/"), self.settings_path
            )
        except Exception:  # noqa: BLE001 - retain cursors when configuration cannot be read
            config, error = None, "configuration-unreadable"
        if cache is not None:
            cache[working_dir] = config, error
        return config, error

    def _route(self, artifact_id: str, working_dir: str) -> None:
        config, error = self._effective_route_config(working_dir)
        if error is not None or config is None:
            self.checkpoint.update_artifact_state(
                artifact_id, "blocked", f"effective-config-unavailable:{error or 'unknown'}"
            )
            return
        self.checkpoint.clear_effective_config_block(artifact_id)
        current_names: set[str] = set()
        for name, destination in config.destinations.items():
            current_names.add(name)
            target = _logical_target_fingerprint(destination)
            policy = _policy_fingerprint(destination)
            try:
                active = destination_is_active(destination, working_dir)
            except (TypeError, ValueError):
                active = False
            self.checkpoint.reconcile_cursor(artifact_id, name, target, policy, active)
        for name in config.invalid_names:
            current_names.add(name)
            invalid = hashlib.sha256(f"invalid:{name}".encode()).hexdigest()
            self.checkpoint.reconcile_invalid_destination(artifact_id, name, invalid, invalid)
        self.checkpoint.retire_missing_destinations(
            artifact_id,
            (
                name
                for name in self.checkpoint.cursor_destinations(artifact_id)
                if name not in current_names
            ),
        )

    def _refresh_artifact_states(self, artifact_ids: Iterable[str] | None = None) -> None:
        query = "SELECT artifact_id,state,reason FROM artifact"
        params: tuple[str, ...] = ()
        if artifact_ids is not None:
            params = tuple(dict.fromkeys(artifact_ids))
            if not params:
                return
            query += f" WHERE artifact_id IN ({','.join('?' for _ in params)})"
        for row in self.checkpoint.connection.execute(query, params):
            if row["state"] == "quarantined" or (
                row["state"] == "blocked"
                and str(row["reason"]).startswith("effective-config-unavailable:")
            ):
                continue
            artifact_id = str(row["artifact_id"])
            state, reason = self.checkpoint.artifact_state_from_cursors(artifact_id)
            if (state, reason) != (str(row["state"]), str(row["reason"])):
                self.checkpoint.update_artifact_state(artifact_id, state, reason)

    def _refresh_inventory_cache(self, artifact_ids: Iterable[str]) -> None:
        """Update only artifacts whose delivery attempt may have changed their state."""

        for artifact_id in dict.fromkeys(artifact_ids):
            row = self.checkpoint.connection.execute(
                "SELECT state FROM artifact WHERE artifact_id=?", (artifact_id,)
            ).fetchone()
            if row is None:
                continue
            state = str(row["state"])
            previous = self._artifact_state_cache.get(artifact_id)
            if previous == state:
                continue
            if previous is not None:
                remaining = self._inventory_cache.get(previous, 0) - 1
                if remaining:
                    self._inventory_cache[previous] = remaining
                else:
                    self._inventory_cache.pop(previous, None)
            self._inventory_cache[state] = self._inventory_cache.get(state, 0) + 1
            self._artifact_state_cache[artifact_id] = state

    def _deliver_target(
        self, client: httpx.Client, target: str
    ) -> tuple[int, float | None, str | None]:
        from context_intelligence.prune.coordination import CaptureBusy, PruneError, replay_guard

        row = self.checkpoint.row_for_target(target)
        if row is None:
            return 0, None, None
        artifact_id = str(row["artifact_id"])
        path = Path(str(row["path"]))
        if _target_is_outside_owner_home(path):
            self.checkpoint.quarantine_artifact(artifact_id, "target-outside-owner-home")
            return 0, None, artifact_id
        try:
            with replay_guard(path):
                return self._deliver_unmarked_target(client, target)
        except CaptureBusy:
            self.checkpoint.defer_cursor(row, time.time() + 1, "capture-maintenance-busy")
            return 0, 1, artifact_id
        except (PruneError, OSError) as exc:
            self.checkpoint.quarantine_artifact(artifact_id, str(exc))
            return 0, None, artifact_id

    def _deliver_unmarked_target(
        self, client: httpx.Client, target: str
    ) -> tuple[int, float | None, str | None]:
        row = self.checkpoint.row_for_target(target)
        if row is None:
            return 0, None, None
        artifact_id = str(row["artifact_id"])
        try:
            config, error = load_effective_destination_config(
                str(row["working_dir"]).removesuffix("/"), self.settings_path
            )
        except Exception:  # noqa: BLE001 - preserve an existing cursor on config failure
            config, error = None, "configuration-unreadable"
        current = config.destinations.get(str(row["destination"])) if config is not None else None
        if error is not None or current is None:
            return 0, None, artifact_id
        if (
            _logical_target_fingerprint(current) != target
            or _policy_fingerprint(current) != row["policy_fingerprint"]
            or not destination_is_active(current, str(row["working_dir"]))
        ):
            self._route(str(row["artifact_id"]), str(row["working_dir"]))
            return 0, None, artifact_id
        try:
            headers = self._destination_headers(current)
        except Exception:  # noqa: BLE001 - authentication is an external seam
            self.checkpoint.set_cursor_state(row, "blocked", "authentication-unavailable")
            return 0, None, artifact_id
        if target in self._available_targets:
            capability, retry = "available", None
        else:
            capability, retry = self._has_recovery_capability(client, current, headers)
            if capability == "available":
                self._available_targets.add(target)
        if capability == "throttled":
            delay = retry or 1.0
            self.checkpoint.defer_cursor(row, time.time() + delay, "throttled")
            return 0, delay, artifact_id
        if capability == "temporary":
            delay = min(_MAX_BACKOFF_S, 2.0 ** min(int(row["attempts"]) + 1, 10))
            self.checkpoint.defer_cursor(row, time.time() + delay, "temporary")
            return 0, delay, artifact_id
        if capability != "available":
            self.checkpoint.set_cursor_state(row, "blocked", capability)
            return 0, None, artifact_id
        body, next_offset, error = self._reconstruct_next(row)
        if error is not None or body is None:
            self.checkpoint.quarantine_artifact(row["artifact_id"], error or "source-changed")
            return 0, None, artifact_id
        try:
            response = client.post(
                f"{current.url.rstrip('/')}/recovery/events",
                content=body,
                headers={**headers, "Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            delay = min(_MAX_BACKOFF_S, 2.0 ** min(int(row["attempts"]) + 1, 10))
            self.checkpoint.defer_cursor(row, time.time() + delay, "transport")
            return 0, delay, artifact_id
        if response.status_code == 202:
            self.checkpoint.advance(row, next_offset)
            return 1, None, artifact_id
        if response.status_code in {401, 403, 404}:
            self._available_targets.discard(target)
        if response.status_code == 429:
            delay = _retry_after(response)
            self.checkpoint.defer_cursor(row, time.time() + delay, "throttled")
            return 0, delay, artifact_id
        if 500 <= response.status_code <= 599:
            delay = min(_MAX_BACKOFF_S, 2.0 ** min(int(row["attempts"]) + 1, 10))
            self.checkpoint.defer_cursor(row, time.time() + delay, f"http-{response.status_code}")
            return 0, delay, artifact_id
        if response.status_code in {400, 409, 422}:
            self.checkpoint.quarantine_cursor(row, f"http-{response.status_code}")
            return 0, None, artifact_id
        self.checkpoint.set_cursor_state(row, "blocked", f"http-{response.status_code}")
        return 0, None, artifact_id

    def _revalidate_changed_snapshot(
        self, row: sqlite3.Row, path: Path, metadata_path: Path
    ) -> tuple[tuple[int, int, int] | None, tuple[int, int, int] | None, str | None]:
        """Hash a changed-stat artifact once before permitting an existing cursor read."""

        source_before = _stat_facts(path)
        metadata_before = _stat_facts(metadata_path)
        metadata, metadata_sha, _working_dir, metadata_error = _read_metadata(
            metadata_path,
            path.parent.parent.name,
            path.parent.parent.parent.parent.name,
        )
        if metadata_error is not None or metadata is None:
            return None, None, "source-changed"
        count, stream_sha, source_sha, error = _stream_fingerprint(path, metadata)
        source_after = _stat_facts(path)
        metadata_after = _stat_facts(metadata_path)
        if source_before != source_after or metadata_before != metadata_after:
            return None, None, "source-changing"
        if (
            error is not None
            or metadata_sha != row["metadata_sha256"]
            or count != row["record_count"]
            or stream_sha != row["source_stream_sha256"]
            or source_sha != row["source_sha256"]
        ):
            return None, None, "source-changed"
        self.checkpoint.update_stat_facts(str(row["artifact_id"]), source_after, metadata_after)
        return source_after, metadata_after, None

    def _reconstruct_next(self, row: sqlite3.Row) -> tuple[bytes | None, int, str | None]:
        """Read one bounded line through a stat-verified source descriptor."""

        path = Path(str(row["path"]))
        metadata_path = path.parent / "metadata.json"
        if _target_is_outside_owner_home(path) or _target_is_outside_owner_home(metadata_path):
            return None, 0, "target-outside-owner-home"
        source_facts: tuple[int, int, int] | None = (
            int(row["source_size"]),
            int(row["source_mtime_ns"]),
            int(row["source_inode"]),
        )
        metadata_facts: tuple[int, int, int] | None = (
            int(row["metadata_size"]),
            int(row["metadata_mtime_ns"]),
            int(row["metadata_inode"]),
        )
        if not _snapshot_matches(path, row, "source") or not _snapshot_matches(
            metadata_path, row, "metadata"
        ):
            source_facts, metadata_facts, refresh_error = self._revalidate_changed_snapshot(
                row, path, metadata_path
            )
            if refresh_error is not None or source_facts is None or metadata_facts is None:
                return None, 0, refresh_error or "source-changed"
        if source_facts is None or metadata_facts is None:
            return None, 0, "source-changed"
        metadata = {"session_id": row["session_id"], "workspace": row["workspace"]}
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                if _stat_facts_from_result(os.fstat(handle.fileno())) != source_facts:
                    refresh_error = self._revalidate_changed_snapshot(row, path, metadata_path)[2]
                    return None, 0, refresh_error or "source-changing"
                handle.seek(int(row["byte_offset"]))
                raw_line, read_error = _bounded_readline(handle)
                next_offset = handle.tell()
                if read_error is not None or raw_line is None:
                    return None, 0, read_error or "source-changed"
                line_digest = hashlib.sha256(raw_line).hexdigest()
                if (
                    _stat_facts_from_result(os.fstat(handle.fileno())) != source_facts
                    or _stat_facts(path) != source_facts
                    or _stat_facts(metadata_path) != metadata_facts
                ):
                    refresh_error = self._revalidate_changed_snapshot(row, path, metadata_path)[2]
                    return None, 0, refresh_error or "source-changing"
        except OSError:
            return None, 0, "inaccessible-file"
        error = _validate_raw_record(raw_line, metadata)
        if error is not None:
            return None, 0, error
        try:
            parsed = json.loads(raw_line.rstrip(b"\r\n").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None, 0, "malformed-line"
        data = dict(parsed["data"])
        data.setdefault("session_id", metadata["session_id"])
        payload = build_payload(
            parsed["event"], parsed["workspace"], data, str(row["working_dir"]).removesuffix("/")
        )
        payload["origin"] = {
            "session_id": metadata["session_id"],
            "ordinal": int(row["ordinal"]),
            "source_line_sha256": line_digest,
            "source_stream_sha256": row["source_stream_sha256"],
        }
        return _canonical_bytes(payload), next_offset, None

    @staticmethod
    def _destination_headers(destination: Destination) -> dict[str, str]:
        return build_auth_strategy(
            auth_mode=destination.auth_mode,
            api_key=destination.api_key,
            auth_resource=destination.auth_resource,
        ).headers()

    @staticmethod
    def _has_recovery_capability(
        client: httpx.Client, destination: Destination, headers: dict[str, str]
    ) -> tuple[str, float | None]:
        try:
            response = client.get(f"{destination.url.rstrip('/')}/version", headers=headers)
        except httpx.HTTPError:
            return "temporary", None
        if response.status_code == 429:
            return "throttled", _retry_after(response)
        if 500 <= response.status_code <= 599:
            return "temporary", None
        if response.status_code in {401, 403, 404}:
            return f"http-{response.status_code}", None
        if not 200 <= response.status_code < 300:
            return "capability-unavailable", None
        try:
            capabilities = response.json().get("capabilities", [])
        except (ValueError, AttributeError):
            return "capability-unavailable", None
        return (
            ("available", None)
            if (isinstance(capabilities, list) and "native-recovery" in capabilities)
            else ("capability-unavailable", None)
        )


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(0.0, float(response.headers.get("Retry-After", "1")))
    except (TypeError, ValueError):
        return 1.0
