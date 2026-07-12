# Workstream 1 — Multi-Source Query Tool Fail-Loud Fix

**Status:** Ready for implementation
**Scope:** `tool-context-intelligence-query` module only (`graph_query` + `blob_read` tools) and its
shared `ToolConfigResolver` in `context_intelligence/tool_resolver.py`.
**Source:** Council consensus (6-lens adversarial review, one debate round) on the documented-but-unsafe
`sources` first-entry-only behavior at `README.md:431`.
**Explicitly parked (do NOT implement here):** full multi-source fan-out/federation, a "list configured
sources" discovery API, cross-source reconciliation/merge logic, and the separate generic
non-bundle file-capture workstream.

---

## 0. Current-state summary (verified against `main`)

| Fact | Where |
|---|---|
| `ToolConfigResolver.sources` already parses a **genuinely multi-entry** `dict[str, Source]` — the single-entry limitation is purely in how it's *consumed*, not how it's *parsed*. | `context_intelligence/tool_resolver.py:329-387` |
| `_first_entry(mapping)` always returns `next(iter(mapping.values()), None)` — silently picks insertion-order-first regardless of how many entries exist. | `context_intelligence/tool_resolver.py:114-124` |
| `resolve_query_endpoint()` and `resolve_query_auth_strategy()` both call `_first_entry(tool_resolver.sources)` directly — this is the sole call site of the "only first entry" behavior for the read path. | `context_intelligence/tool_resolver.py:173, 216` |
| `validate_sources()` loops over **every** entry in the map and raises a single `ValueError` naming **all** bad entries if **any** one is invalid — this blocks mount for entries that were never going to be queried. | `context_intelligence/tool_resolver.py:389-426` |
| `mount()` calls `resolver.validate_sources()` **unconditionally, eagerly, at mount time** — a single bad entry currently fails the whole module's mount. | `modules/tool-context-intelligence-query/.../__init__.py:41-42` |
| `GraphQueryTool.input_schema` / `BlobReadTool.input_schema` have no `source` field today. | `graph_query_tool.py:58-91`, `blob_read_tool.py:60-71` |
| `GraphQueryTool._resolve_server_config()` and `BlobReadTool.execute()` both call `resolve_query_endpoint(hook_resolver, tool_resolver)` / `resolve_query_auth_strategy(...)` with no selector argument. | `graph_query_tool.py:103-122`, `blob_read_tool.py:80-84` |
| `skill_sync.py` **also** calls `tool._resolve_server_config(coordinator)` (two call sites, no selector) purely to find "a" reachable server for fetching the (session-agnostic, static) skill-body documentation — this is a **second consumer of the same resolution chain that must not be broken** by making ambiguity fail loud. | `skill_sync.py:168`, `skill_sync.py:308-310` |
| `Source` / `Destination` have exactly one shape: `name, url, api_key, auth_mode, auth_resource` (+ `include`/`exclude` on `Destination` only). **No `type` discriminator field exists anywhere in either config schema.** | `context_intelligence/tool_resolver.py:91-107`, `config_resolver.py:94-118` |
| README documents the exact defect at issue: *"Only a single read source is supported in this version... if more than one is present, only the first (declaration / insertion order) is used and the rest are ignored."* | `README.md:431` |

This last row is why criteria 1–4 are almost entirely a **`context_intelligence/tool_resolver.py` + two tool files** change — the data model (`Source`, the `sources` dict) is already multi-entry-capable; only the *consumption* and *validation* logic needs to change.

---

## 1. Judgment calls (criteria 3, 5, 6, 7) — decisions and rationale

| # | Decision | One-line reason |
|---|---|---|
| 3 | **Require an explicit `source` selector when 2+ sources are configured and none is given.** No new "designated default" config field. | Zero new config surface; matches the codebase's existing fail-loud-with-ValueError convention (`validate_destinations`/`validate_sources`) instead of introducing the first "magic boolean" field on `Source`/`Destination`. See §1.1. |
| 5 | **Document a restart requirement in README.md. Do not implement cache invalidation.** | Every other cached resolver property in this codebase (`HookConfigResolver.destinations`, `.base_path`, `.workspace`; `ToolConfigResolver.workspace`) has *exactly* the same "compute once per session process, no invalidation" lifecycle, and the Entra token-cache section already documents "start a new session to reset" for an analogous case. Adding invalidation for just `sources` would be new, unprecedented infrastructure for one field. See §1.2. |
| 6 | **N/A — confirmed, not a real config option today.** | Neither `Source` nor `Destination` has a `type`/scheme discriminator; nothing in `AsyncCIClient` interprets a non-HTTP(S) URL for a *source*. `ci-blob://` is a **query-result reference format** returned by the graph server, not a `sources`/`destinations` config shape. No code path exists to make N/A ambiguous. See §1.3. |
| 7 | **Implement the cheap variant (a single log line); explicitly defer real existence-checking.** | Actually checking "does this session_id exist in another configured source" requires a network round-trip per other source plus session_id extraction from arbitrary caller-supplied Cypher — that IS the fan-out/reconciliation infrastructure the council parked. The zero-network-cost variant (log that N other sources exist and were not queried, whenever 2+ are configured) needs no new infrastructure and ships today. See §1.4. |

