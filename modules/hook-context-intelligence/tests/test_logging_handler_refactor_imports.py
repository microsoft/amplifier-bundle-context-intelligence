"""Tests for task-2: logging_handler.py imports from upload.py.

Verifies that:
- _canonical_json and _compute_idempotency_key are imported FROM upload.py
  (not redefined locally in logging_handler.py)
- hashlib is not directly imported in logging_handler.py
"""

from __future__ import annotations


class TestImportRefactoring:
    """logging_handler.py must import helpers from upload.py, not define them locally."""

    def test_canonical_json_is_same_object_as_upload(self) -> None:
        """_canonical_json in logging_handler must be the same object as in upload."""
        import amplifier_module_hook_context_intelligence.handlers.logging_handler as lh
        import amplifier_module_hook_context_intelligence.upload as upload

        assert lh._canonical_json is upload._canonical_json, (
            "_canonical_json must be imported from upload, not redefined in logging_handler"
        )

    def test_compute_idempotency_key_is_same_object_as_upload(self) -> None:
        """_compute_idempotency_key in logging_handler must be the same object as in upload."""
        import amplifier_module_hook_context_intelligence.handlers.logging_handler as lh
        import amplifier_module_hook_context_intelligence.upload as upload

        assert lh._compute_idempotency_key is upload._compute_idempotency_key, (
            "_compute_idempotency_key must be imported from upload, not redefined in logging_handler"
        )

    def test_hashlib_not_imported_in_logging_handler(self) -> None:
        """hashlib must NOT be directly imported in logging_handler module."""
        import amplifier_module_hook_context_intelligence.handlers.logging_handler as lh

        assert "hashlib" not in lh.__dict__, (
            "hashlib must be removed from logging_handler imports "
            "(it is only needed in upload.py)"
        )

    def test_canonical_json_still_works(self) -> None:
        """_canonical_json imported into logging_handler must still produce correct output."""
        import amplifier_module_hook_context_intelligence.handlers.logging_handler as lh

        result = lh._canonical_json({"b": 2, "a": 1})
        assert result == '{"a":1,"b":2}', f"Expected sorted compact JSON, got: {result}"

    def test_compute_idempotency_key_still_works(self) -> None:
        """_compute_idempotency_key imported into logging_handler must still work correctly."""
        import amplifier_module_hook_context_intelligence.handlers.logging_handler as lh

        key = lh._compute_idempotency_key("test:event", "my-workspace", {"foo": "bar"})
        assert key.startswith("aci-event-v1:"), f"Expected aci-event-v1: prefix, got: {key}"
        assert len(key) > len("aci-event-v1:"), "Key must contain a hash after the prefix"
