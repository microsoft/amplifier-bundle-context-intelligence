"""Destructive authority checks use real SQLite jobs and owned synthetic captures."""

from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from context_intelligence import config
from context_intelligence.prune import PruneSummary, prune_file
from context_intelligence.prune import recovery
from context_intelligence.prune.coordination import receipt_path
from test_prune_ledger_resources import BODY, NOW, _ledger, _request


@pytest.fixture
def authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / "settings.yaml")
    monkeypatch.setattr(config, "DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard")
    monkeypatch.delenv("PR143_AUTHORITY_TEST", raising=False)
    return tmp_path


def set_authority(value: object) -> None:
    config.SETTINGS_PATH.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: {json.dumps(value)}\n",
        encoding="utf-8",
    )


def capture(root: Path) -> Path:
    source = root / "events.jsonl"
    source.write_bytes(BODY)
    return source


def assert_preserved(source: Path) -> None:
    assert source.read_bytes() == BODY
    assert not receipt_path(source).exists()


@pytest.mark.parametrize("member_root", ["standard", "configured", "extra"])
def test_standard_configured_and_explicit_members_all_exclude(
    authority: Path, member_root: str
) -> None:
    source = capture(authority)
    roots = {name: authority / name for name in ("standard", "configured", "extra")}
    set_authority(str(roots["configured"]))
    for name, root in roots.items():
        _ledger(root, 0, source if name == member_root else authority / f"unrelated-{name}")
    result = prune_file(source, _request(authority, recovery_roots=(roots["extra"],)), now=NOW)
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert_preserved(source)


def test_canonical_duplicates_are_inventoried_once(authority: Path) -> None:
    source = capture(authority)
    roots = [authority / name for name in ("standard", "configured", "extra")]
    set_authority("~/configured")
    dbs = [_ledger(root, 0, authority / "unrelated") for root in roots]
    with patch.object(recovery, "_exclude_registered", wraps=recovery._exclude_registered) as read:
        result = prune_file(
            source,
            _request(authority, recovery_roots=(*roots, Path("~/configured"))),
            now=NOW,
        )
    assert result.status == "pruned"
    assert read.call_count == len(dbs) == 3
    assert json.loads(source.read_bytes())["data"] == {"usage": {"tokens": 4}}
    assert receipt_path(source).is_file()


@pytest.mark.parametrize("explicit", ["standard", "configured"])
def test_explicit_duplicate_of_missing_implicit_root_stays_required(
    authority: Path, explicit: str
) -> None:
    source = capture(authority)
    set_authority(str(authority / "configured"))
    result = prune_file(
        source, _request(authority, recovery_roots=(authority / explicit,)), now=NOW
    )
    assert result.status == "error"
    assert (result.reason or "").startswith("recovery-state-missing:")
    assert_preserved(source)
    assert not (authority / explicit).exists()


@pytest.mark.parametrize("root_name", ["standard", "configured"])
@pytest.mark.parametrize("fault", ["schema", "busy"])
def test_unknown_or_busy_implicit_root_refuses(authority: Path, root_name: str, fault: str) -> None:
    source = capture(authority)
    set_authority(str(authority / "configured"))
    db = _ledger(authority / root_name, 0, authority / "unrelated")
    if fault == "schema":
        with closing(sqlite3.connect(db)) as connection:
            connection.execute("ALTER TABLE cursor ADD COLUMN unknown TEXT")
            connection.commit()
    with Path(str(db) + ".lock").open("rb") as held:
        if fault == "busy":
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = prune_file(source, _request(authority), now=NOW)
    assert result.status == "error"
    assert (result.reason or "").startswith("recovery-state-")
    assert_preserved(source)


@pytest.mark.parametrize("unsafe", ["symlink", "parent-spelling"])
def test_unsafe_root_is_not_hidden_by_canonical_dedup(authority: Path, unsafe: str) -> None:
    source = capture(authority)
    root = authority / "configured"
    root.mkdir()
    set_authority(str(root))
    alias = authority / "alias"
    if unsafe == "symlink":
        alias.symlink_to(root, target_is_directory=True)
    else:
        alias = root / ".." / "configured"
    result = prune_file(source, _request(authority, recovery_roots=(alias,)), now=NOW)
    assert result.status == "error"
    assert (result.reason or "").startswith("recovery-state-unsafe:")
    assert_preserved(source)