### 1.1 Criterion 3 rationale (expanded)

The alternative — an explicit `default: true` marker on a `sources` entry — would require: a new field on the `Source` `NamedTuple`, new parse logic in the `sources` property, and new validation ("what if two entries both claim `default: true`?", "what happens if the marked-default entry is later renamed?"). That's real new surface for a need that **only exists because this very fix introduces multi-entry sources in the first place** — today, nobody has 2+ sources configured, so nobody depends on implicit precedence yet. Requiring an explicit `source=` argument the moment ambiguity is possible:

- Adds **zero** new YAML surface — `Source`'s shape is unchanged.
- Is consistent with every other "fail loud, name the entries" precedent in this file (`validate_destinations`, `validate_sources`).
- Only ever bites when the caller has *already* opted into a new capability (configuring 2+ sources) — the common "configure nothing" / "configure exactly one" cases (README: *"Most users configure nothing here"*) are completely unaffected — see the truth table in §2.2.

### 1.2 Criterion 5 rationale (expanded)

`ToolConfigResolver` is constructed exactly **once** per session, inside `mount()` (`__init__.py:41`, comment: *"built ONCE"*), and lives for the process's lifetime — identical to `HookConfigResolver`, whose own `destinations` property has the identical "cached after first access, no invalidation" contract (`config_resolver.py:597-598`). There is no live-reload mechanism anywhere in this bundle for *any* mount-time config value; session-process-lifetime is the universal unit of config lifecycle here. The Entra token-cache section of the README (lines 380-382) already tells operators the exact same thing for a conceptually adjacent problem: *"To switch identities immediately, start a new session — a fresh process resets the cache."* Implementing invalidation for `sources` alone would be inconsistent (every sibling property stays static-for-process-life) and would require inventing a config-change-detection mechanism that doesn't exist anywhere else in the bundle. Document, don't build.

### 1.3 Criterion 6 rationale (expanded)

Grepped `Source`, `Destination`, `AsyncCIClient`, and the whole `context_intelligence/` + module tree for any `type`/scheme-discriminator field or file-scheme handling on a *source* or *destination* config entry: none exists. The only non-HTTP scheme in the codebase is `ci-blob://` (`blob_read_tool.py:32`), which is a URI format for referencing a specific blob returned **inside a query result**, not a `sources`/`destinations` config entry shape — it never appears in `Source`/`Destination`, `validate_sources`, or `validate_destinations`. Confirmed N/A; no code changes required for this criterion. If a genuine file-backed source type is proposed in the future, it needs its own design (new `type` field, new per-type validation, new dispatch in `AsyncCIClient`) — explicitly out of scope here.

### 1.4 Criterion 7 rationale (expanded)

A true "is the same `session_id` present in another configured source" check needs: (a) a live query against every *other* configured source (a real network call, with its own auth/timeout/failure handling per source — this alone re-introduces most of the parked fan-out machinery), and (b) reliable `session_id` extraction from the caller's input — trivial for `blob_read` (`session_id` is structurally the first URI path segment) but **not reliably available** for `graph_query`, whose `query`/`params` are arbitrary caller-supplied Cypher (a `session_id` is a common but not guaranteed bound parameter — see `bundled_skill/context-intelligence-graph-query.md`, which uses `$session_id` throughout but does not make it mandatory). Building this "cheaply" is not actually cheap — it's the reconciliation layer, deferred. The variant that *is* cheap, and is what ships: a single `log.info(...)` line, emitted whenever the tool successfully dispatches a query to one named source out of 2+ configured, naming the untouched sources — zero network calls, zero new session_id-extraction logic, zero change to `ToolResult.output` shape (which must not be polluted with side-channel metadata for `graph_query`, since its `output` is the raw, arbitrary-shaped Cypher result an agent may parse directly).

---

## 2. Criteria 1–4: `context_intelligence/tool_resolver.py` changes

### 2.1 New exception type

Add near the top of the file, after the `Source` `NamedTuple` (after line 107):

```python
class SourceSelectionError(ValueError):
    """Raised when a caller-supplied (or absent) `source` selector cannot be resolved
    unambiguously against the configured `sources` map.

    Always a ValueError subclass so existing `except ValueError` call sites (if any)
    still catch it, but carries structured data so tool execute() methods can build a
    precise ToolResult error without re-parsing the message string.
    """

    def __init__(self, message: str, *, error_type: str, valid_names: list[str]) -> None:
        super().__init__(message)
        #: "unknown_source" | "ambiguous_source_selection" -- mirrors ToolResult.error["type"].
        self.error_type = error_type
        #: Sorted list of configured source names, for the caller to echo back verbatim.
        self.valid_names = valid_names
```

