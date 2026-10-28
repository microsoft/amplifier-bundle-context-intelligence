"""Module-level contract tests for tool-context-intelligence-transcript."""

from __future__ import annotations

import inspect
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
import context_intelligence.native_transcript as native


class TestModuleContract:
    def test_module_type_is_tool(self) -> None:
        from amplifier_module_tool_context_intelligence_transcript import __amplifier_module_type__

        assert __amplifier_module_type__ == "tool"

    def test_mount_is_coroutine(self) -> None:
        from amplifier_module_tool_context_intelligence_transcript import mount

        assert inspect.iscoroutinefunction(mount)

    async def test_mount_registers_only_session_transcript_and_returns_none(self) -> None:
        from amplifier_module_tool_context_intelligence_transcript import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()

        result = await mount(coordinator, config={})

        assert result is None
        coordinator.mount.assert_awaited_once()
        call = coordinator.mount.call_args
        assert call.args[0] == "tools"
        assert call.kwargs["name"] == "session_transcript"

    @pytest.mark.skipif(
        not native._SECURE_OPEN_SUPPORTED, reason="secure-open operations unavailable"
    )
    async def test_mount_delivers_trusted_config_to_registered_tool(self, tmp_path):
        from amplifier_module_tool_context_intelligence_transcript import mount

        (tmp_path / "metadata.json").write_text(
            json.dumps(
                {
                    "format": "context-intelligence",
                    "version": "1.0.0",
                    "session_id": "expected-full-id",
                    "workspace": "test",
                }
            ),
            encoding="utf-8",
        )
        (tmp_path / "events.jsonl").write_text(
            json.dumps(
                {
                    "event": "prompt:complete",
                    "timestamp": "t",
                    "data": {"response": "CONFIG-DELIVERED"},
                }
            ),
            encoding="utf-8",
        )
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(
            coordinator,
            {
                "capture_roots": {
                    "copy": {"directory": str(tmp_path), "session_id": "expected-full-id"}
                }
            },
        )
        tool = coordinator.mount.call_args.args[1]
        result = await tool.execute({"capture_root": "copy"})
        assert result.success, result.error
        assert result.output["sessions"][0]["messages"][0]["content"] == "CONFIG-DELIVERED"
        coordinator.get_capability.assert_not_called()

    @pytest.mark.skipif(
        not native._SECURE_OPEN_SUPPORTED, reason="secure-open operations unavailable"
    )
    async def test_mount_delivers_explicit_persisted_format_to_registered_tool(self, tmp_path):
        from amplifier_module_tool_context_intelligence_transcript import mount

        (tmp_path / "metadata.json").write_text('{"session_id":"stored-id"}', encoding="utf-8")
        (tmp_path / "transcript.jsonl").write_text(
            '{"role":"assistant","content":[{"type":"text","text":"FORMAT-DELIVERED"}]}\n',
            encoding="utf-8",
        )
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(
            coordinator,
            {
                "capture_roots": {
                    "copy": {
                        "directory": str(tmp_path),
                        "session_id": "stored-id",
                        "capture_format": "amplifier-transcript",
                    }
                }
            },
        )
        result = await coordinator.mount.call_args.args[1].execute({"capture_root": "copy"})
        assert result.success, result.error
        assert result.output["sessions"][0]["messages"][0]["content"] == [
            {"index": 0, "text": "FORMAT-DELIVERED"}
        ]
        assert result.output["sessions"][0]["source"]["source_format"] == "amplifier-transcript"
        coordinator.get_capability.assert_not_called()

    @pytest.mark.parametrize("config", [{"capture_roots": None}, {"capture_roots": []}, []])
    async def test_invalid_mount_config_is_not_silently_ignored(self, config):
        from amplifier_module_tool_context_intelligence_transcript import mount

        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        with pytest.raises(native.NativeTranscriptError) as error:
            await mount(coordinator, config)
        assert error.value.code == "invalid_capture_config"
        coordinator.mount.assert_not_called()

    async def test_unsupported_secure_open_fails_closed_without_affecting_default_mount(
        self, monkeypatch
    ):
        from amplifier_module_tool_context_intelligence_transcript import mount

        monkeypatch.setattr(native, "_SECURE_OPEN_SUPPORTED", False)
        coordinator = MagicMock()
        coordinator.mount = AsyncMock()
        await mount(coordinator, {})
        coordinator.mount.assert_awaited_once()
        coordinator.mount.reset_mock()
        with pytest.raises(native.NativeTranscriptError) as error:
            await mount(
                coordinator,
                {
                    "capture_roots": {
                        "copy": {"directory": "/srv/captures/copy", "session_id": "full-id"}
                    }
                },
            )
        assert error.value.code == "configured_capture_unsupported"
        coordinator.mount.assert_not_called()
