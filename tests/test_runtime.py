import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from styk.backends import BACKENDS
from styk.process import check_command
from styk.runtime import Runtime, validate_plan
from styk.storage import load, workspace_lock


def plan(backend="demo"):
    return {"backend": backend, "max_attempts": 2, "timeout_seconds": 3, "tasks": [{
        "id": "build", "role": "builder", "objective": "Write greeting",
        "acceptance_criteria": ["Greeting exists"],
        "checks": [[sys.executable, "-c", "from pathlib import Path; assert Path('hello.txt').exists()"]],
    }]}


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)

    async def test_full_demo_and_immutable_results(self):
        runtime = Runtime.create(self.workspace, plan(), quiet=True)
        state = await runtime.execute()
        self.assertEqual(state["status"], "SUCCEEDED")
        result_file = next(runtime.directory.glob("tasks/*/attempts/*/result.json"))
        result = result_file.read_bytes()
        self.assertEqual(load(result_file)["verification"][0]["exit_code"], 0)
        await runtime.execute(resume=True)
        self.assertEqual(result, result_file.read_bytes())

    async def test_failed_check_retries_with_diagnostic(self):
        calls = []

        async def worker(task, workspace, attempt, config, emit):
            calls.append(task)
            if len(calls) == 2:
                (workspace / "hello.txt").write_text("fixed")
            return {"outcome": "COMPLETED", "summary": "Done"}

        runtime = Runtime.create(self.workspace, plan(), quiet=True)
        with patch.dict(BACKENDS, {"demo": worker}):
            state = await runtime.execute()
        self.assertEqual(state["status"], "SUCCEEDED")
        self.assertIn("Check failed", calls[1]["previous_error"])
        self.assertEqual(len(list(runtime.directory.glob("tasks/*/attempts/*/result.json"))), 2)

    async def test_exhausted_attempts_do_not_restart_on_resume(self):
        config = plan()
        config["tasks"][0]["checks"] = [[sys.executable, "-c", "raise SystemExit(1)"]]
        runtime = Runtime.create(self.workspace, config, quiet=True)

        async def worker(*args):
            return {"outcome": "COMPLETED", "summary": "No change"}

        with patch.dict(BACKENDS, {"demo": worker}):
            await runtime.execute()
            state = await runtime.execute(resume=True)
        self.assertEqual(state["status"], "FAILED")
        self.assertEqual(state["tasks"]["build"]["attempts"], 2)

    async def test_resume_refuses_external_changes(self):
        runtime = Runtime.create(self.workspace, plan(), quiet=True)
        with patch.dict(BACKENDS, {"demo": lambda *args: asyncio.sleep(30)}):
            job = asyncio.create_task(runtime.execute())
            await asyncio.sleep(0.05)
            job.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await job
        self.assertEqual(load(runtime.directory / "state.json")["status"], "CANCELLED")
        (self.workspace / "user.txt").write_text("new user change")
        with self.assertRaisesRegex(ValueError, "differs"):
            await runtime.execute(resume=True)

    async def test_lock_rejects_second_run(self):
        runtime = Runtime.create(self.workspace, plan(), quiet=True)
        with workspace_lock(self.workspace):
            with self.assertRaisesRegex(ValueError, "lock"):
                await runtime.execute()

    async def test_missing_pi_is_blocked(self):
        config = plan("pi")
        config["pi_executable"] = str(self.workspace / "missing-pi")
        runtime = Runtime.create(self.workspace, config, quiet=True)
        state = await runtime.execute()
        self.assertEqual(state["status"], "BLOCKED")
        self.assertEqual(state["tasks"]["build"]["attempts"], 1)

    async def test_pi_waits_for_settled_and_ignores_agent_end(self):
        fake = self.workspace / "fake-pi"
        fake.write_text(f"#!{sys.executable}\n" + '''import json, sys
json.loads(sys.stdin.buffer.readline())
def emit(value):
    print(json.dumps(value), flush=True)
emit({"type":"response","id":"work","success":True,"data":{"disposition":"started"}})
emit({"type":"agent_end"})
emit({"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":json.dumps({"outcome":"COMPLETED","summary":"Report with \\u2028 separator"})}]}})
emit({"type":"agent_settled","aborted":False})
sys.stdin.buffer.read()
''')
        fake.chmod(0o755)
        create_process = asyncio.create_subprocess_exec

        async def launch_fake(*argv, **kwargs):
            if argv[0] == str(fake):
                argv = (sys.executable, str(fake))
            return await create_process(*argv, **kwargs)
        config = plan("pi")
        config["pi_executable"] = str(fake)
        config["tasks"][0].update(role="scout", checks=[])
        runtime = Runtime.create(self.workspace, config, quiet=True)
        with patch("styk.backends.asyncio.create_subprocess_exec", side_effect=launch_fake):
            state = await runtime.execute()
        self.assertEqual(state["status"], "SUCCEEDED")
        self.assertTrue(list(runtime.directory.glob("tasks/*/attempts/*/pi.events.jsonl")))

    async def test_check_timeout_terminates_child(self):
        with self.assertRaises(TimeoutError):
            await check_command([sys.executable, "-c", "import time; time.sleep(30)"],
                                self.workspace, 0.05, self.workspace / "check.log")

    def test_validation_rejects_path_traversal_and_unchecked_builder(self):
        config = plan()
        config["tasks"][0]["id"] = "../../escape"
        with self.assertRaises(ValueError):
            validate_plan(config)
        config = plan()
        config["tasks"][0]["checks"] = []
        with self.assertRaises(ValueError):
            validate_plan(config)


if __name__ == "__main__":
    unittest.main()