### 2.2 `_select_source()` — replaces the `_first_entry(tool_resolver.sources)` call sites

Add directly after `_first_entry()` (after line 124), **not replacing `_first_entry`** — `_first_entry` is still used unchanged for `_first_destination()` (hook destinations are explicitly out of scope for this fix; tier 2/3 of `resolve_query_endpoint` are untouched).

```python
def _select_source(
    sources: dict[str, Source],
    requested_name: str | None,
    *,
    allow_implicit_default: bool = False,
) -> Source | None:
    """Select which configured ``sources`` entry a caller wants (criteria 1-3).

    Parameters
    ----------
    sources:
        ``tool_resolver.sources`` -- the parsed, name-keyed map.
    requested_name:
        The caller's explicit ``source`` input (``input.get("source")``), or ``None``
        if the caller didn't pass one.
    allow_implicit_default:
        When ``True``, restores the OLD (pre-fix) "silently use the first
        insertion-order entry, never raise" behavior for the 2+-sources/no-name case.
        This flag exists for exactly ONE caller: ``skill_sync.py``'s internal
        server-reachability lookups, which fetch session-agnostic *documentation*
        (the graph-query skill body) rather than session-specific graph data, so an
        arbitrary configured source is a safe pick there. Every other caller (the
        ``graph_query`` / ``blob_read`` tools' ``execute()`` paths) MUST leave this
        ``False`` (the default) so criterion 3's fail-loud rule applies to real
        queries. See docs/designs/workstream-1-multi-source-query-tools.md sec 2.5.

    Returns
    -------
    Source | None
        - ``None`` only when ``sources`` is empty AND ``requested_name`` is ``None``
          -- unchanged legacy behavior: caller falls through to tier 2 (hook
          destination) / tier 3 (env).
        - The matching ``Source`` in every other case.

    Raises
    ------
    SourceSelectionError
        - ``error_type="unknown_source"``: ``requested_name`` is not ``None`` and is
          not a key in ``sources`` (whether ``sources`` is empty or non-empty). An
          explicit request is NEVER silently redirected to a different endpoint
          (criterion 2) -- not even the hook's upload destination.
        - ``error_type="ambiguous_source_selection"``: ``requested_name`` is ``None``,
          2+ sources are configured, and ``allow_implicit_default`` is ``False``
          (criterion 3).
    """
    if requested_name is not None:
        if requested_name not in sources:
            raise SourceSelectionError(
                f"context-intelligence: unknown source {requested_name!r}. "
                f"Configured sources: "
                f"{', '.join(sorted(sources)) if sources else '(none configured)'}.",
                error_type="unknown_source",
                valid_names=sorted(sources),
            )
        return sources[requested_name]

    if not sources:
        return None  # unchanged: tier 1 empty -> fall through to tier 2 / tier 3

    if len(sources) == 1:
        return next(iter(sources.values()))  # single entry -- no ambiguity, no selector needed

    if allow_implicit_default:
        log.debug(
            "CI source selection: %d sources configured, no selector given, "
            "allow_implicit_default=True (skill-sync path) -- using first: %s",
            len(sources),
            next(iter(sources)),
        )
        return next(iter(sources.values()))

    raise SourceSelectionError(
        f"context-intelligence: {len(sources)} sources are configured "
        f"({', '.join(sorted(sources))}) but no `source` was specified. "
        f"Pass source=<name> to select one.",
        error_type="ambiguous_source_selection",
        valid_names=sorted(sources),
    )
```

**Truth table** (this is the exact behavior matrix to hand to `modular-builder` / test authors):

| `len(sources)` | `requested_name` | `allow_implicit_default` | Result |
|---|---|---|---|
| 0 | `None` | any | `None` (unchanged: falls through to hook destination / env) |
| 0 | `"foo"` | any | raises `unknown_source`, `valid_names=[]` |
| 1 | `None` | any | that single source (unchanged behavior, now principled not coincidental) |
| 1 | `"default"` (matches) | any | that source |
| 1 | `"bogus"` (no match) | any | raises `unknown_source`, `valid_names=["default"]` |
| 2+ | `None` | `False` (tools) | raises `ambiguous_source_selection`, `valid_names=[...]` |
| 2+ | `None` | `True` (skill_sync only) | first by insertion order (old behavior, logged at DEBUG) |
| 2+ | `"a"` (matches) | any | source `"a"` |
| 2+ | `"z"` (no match) | any | raises `unknown_source`, `valid_names=["a", "b", ...]` |

### 2.3 `resolve_query_endpoint()` — add `source_name` and per-entry validation (criteria 1, 2, 4)

Replace lines 202-235 with:

