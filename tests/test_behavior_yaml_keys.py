"""Test that context-intelligence.yaml uses the new key names."""

import yaml
import pathlib


BEHAVIOR_FILE = pathlib.Path(__file__).parent.parent / "behaviors" / "context-intelligence.yaml"


def load_yaml():
    return yaml.safe_load(BEHAVIOR_FILE.read_text())


def test_yaml_parses():
    """YAML file must parse without errors."""
    data = load_yaml()
    assert data is not None


def test_hook_config_uses_server_key():
    """hooks[].config must use 'server' key, not 'context_intelligence_server'."""
    data = load_yaml()
    hook_config = data["hooks"][0]["config"]
    assert "server" in hook_config, "Expected 'server' key in hook config"
    assert "context_intelligence_server" not in hook_config, (
        "'context_intelligence_server' key must be removed"
    )


def test_no_allow_workspaces_key():
    """The file must not contain 'allow_workspaces' (renamed to 'include')."""
    content = BEHAVIOR_FILE.read_text()
    assert "allow_workspaces" not in content, "'allow_workspaces' must be replaced with 'include'"


def test_no_deny_workspaces_key():
    """The file must not contain 'deny_workspaces' (renamed to 'exclude')."""
    content = BEHAVIOR_FILE.read_text()
    assert "deny_workspaces" not in content, "'deny_workspaces' must be replaced with 'exclude'"


def test_include_comment_present():
    """The file should have an 'include:' comment under server config."""
    content = BEHAVIOR_FILE.read_text()
    assert "# include:" in content, "Expected '# include:' comment in hook server config"


def test_exclude_comment_present():
    """The file should have an 'exclude:' comment under server config."""
    content = BEHAVIOR_FILE.read_text()
    assert "# exclude:" in content, "Expected '# exclude:' comment in hook server config"
