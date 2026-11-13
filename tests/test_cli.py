import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class InitializationTests(unittest.TestCase):
    def invoke(self, workspace, *args):
        return subprocess.run(
            [sys.executable, "-m", "styk", "--workspace", str(workspace), *args],
            capture_output=True, text=True, encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )

    def test_default_pi_plan_and_refusal_to_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            result = self.invoke(workspace, "init")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            path = workspace / "styk.json"
            original = path.read_bytes()
            self.assertEqual(json.loads(original)["backend"], "pi")
            result = self.invoke(workspace, "init")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse((workspace / ".agent-runs").exists())

    def test_generated_demo_runs_from_workspace_with_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / "test project"
            workspace.mkdir()
            result = self.invoke(workspace, "init", "demo plan.json", "--backend", "demo")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = self.invoke(workspace, "run", str(workspace / "demo plan.json"))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((workspace / "hello.txt").read_text(), "Hello from Styk!\n")