```python
def resolve_query_endpoint(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
    *,
    source_name: str | None = None,
    allow_implicit_default: bool = False,
) -> tuple[str | None, str | None]:
    """Resolve (server_url, api_key) for the query path. Per-field independent.

    Explicit-first order (each field, first non-empty wins):
      1. selected entry of tool_resolver.sources (.url / .api_key) -- selection is now
         via _select_source(), not "always first" (criteria 1-3). The selected entry
         is validated (criterion 4: per-entry, not whole-map) before its fields are read.
      2. first upload destination on the hook resolver (.url / .api_key)
      3. AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL / AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY
    Returns (None, None)-able per field; each is None only if all three miss.

    Parameters
    ----------
    source_name:
        Caller's explicit ``source`` selector (from tool input), or ``None``.
    allow_implicit_default:
        Passed straight through to ``_select_source`` -- see its docstring. Only
        ``skill_sync.py`` sets this ``True``.

    Raises
    ------
    SourceSelectionError
        Selection is ambiguous or names an unconfigured source (criteria 2-3).
    ValueError
        The *selected* source itself fails per-field validation (criterion 4) --
        message names ONLY that one source, never the whole map.

    Emits one DEBUG line naming which tier supplied each field.
    """
    read = _select_source(tool_resolver.sources, source_name, allow_implicit_default=allow_implicit_default)
    if read is not None:
        tool_resolver.validate_source(read.name)  # raises ValueError naming only `read.name` if bad

    dest = _first_destination(hook_resolver)

    url, url_src = _pick(
        ((read.url if read else None), f"source:{read.name}" if read else None),
        ((dest.url if dest else None), f"destination:{dest.name}" if dest else None),
        (_env("SERVER_URL"), "env:SERVER_URL"),
    )
    api_key, key_src = _pick(
        ((read.api_key if read else None), f"source:{read.name}" if read else None),
        ((dest.api_key if dest else None), f"destination:{dest.name}" if dest else None),
        (_env("API_KEY"), "env:API_KEY"),
    )

    log.debug(
        "CI query endpoint resolved: url<-%s api_key<-%s",
        url_src or "none",
        key_src or "none",
    )

    # Criterion 7 (cheap variant only -- see sec 2.6): note untouched sibling sources.
    if read is not None and len(tool_resolver.sources) >= 2:
        others = sorted(name for name in tool_resolver.sources if name != read.name)
        log.info(
            "CI query dispatched to source %r; %d other configured source(s) not "
            "queried: %s. Cross-source existence checking is not implemented -- if "
            "the same session_id may exist in another source, query it explicitly "
            "via source=<name>.",
            read.name,
            len(others),
            ", ".join(others),
        )

    return (url or None, api_key or None)
```

### 2.4 `resolve_query_auth_strategy()` — add `source_name` (criteria 1, 2)

Replace lines 142-199's signature and the two `read = ...` / auth-field lines:

```python
def resolve_query_auth_strategy(
    hook_resolver: Any | None,
    tool_resolver: "ToolConfigResolver",
    api_key: str = "",
    *,
    source_name: str | None = None,
    allow_implicit_default: bool = False,
) -> Any:
    """Build an AuthStrategy for query tool requests.

    ... (existing docstring body unchanged) ...

    source_name / allow_implicit_default:
        Same contract as resolve_query_endpoint(). Re-runs selection independently
        (mirrors this module's existing "each field/each function resolves
        independently" design -- see the module docstring) rather than threading a
        pre-selected Source object through; the extra dict lookup is negligible and
        this keeps the two functions decoupled and independently testable, exactly
        as _pick()/_first_entry() already are.

    Raises
    ------
    SourceSelectionError, ValueError
        Same as resolve_query_endpoint() -- if called after resolve_query_endpoint()
        already succeeded for the same (tool_resolver, source_name), this call is
        guaranteed not to raise (selection and validation are deterministic and
        idempotent over the same inputs).
    """
    from context_intelligence.auth import ApiKeyAuth, build_auth_strategy  # noqa: PLC0415

    read = _select_source(tool_resolver.sources, source_name, allow_implicit_default=allow_implicit_default)
    if read is not None:
        tool_resolver.validate_source(read.name)
    dest = _first_destination(hook_resolver)

    # ... unchanged auth_mode / auth_resource _pick-style resolution below ...
```

### 2.5 `ToolConfigResolver` — split `validate_sources()` semantics (criterion 4)

**This is a breaking behavior change to an existing method** — call out prominently in the PR description. Replace lines 389-426:

