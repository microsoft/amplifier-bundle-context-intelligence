# Public event ingestion: contract and live evidence

Applications need to send their own event streams without mounting a session
telemetry hook or importing a module's private uploader. This additive change
provides a pure `build_event_payload` helper and `AsyncCIClient.ingest` using the
existing server/auth contract. Callers own routing, consent, redaction, local
retention, fan-out and retry scheduling. No bundle, agent, skill or mode changes.

## Acceptance contract

- The helper produces a detached JSON envelope and the hook-compatible
  `aci-event-v1` content hash. Distinct occurrences need distinct stable identity
  in `data`. JSON containing NaN/Infinity is rejected. Unknown `working_dir` is
  omitted; a supplied directory is outside the v1 hash, as in the hook.
- The client snapshots the supplied envelope before refreshing auth, posts once,
  does not follow redirects, and returns the server's HTTP 202 receipt. Custom
  compatible envelopes and keys are allowed. Existing auth strategies and
  `CIClientError` classifications are reused; blocking token refresh runs outside
  the event loop.
- `queued` means the server accepted a durable queue append. `duplicate` means
  its deduplication cache recognized the key. Neither means graph indexing is
  complete. There is no automatic retry or exactly-once guarantee.
- Current server handlers require `data.timestamp`. Session-scoped authorization
  and graph ingestion also require a nonblank `data.session_id`; generic events
  without a session may be accepted by compatibility mode but not indexed. The
  helper deliberately does not invent either field.

## Real seam run

[Captured evidence](evidence/live.json) scores five scenarios through the actual
public API, real HTTP/auth and Neo4j. All five passed: first `queued` receipt,
second `duplicate` receipt, exactly one matching graph event, explicit HTTP 401
for a bad key, and `connection_error` for a stopped endpoint. All event data is
synthetic. There are no credentials, prompts or personal session contents in the
evidence. The tests' receipt doubles were reconciled with these responses.

Provenance:

- Library starting commit: `87894048f8b68519f61aea52306d907b04f4bdfb` plus this
  change; exact client/helper source SHA-256 values are in the evidence.
- Server: `microsoft/amplifier-context-intelligence` commit
  `43973967ff4bc9a02422814a8c00dfce7624a76d`, clean checkout, single uvicorn
  process at `http://127.0.0.1:18081`, explicit isolated storage/config.
- Neo4j: official `neo4j:5.26.22-community` image with bundled APOC,
  image ID `sha256:24b071534c7cfe9718689041ab9aafea2cd0d88af9ea58b768cb5eed381ab2d0`.
  A dedicated container exposed Bolt on 17687 and HTTP on 17474.
- Auth: a dedicated synthetic static identity. Entra refresh is covered by
  strategy tests; this run did not authenticate against Azure.

Reproduce against an explicitly provisioned isolated server, with write/query
access to the `ci-public-api-fixture` workspace:

```bash
uv run --frozen python scripts/verify-event-ingestion.py \
  --server-url http://127.0.0.1:18081 \
  --token-file /private/path/to/token \
  --server-revision <verified-server-commit> \
  --output /private/path/to/evidence.json
```

The harness uses a unique session/event, queries only that session, and prints
pass/fail scores without credentials or arbitrary server error text. The default
network-failure endpoint is `127.0.0.1:1`; override `--unavailable-url` if needed.
It does not provision services, stop them, read user configuration or run models.

## Server limitation discovered at this seam

At the server revision above, `context_intelligence_server/main.py:1501–1510`
calls `idempotency_cache.check_and_store` before the durable queue append at
line 1533. If that first append fails, a later request with the same key can
receive `duplicate` even though that attempt never appended. A duplicate receipt
therefore cannot independently establish durability. The client exposes the
receipt without promoting it to `queued` or silently replaying with a new key.
This is a source-level failure-window finding; the live run did not inject a disk
append failure or test server crash/restart durability. It requires a separate
server-side correction and fault-injection test.

## Validation

- `uv run --frozen pytest tests -q`: 880 passed.
- Query module, `PYTHONPATH=../.. uv run --frozen pytest -q`: 195 passed.
- `uv run --frozen ruff check .`, `ruff format --check .`, `pyright`: clean.
- `uv build --no-sources`: sdist and wheel built. The wheel's `[client]` extra
  installed in a fresh environment; an isolated `python -I` imported both APIs.
- Five real seam scenarios passed, as captured above.
- `scripts/validate-full.sh`: exit 0, `validation_mode: full`, adjudicated
  **PASS WITH WARNINGS**. Its mechanical FAIL is the known mode-advertising
  false positive; the review also confirmed the standalone README install
  convention is intentional. The existing `server-data-ops` agent description
  exceeds the cosmetic length recommendation. Those unrelated files are
  unchanged. The recipe lacks `pip wheel`; the separate successful `uv build`
  and installed-wheel smoke cover package construction. The validator's
  generated overview diagram changes are excluded because this change does not
  alter bundle structure.

Test environment notes: bare root `pytest` collects independently packaged module
suites and currently fails collection due to missing module dependencies and
colliding `tests.conftest` names. The root's intended `tests/` suite was selected
explicitly. The query module's lock pins an older library revision without the
existing `CIClientError` class, so `PYTHONPATH=../..` selects this checkout for the
required module suite. Neither unrelated setup issue was changed here. Frozen
uv commands preserve the repository's public-index lock in environments with
an externally configured default package index.

The current recipe runner injects the CLI interpreter as `AMPLIFIER_PYTHON`, so
the helper's PATH-only dependency environment did not supply hatchling on the
first run (`full_no_build`). For the successful rerun, hatchling was installed
into `/tmp/amplifier-ci-validator-extra` with `uv pip install --target`, and
`PYTHONPATH` pointed there for this command only. No installed CLI environment
was changed. Recipe version: 3.15.0; recipe SHA-256:
`dd1f7b7e87cd384bc8f49d9545cd641a52287f2b9f7860985e99493a9cd6cb5d`.
