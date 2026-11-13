import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from styk.initialization import initialize
from styk.pi_install import install_extension
from styk.storage import load


class BridgeTests(unittest.TestCase):
    def command(self, workspace):
        return [sys.executable, "-m", "styk", "--workspace", str(workspace), "bridge"]

    def request(self, workspace, request):
        process = subprocess.run(self.command(workspace), input=json.dumps(request) + "\n",
                                 text=True, capture_output=True, timeout=20)
        return process, [json.loads(line) for line in process.stdout.splitlines()]

    def test_run_and_status_return_verified_reports_without_plan_file(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            initialize(workspace, "temporary.json", "demo")
            plan = load(workspace / "temporary.json")
            (workspace / "temporary.json").unlink()
            process, records = self.request(workspace, {"plan": plan})
            self.assertEqual(process.returncode, 0, process.stderr)
            result = records[-1]
            self.assertEqual(result["status"], "SUCCEEDED")
            self.assertEqual(result["tasks"][-1]["verification"][0]["exit_code"], 0)
            self.assertTrue(Path(result["tasks"][-1]["report_path"]).exists())
            process, records = self.request(workspace, {"action": "status", "run_id": result["run_id"]})
            self.assertEqual(records[-1]["status"], "SUCCEEDED")
            self.assertFalse((workspace / "styk.json").exists())

    def test_invalid_plan_is_rejected_before_work_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            process, records = self.request(folder, {"plan": {"backend": "pi", "tasks": []}})
            self.assertEqual(process.returncode, 2)
            self.assertEqual(records[-1]["type"], "error")
            self.assertFalse((Path(folder) / ".agent-runs").exists())

    def test_cancellation_over_stdin_saves_checkpoint_and_releases_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            initialize(workspace, "plan.json", "demo")
            plan = load(workspace / "plan.json")
            plan["tasks"][-1]["checks"] = [[sys.executable, "-c", "import time; time.sleep(30)"]]
            plan["timeout_seconds"] = 60
            process = subprocess.Popen(self.command(workspace), stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write(json.dumps({"plan": plan}) + "\n")
                process.stdin.flush()
                records = []
                for line in process.stdout:
                    record = json.loads(line)
                    records.append(record)
                    if record.get("event") == "CHECK_STARTED":
                        break
                process.stdin.write('{"cancel":true}\n')
                process.stdin.flush()
                output, error = process.communicate(timeout=15)
                records.extend(json.loads(line) for line in output.splitlines())
                self.assertEqual(process.returncode, 1, error)
                self.assertEqual(records[-1]["status"], "CANCELLED")
                directory = Path(records[-1]["directory"])
                self.assertIn("checkpoint", load(directory / "state.json"))
                from styk.storage import workspace_lock
                with workspace_lock(workspace):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def test_extension_is_bundled_and_existing_custom_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "extensions/styk.ts"
            install_extension(destination)
            self.assertIn("styk_run", destination.read_text(encoding="utf-8"))
            install_extension(destination)
            destination.write_text("custom extension")
            with self.assertRaises(FileExistsError):
                install_extension(destination)
            self.assertEqual(destination.read_text(), "custom extension")
            install_extension(destination, force=True)
            self.assertIn("styk_run", destination.read_text(encoding="utf-8"))