```python
def validate_sources(self) -> list[str]:
    """Best-effort validation pass over ALL configured sources -- WARN, never raise.

    BREAKING CHANGE (criterion 4, docs/designs/workstream-1-multi-source-query-tools.md):
    previously this method raised ValueError naming EVERY problem across the WHOLE
    sources map, and was called unconditionally at mount() time -- one bad entry
    blocked ALL queries, including ones the caller never intended to touch. It is now
    a non-fatal diagnostic pass: still runs at mount() (so operators still see typos
    immediately in logs), but only WARNS. Hard, fail-loud validation of the specific
    source a query actually targets now happens per-query via validate_source(name)
    (below), called from resolve_query_endpoint()/resolve_query_auth_strategy() only
    for the ONE selected entry.

    Per-source XOR auth rules (unchanged):
    - auth_mode="static" (default): api_key must be non-empty.
    - auth_mode="entra":           auth_resource must be non-empty; api_key not required.
    - unknown auth_mode:           always a problem.
    - url must always be non-empty for explicitly configured sources.

    Returns
    -------
    list[str]
        Problem strings (``"{name}: {problem}"``), one per invalid field found.
        Empty list if every configured source is valid. (Kept as a return value,
        not just a log side-effect, so tests and callers can assert on it directly.)
    """
    srcs = self.sources
    problems = self._collect_source_problems(srcs)
    if problems:
        log.warning(
            "context-intelligence sources misconfigured (mount-time diagnostic only "
            "-- queries against OTHER, correctly configured sources are unaffected; "
            "hard validation is now per-source at query time): %s. "
            "Set url and api_key (static) or auth_resource (entra) under "
            "overrides.tool-context-intelligence-query.config.sources.<name>.",
            ", ".join(problems),
        )
    return problems

def validate_source(self, name: str) -> Source:
    """Validate and return exactly ONE named source. Fail-fast for JUST this entry.

    This is the hard, query-time gate (criterion 4): a misconfigured entry only ever
    blocks queries that target IT, never its siblings.

    Raises
    ------
    KeyError
        `name` is not in ``self.sources``. (Callers should always pass a name that
        already came from ``_select_source`` / ``self.sources``, so this should be
        unreachable in practice -- it is not the caller-facing "unknown source"
        error, which is ``SourceSelectionError`` and is raised earlier, in
        ``_select_source``, before this method is ever called.)
    ValueError
        The named source fails per-field validation. Message names ONLY `name`.
    """
    src = self.sources[name]
    problems = self._collect_source_problems({name: src})
    if problems:
        raise ValueError(
            f"context-intelligence source {name!r} misconfigured: {', '.join(problems)}. "
            f"Set url and api_key (static) or auth_resource (entra) under "
            f"overrides.tool-context-intelligence-query.config.sources.{name}."
        )
    return src

@staticmethod
def _collect_source_problems(srcs: dict[str, Source]) -> list[str]:
    """Shared per-field XOR check, extracted so validate_sources()/validate_source()
    apply IDENTICAL rules to the whole map vs. a single entry (criterion 4 requires
    they diverge only in *scope* -- whole-map vs one-entry -- never in *rule*)."""
    problems: list[str] = []
    for name, src in srcs.items():
        if not src.url:
            problems.append(f"{name}: missing url")
        if src.auth_mode == "static":
            if not src.api_key:
                problems.append(f"{name}: missing api_key")
        elif src.auth_mode == "entra":
            if not src.auth_resource:
                problems.append(f"{name}: missing auth_resource (required for auth_mode=entra)")
        else:
            problems.append(f"{name}: unknown auth_mode {src.auth_mode!r} (valid: 'static', 'entra')")
    return problems
```

> Note on the `name!r} misconfigured: {', '.join(problems)}` message format above: `problems` for a
> single-entry call always contains strings already prefixed with `"{name}: "` (from
> `_collect_source_problems`), so the rendered message looks like:
> `context-intelligence source 'azure-team' misconfigured: azure-team: missing api_key. Set url and
> api_key ...` — slightly redundant-looking but byte-consistent with the existing
> whole-map message format (`README`/tests already expect `"{name}: {problem}"` substrings), and
> keeps `_collect_source_problems` identical for both call sites. Acceptable; do not "clean up" the
> redundancy by diverging the shared helper's output shape between the two callers.

### 2.6 `mount()` — remove the eager, unconditional fail-fast call

`modules/tool-context-intelligence-query/amplifier_module_tool_context_intelligence_query/__init__.py:41-42`:

```python
# BEFORE
resolver = ToolConfigResolver(config or {}, coordinator)  # built ONCE
resolver.validate_sources()  # fail-loud on misconfigured sources (mirrors hook validate_destinations)

# AFTER
resolver = ToolConfigResolver(config or {}, coordinator)  # built ONCE
resolver.validate_sources()  # WARN-only diagnostic pass (criterion 4) -- no longer raises;
                              # hard validation is now per-source at query time (see tool_resolver.py)
```

The call site is unchanged (still call it at mount so a typo shows up in logs immediately); only the
method's own behavior changed from raise to warn. Update the inline comment as shown.

---

## 3. Criteria 1–3: tool-facing changes