@pytest.mark.parametrize(
    "damage",
    [
        "malformed",
        "unreadable",
        "invalid-utf8",
        "directory",
        "nonregular",
        "dangling-settings",
        "document-shape",
        "overrides-shape",
        "hook-shape",
        "config-shape",
        "boolean",
        "number",
        "sequence",
        "mapping",
        "relative",
        "missing-env",
        "embedded-missing-env",
        "unresolved",
    ],
)
def test_unknown_authority_configuration_refuses_before_pruning(
    authority: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, damage: str
) -> None:
    source = capture(authority)
    known = authority / "configured"
    db = _ledger(known, 0, source)
    lock = Path(str(db) + ".lock")
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)]
    values: dict[str, object] = {
        "boolean": True,
        "number": 42,
        "sequence": [str(known)],
        "mapping": {"path": str(known)},
        "relative": "SYNTHETIC-CONFIG-SENTINEL/relative",
        "missing-env": "${PR143_AUTHORITY_TEST:}",
        "embedded-missing-env": str(authority) + "/${PR143_AUTHORITY_TEST}/state",
        "unresolved": "${SYNTHETIC-CONFIG-SENTINEL",
    }
    set_authority(values.get(damage, str(known)))
    texts = {
        "malformed": "overrides: [SYNTHETIC-CONFIG-SENTINEL\n",
        "document-shape": "[]\n",
        "overrides-shape": "overrides: null\n",
        "hook-shape": "overrides:\n  hook-context-intelligence: []\n",
        "config-shape": "overrides:\n  hook-context-intelligence:\n    config: 12\n",
    }
    if damage in texts:
        config.SETTINGS_PATH.write_text(texts[damage], encoding="utf-8")
    elif damage == "invalid-utf8":
        config.SETTINGS_PATH.write_bytes(b"\xff")
    elif damage == "directory":
        config.SETTINGS_PATH.unlink()
        config.SETTINGS_PATH.mkdir()
    elif damage == "nonregular":
        config.SETTINGS_PATH.unlink()
        os.mkfifo(config.SETTINGS_PATH)
    elif damage == "dangling-settings":
        config.SETTINGS_PATH.unlink()
        config.SETTINGS_PATH.symlink_to(authority / "absent-target")
    elif damage == "unreadable":
        real_read = Path.read_text

        def deny(path: Path, *args, **kwargs):
            if path == config.SETTINGS_PATH:
                raise PermissionError("SYNTHETIC-CONFIG-SENTINEL")
            return real_read(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", deny)
    # Supplying another known root must not mask unknown current authority.
    extras = (known,) if damage == "unreadable" else ()
    caplog.set_level("DEBUG", logger="context_intelligence.config")
    result = prune_file(source, _request(authority, recovery_roots=extras), now=NOW)
    assert result.status == "error"
    assert result.reason == "recovery-state-unknown: invalid authority configuration"
    summary = PruneSummary()
    summary.add(result)
    assert summary.exit_code == 1 and summary.changed_files == 0
    assert_preserved(source)
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)] == before
    assert "SYNTHETIC-CONFIG-SENTINEL" not in caplog.text
    assert str(known) not in caplog.text


@pytest.mark.parametrize(
    "document",
    [
        None,
        "",
        "# comment only\n",
        "---\n",
        "{}",
        "unrelated: []",
        "overrides: {}",
        "overrides:\n  hook-context-intelligence:\n    config: {}",
        "overrides:\n  hook-context-intelligence:\n    config:\n      recovery_state_root: null",
        "overrides:\n  hook-context-intelligence:\n    config:\n      recovery_state_root: '   '",
    ],
)
def test_absent_or_unconfigured_settings_permit_prune_without_creating_state(
    authority: Path, document: str | None
) -> None:
    source = capture(authority)
    if document is not None:
        config.SETTINGS_PATH.write_text(document, encoding="utf-8")
    result = prune_file(source, _request(authority), now=NOW)
    assert result.status == "pruned"
    assert receipt_path(source).is_file()
    assert not config.DEFAULT_RECOVERY_STATE_ROOT.exists()


