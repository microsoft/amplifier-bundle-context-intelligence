"""Guard the standalone CLI's declared runtime dependencies."""

import tomllib
import unittest
from pathlib import Path


class RuntimeDependencyTests(unittest.TestCase):
    def test_yaml_is_a_direct_runtime_dependency(self):
        project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
        metadata = tomllib.loads(project_file.read_text(encoding="utf-8"))
        requirements = {
            "".join(requirement.split()).lower()
            for requirement in metadata["project"]["dependencies"]
        }
        self.assertIn("pyyaml>=6.0", requirements)