### 3.1 `graph_query_tool.py`

**`input_schema`** — add one property (insert after the `"workspace"` block, before `"required"`):

```python
"source": {
    "type": "string",
    "description": (
        "Optional name of a specific configured read source to query (see "
        "overrides.tool-context-intelligence-query.config.sources). Required when "
        "2 or more sources are configured and you have not already been told which "
        "one to use -- omitting it in that case raises an error listing the valid "
        "names. Safe to omit when 0 or 1 source is configured."
    ),
},
```

**`_resolve_server_config`** — add `source_name` and `allow_implicit_default` passthrough params, and
wrap selection/validation errors:

```python
def _resolve_server_config(
    self,
    coordinator: Any,
    source_name: str | None = None,
    *,
    allow_implicit_default: bool = False,
) -> tuple[str | None, str | None, str, Any]:
    """Resolve (server_url, api_key, workspace, auth_strategy) using the three-tier fallback chain.

    source_name / allow_implicit_default: forwarded to resolve_query_endpoint() /
    resolve_query_auth_strategy() unchanged -- see their docstrings and
    docs/designs/workstream-1-multi-source-query-tools.md sec 2.2 for the selection
    contract (criteria 1-3). Raises SourceSelectionError / ValueError on ambiguous
    or misconfigured selection; callers (execute(), skill_sync) are responsible for
    catching these and degrading appropriately for their own context.

    Late-mount upgrade: retries hook capability lookup on every call while
    _hook_resolver is None (hook may mount after the tool).
    """
    if self._hook_resolver is None:
        self._hook_resolver = coordinator.get_capability(
            "context_intelligence.hook_config_resolver"
        )
    url, api_key = resolve_query_endpoint(
        self._hook_resolver, self._tool_resolver,
        source_name=source_name, allow_implicit_default=allow_implicit_default,
    )
    auth_strategy = resolve_query_auth_strategy(
        self._hook_resolver, self._tool_resolver, api_key=api_key or "",
        source_name=source_name, allow_implicit_default=allow_implicit_default,
    )
    workspace = (
        self._hook_resolver.workspace
        if self._hook_resolver is not None
        else self._tool_resolver.workspace
    )
    return url, api_key, workspace, auth_strategy
```

**`execute()`** — read `input.get("source")`, catch the two new error classes, translate to
`ToolResult`:

```python
async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
    from context_intelligence.tool_resolver import SourceSelectionError  # noqa: PLC0415

    source_name = input.get("source")
    try:
        server_url, api_key, workspace, auth_strategy = self._resolve_server_config(
            self._coordinator, source_name
        )
    except SourceSelectionError as exc:
        return ToolResult(
            success=False,
            error={
                "message": str(exc),
                "type": exc.error_type,  # "unknown_source" | "ambiguous_source_selection"
                "valid_sources": exc.valid_names,
            },
        )
    except ValueError as exc:
        # The selected source itself is misconfigured (criterion 4) -- names only it.
        return ToolResult(
            success=False,
            error={"message": str(exc), "type": "source_misconfigured"},
        )

    if not server_url:
        return ToolResult(
            success=False,
            error={
                "message": "context-intelligence server URL not configured",
                "type": "configuration_error",
            },
        )

    # ... rest of execute() body (query/params handling, AsyncCIClient call) unchanged ...
```

### 3.2 `blob_read_tool.py`

**`input_schema`** — identical `"source"` property added alongside `"uri"`:

```python
"source": {
    "type": "string",
    "description": (
        "Optional name of a specific configured read source to fetch the blob from "
        "(see overrides.tool-context-intelligence-query.config.sources). Required "
        "when 2 or more sources are configured and none was implied -- omitting it "
        "in that case raises an error listing the valid names."
    ),
},
```

**`execute()`** — same selection/validation pattern as `GraphQueryTool`, applied before the existing
`resolve_query_endpoint(...)` call at line 81:

```python
async def execute(self, input: dict[str, Any]) -> ToolResult:  # noqa: A002
    from context_intelligence.tool_resolver import SourceSelectionError  # noqa: PLC0415

    if self._hook_resolver is None:
        self._hook_resolver = self._coordinator.get_capability(
            "context_intelligence.hook_config_resolver"
        )

    source_name = input.get("source")
    try:
        server_url, api_key = resolve_query_endpoint(
            self._hook_resolver, self._tool_resolver, source_name=source_name
        )
        auth_strategy = resolve_query_auth_strategy(
            self._hook_resolver, self._tool_resolver, api_key=api_key or "",
            source_name=source_name,
        )
    except SourceSelectionError as exc:
        return ToolResult(
            success=False,
            error={
                "message": str(exc),
                "type": exc.error_type,
                "valid_sources": exc.valid_names,
            },
        )
    except ValueError as exc:
        return ToolResult(
            success=False,
            error={"message": str(exc), "type": "source_misconfigured"},
        )

    if not server_url:
        return ToolResult(
            success=False,
            error={
                "message": "context-intelligence server URL not configured",
                "type": "configuration_error",
            },
        )
    server_url = server_url.rstrip("/")

    # ... rest of execute() body (URI parsing, sanitization, fetch_blob) unchanged ...
```