@pytest.mark.parametrize("spelling", ["absolute", "home", "env", "default"])
def test_valid_authority_expansion_excludes_member(
    authority: Path, monkeypatch: pytest.MonkeyPatch, spelling: str
) -> None:
    source = capture(authority)
    root = authority / "configured"
    _ledger(root, 0, source)
    if spelling == "env":
        monkeypatch.setenv("PR143_AUTHORITY_TEST", str(root))
    set_authority(
        {
            "absolute": str(root),
            "home": "~/configured",
            "env": "${PR143_AUTHORITY_TEST}",
            "default": "${PR143_AUTHORITY_TEST:" + str(root) + "}",
        }[spelling]
    )
    result = prune_file(source, _request(authority), now=NOW)
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert_preserved(source)


def test_empty_env_does_not_select_nonempty_default(
    authority: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = capture(authority)
    monkeypatch.setenv("PR143_AUTHORITY_TEST", "")
    set_authority("${PR143_AUTHORITY_TEST:" + str(authority / "configured") + "}")
    result = prune_file(source, _request(authority), now=NOW)
    assert result.status == "error"
    assert result.reason == "recovery-state-unknown: invalid authority configuration"
    assert_preserved(source)


def test_missing_parser_refuses_existing_file_but_not_absent_settings(authority: Path) -> None:
    source = capture(authority)
    set_authority(str(authority / "configured"))
    with patch.dict(sys.modules, {"yaml": None}):
        result = prune_file(source, _request(authority), now=NOW)
        assert result.status == "error"
        assert result.reason == "recovery-state-unknown: invalid authority configuration"
        assert_preserved(source)
        config.SETTINGS_PATH.unlink()
        assert prune_file(source, _request(authority), now=NOW).status == "pruned"


def test_absent_settings_still_excludes_standard_job(authority: Path) -> None:
    source = capture(authority)
    _ledger(config.DEFAULT_RECOVERY_STATE_ROOT, 0, source)
    result = prune_file(source, _request(authority), now=NOW)
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert_preserved(source)


def test_strict_reader_honors_explicit_home_relative_settings_path(
    authority: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_authority("~/configured")
    monkeypatch.setattr(config, "SETTINGS_PATH", authority / "absent.yaml")
    assert (
        config.strict_global_recovery_state_root(Path("~/settings.yaml"))
        == authority / "configured"
    )


@pytest.mark.parametrize("expansion", ["env", "default"])
def test_trailing_space_expansion_keeps_registered_authority(
    authority: Path, monkeypatch: pytest.MonkeyPatch, expansion: str
) -> None:
    source = capture(authority)
    root = authority / "configured "
    db = _ledger(root, 0, source)
    lock = Path(str(db) + ".lock")
    if expansion == "env":
        monkeypatch.setenv("PR143_AUTHORITY_TEST", str(root))
        set_authority("${PR143_AUTHORITY_TEST:}")
    else:
        set_authority("${PR143_AUTHORITY_TEST:" + str(root) + "}")
    legacy = config.global_recovery_state_root()
    strict = config.strict_global_recovery_state_root()
    assert legacy == root
    assert not (authority / "configured").exists()

    def checkpoint_rows():
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
            return (
                list(connection.execute("SELECT * FROM artifact")),
                list(connection.execute("SELECT * FROM cursor")),
            )

    rows_before = checkpoint_rows()
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)]
    result = prune_file(source, _request(authority), now=NOW)
    summary = PruneSummary()
    summary.add(result)
    print(
        f"trailing-space-{expansion}: status={result.status} "
        f"changed={summary.changed_files} exit={summary.exit_code} "
        f"receipt={receipt_path(source).exists()} source_preserved={source.read_bytes() == BODY}"
    )
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert strict == legacy == root
    assert summary.changed_files == 0 and summary.exit_code == 0
    assert_preserved(source)
    assert checkpoint_rows() == rows_before
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)] == before
