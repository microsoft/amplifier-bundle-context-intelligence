"""Tests that mount() docstring and guard use singular 'graph_store' (not plural 'graph_stores').

Task-2: refactor plural → singular in two places inside __init__.py:
  1. Guard on (previously) line 105: config.get("graph_store")
  2. Docstring on line 69: "enable_graph + graph_store"

Task-6: guard migrated to resolver: resolver.graph_store_config
"""

from __future__ import annotations

import inspect
from pathlib import Path


def _init_source() -> str:
    """Return the raw source text of __init__.py."""
    import amplifier_module_hook_context_intelligence as mod

    src_path = Path(inspect.getfile(mod))
    return src_path.read_text()


def test_mount_docstring_uses_singular_graph_store():
    """Docstring for mount() must say 'graph_store' (singular), not 'graph_stores'."""
    from amplifier_module_hook_context_intelligence import mount

    doc = mount.__doc__ or ""
    assert "graph_store" in doc, "mount() docstring must reference 'graph_store'"
    assert "graph_stores" not in doc, (
        "mount() docstring must NOT reference plural 'graph_stores'; "
        "it was renamed to singular 'graph_store'"
    )


def test_no_graph_stores_plural_in_init_source():
    """No occurrence of 'graph_stores' (plural) should remain in __init__.py."""
    source = _init_source()
    assert "graph_stores" not in source, (
        "__init__.py must not contain the plural key 'graph_stores'; "
        "all references must use singular 'graph_store'"
    )


def test_guard_uses_singular_graph_store_key():
    """The conditional guard in __init__.py must use resolver.graph_store_config (singular)."""
    source = _init_source()
    assert "resolver.graph_store_config" in source, (
        "__init__.py guard must use resolver.graph_store_config "
        "(singular graph_store key accessed via resolver)"
    )