### 3.3 `skill_sync.py` — the required non-breaking call-site update

Both existing call sites **must** add `allow_implicit_default=True` so that skill-body sync (which is
session-agnostic and never has a `source` selector to pass) keeps its exact pre-fix behavior — "pick
a configured source, never error" — even when 2+ sources are now configured. Without this change,
`on_session_ready()` would start raising `SourceSelectionError` for every session with an ambiguous
`sources` map, which the kernel would catch and log as a `module:on_session_ready_failed` WARNING
(non-fatal per `CONTRACTS.md`) — but the practical effect would be **skill sync silently stops
working** for anyone who adopts multi-source `sources` for their actual queries. This must not
regress as a side effect of this fix.

`skill_sync.py:168`:

```python
# BEFORE
server_url, _api_key, _workspace, _auth_strategy = tool._resolve_server_config(coordinator)

# AFTER
server_url, _api_key, _workspace, _auth_strategy = tool._resolve_server_config(
    coordinator, allow_implicit_default=True
)
```

`skill_sync.py:308-310`:

```python
# BEFORE
server_url, api_key, _workspace, _auth_strategy = tool._resolve_server_config(
    coordinator
)

# AFTER
server_url, api_key, _workspace, _auth_strategy = tool._resolve_server_config(
    coordinator, allow_implicit_default=True
)
```

No other change to `skill_sync.py` is needed — `_resolve_server_config`'s new `source_name` parameter
defaults to `None`, which is exactly what these two call sites already pass implicitly.

---

## 4. README.md — exact sections to update

### 4.1 Line 431 (the documented defect itself) — full paragraph replacement

**Current text (line 431):**

> **`sources`** is a mapping keyed by name, mirroring the hook's `destinations` shape. **Only a single
> read source is supported in this version** — the read path does **not** fan out to multiple
> sources. Configure exactly one entry (conventionally named `default`); if more than one is present,
> only the **first** (declaration / insertion order) is used and the rest are ignored.

**Replace with:**

> **`sources`** is a mapping keyed by name, mirroring the hook's `destinations` shape. Configure one
> entry for the common case, or 2+ entries when queries must be able to target different servers.
> **The read path does not fan out** — each `graph_query` / `blob_read` call still queries exactly
> one source per invocation — but which one is now explicit rather than an accident of insertion
> order:
>
> | Configured sources | `source` argument passed | Result |
> |---|---|---|
> | 0 | (n/a) | Falls through to the hook's first `destination`, then env (unchanged). |
> | 1 | omitted | That one source is used — no selector needed. |
> | 1 | a name | Used if it matches; **error, naming the one valid name, if it doesn't.** |
> | 2+ | a name | The named source is used if configured; **error enumerating all valid names if not** — never silently substitutes a different source. |
> | 2+ | omitted | **Error enumerating all valid names.** With 2+ sources configured, a selector is required — there is no implicit "default" source chosen by insertion order. |
>
> A misconfigured source (missing `url`, missing `api_key`/`auth_resource`) only blocks queries that
> target **that** source by name — it does not block queries against other, correctly configured
> sources (a startup-time WARNING is still logged for every misconfigured entry so operators see
> typos immediately).

### 4.2 Line 424 (the tier-1 row of the endpoint resolution table) — small clarifying edit

**Current:**

> | **1** | First entry of `sources` on the tool's own config (`overrides.tool-context-intelligence-query.config`) | The explicit read override. Wins when set. Applies to both `graph_query` and `blob_read` — configure once. |

**Replace with:**

> | **1** | The `source`-selected entry of `sources` on the tool's own config (`overrides.tool-context-intelligence-query.config`) — see [`sources`](#query-tools-graph-query-blob-read--read-side-endpoint) below for selection rules when 2+ are configured. | The explicit read override. Wins when set. Applies to both `graph_query` and `blob_read` — pass `source=<name>` per call when 2+ sources are configured. |

### 4.3 Line 442-446 (the example YAML block) — extend to show a real multi-source example

**Current:**

```yaml
overrides:
  tool-context-intelligence-query:
    config:
      sources:
        default:                            # the single read source (only the first entry is honored)
          url: "http://read-replica.example.com"
          api_key: "${CI_READ_KEY}"        # secret lives in keys.env, referenced here
```

**Replace with:**

```yaml
overrides:
  tool-context-intelligence-query:
    config:
      sources:
        default:
          url: "http://read-replica.example.com"
          api_key: "${CI_READ_KEY}"        # secret lives in keys.env, referenced here
        archive:                            # a second source -- now a first-class capability
          url: "http://archive-ci.example.com"
          api_key: "${CI_ARCHIVE_READ_KEY}"
```

```
# With 2+ sources configured (as above), every graph_query / blob_read call MUST pass
# `source`:
#   graph_query(query="...", source="default")
#   graph_query(query="...", source="archive")
# Omitting `source` here raises an error enumerating "default" and "archive".
```

### 4.4 Line 448-451 (the sub-key table right after the example) — add a note

Append one row/sentence directly under the existing `url`/`api_key` sub-key table:

> With exactly one `sources` entry configured, `source` is optional on every call. With 2+ entries,
> `source` is required on every call — see the table in [§4.1 above](#deprecated--legacy-single-server-scalars).

### 4.5 New subsection — "Query tools" section addendum for skill sync (near line 474)

Add one sentence to the existing skill-sync paragraph (after the sentence ending "...`sources` → hook
`destinations` → env)."):

> When 2+ `sources` are configured, skill-body sync intentionally does **not** require a `source`
> selector and will not error on ambiguity — it uses the first configured source by convention,
> because the skill body it fetches is static, session-agnostic documentation (Cypher pattern
> reference), not session-specific graph data. This is the one exception to the fail-loud selection
> rule above, and applies only to skill-body sync, never to `graph_query`/`blob_read` query results.

### 4.6 `docs/context-intelligence-exploration-guide.md` — optional cross-reference

Not required for this scoped fix, but if `graph_query`/`blob_read` invocation examples appear there,
add `source=` to any example that assumes a single configured source, for consistency. Confirm with a
grep before assuming it needs edits — out of scope to touch proactively if it contains no such
examples today.

---

## 5. `context/config-resolution.dot` — non-blocking follow-up (not required to ship this fix)

The `q1` node in this diagram currently reads *"1. sources[first] .url / .api_key ..."* This phrase
is now stale (selection is no longer unconditionally "first"). Recommend a follow-up doc-only PR to
reword `q1`'s label to *"1. sources[selected] .url / .api_key (see _select_source — explicit name,
or the sole entry, or ambiguity error)"*. Not gating this fix on the diagram — it is a `.dot`
visualization artifact, not executable contract text, and the README (§4 above) is the authoritative
prose surface. Flagging so it isn't forgotten in a later documentation pass.

---

## 6. Test additions (file-by-file)

| File | New tests |
|---|---|
| `tests/test_tool_resolver.py` | `TestSelectSource` class covering every row of the §2.2 truth table (0/1/2+ sources × name present/absent/matching/non-matching × `allow_implicit_default` True/False). `TestValidateSourcePerEntry`: one bad entry among 2 configured — `validate_sources()` returns problems but does not raise; `validate_source("good")` returns cleanly; `validate_source("bad")` raises `ValueError` naming only `"bad"`. `TestResolveQueryEndpointSourceName`: `source_name` threading through tiers, `SourceSelectionError` propagation, the criterion-7 log line fires only when `len(sources) >= 2`. |
| `modules/tool-context-intelligence-query/tests/test_graph_query_tool.py` | `execute()` with `source` matching / not matching / omitted-with-2-configured, asserting exact `ToolResult.error["type"]` values (`unknown_source`, `ambiguous_source_selection`, `source_misconfigured`) and that `error["valid_sources"]` is populated and sorted. |
| `modules/tool-context-intelligence-query/tests/test_blob_read_tool.py` | Same matrix as above for `blob_read`. |
| `modules/tool-context-intelligence-query/tests/test_skill_sync.py` (existing file — extend, do not create) | Regression test: with 2 configured `sources` and no `source` selector available to skill_sync, `_apply_offline_skill_bodies` / `_resync_all_watched` complete without raising (i.e. `allow_implicit_default=True` is actually wired at both call sites in `skill_sync.py`). This is the single most important regression guard for this fix — it is the one place a naive implementation of criterion 3 silently breaks a working feature. |
| `modules/tool-context-intelligence-query/tests/test_module.py` | `mount()` with one bad + one good source entry no longer raises (criterion 4) — asserts both tools still mount successfully and a WARNING is logged. |

---

## 7. Non-goals reaffirmed (do not implement as part of this workstream)

- No fan-out: a single `graph_query`/`blob_read` call still queries exactly one resolved source.
- No "list configured sources" tool/API. (The `valid_sources` list in error payloads is a side effect
  of the fail-loud error, not a discovery feature — do not generalize it into a query-time listing
  capability.)
- No cross-source reconciliation/merge of query results.
- No file-type source scheme (§1.3 — confirmed N/A, no such config shape exists).
- No changes to `hook-context-intelligence`'s `destinations` fan-out logic — tier 2 of
  `resolve_query_endpoint` is untouched by this fix.
- No changes related to the parked generic non-bundle file-capture workstream.
