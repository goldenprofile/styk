"""Sequential task runtime. State transitions and verification belong to the host."""

import asyncio
import json
import re
import time
import uuid
from pathlib import Path

from .backends import BACKENDS, Blocked, validate_result
from .process import check_command
from .storage import load, save, snapshot, workspace_lock


def validate_plan(plan):
    if not isinstance(plan, dict) or plan.get("backend") not in BACKENDS:
        raise ValueError("backend must be demo or pi")
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("tasks must be a nonempty list")
    seen = set()
    writing = False
    for task in tasks:
        name = task.get("id", "")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) or name in seen:
            raise ValueError("Task IDs must be unique safe identifiers")
        if task.get("role") not in {"scout", "builder"}:
            raise ValueError("Supported roles: scout, builder")
        if writing and task["role"] == "scout":
            raise ValueError("All scout tasks must precede builders in this prototype")
        writing |= task["role"] == "builder"
        if not isinstance(task.get("objective"), str) or not task["objective"].strip():
            raise ValueError("Each task needs an objective")
        criteria = task.get("acceptance_criteria")
        if not isinstance(criteria, list) or not criteria or not all(isinstance(c, str) and c.strip() for c in criteria):
            raise ValueError("Each task needs nonempty acceptance_criteria")
        dependencies = task.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(isinstance(d, str) and d in seen for d in dependencies):
            raise ValueError("Dependencies must refer to earlier tasks")
        checks = task.get("checks", [])
        if not isinstance(checks, list):
            raise ValueError("checks must be a list of argv arrays")
        if task["role"] == "builder" and not checks:
            raise ValueError("Builders require at least one independent check")
        for argv in checks:
            if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a for a in argv):
                raise ValueError("Each check must be a nonempty argv array")
        seen.add(name)
    for key, default, maximum in [("max_attempts", 3, 10), ("timeout_seconds", 120, 3600)]:
        value = plan.get(key, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{key} must be an integer between 1 and {maximum}")
    if plan.get("provider") and not plan.get("model"):
        raise ValueError("provider requires model")
    return plan


class Runtime:
    def __init__(self, workspace, run_id, state, quiet=False):
        self.workspace = Path(workspace).resolve()
        self.run_id = run_id
        self.directory = self.workspace / ".agent-runs" / run_id
        self.state = state
        self.quiet = quiet

    @classmethod
    def create(cls, workspace, plan, quiet=False):
        validate_plan(plan)
        run_id = "r-" + uuid.uuid4().hex[:12]
        state = {"schema_version": 1, "run_id": run_id, "status": "PENDING", "plan": plan,
                 "workspace": str(Path(workspace).resolve()), "tasks": {
                     task["id"]: {"status": "PENDING", "attempts": 0} for task in plan["tasks"]}}
        runtime = cls(workspace, run_id, state, quiet)
        runtime.directory.mkdir(parents=True)
        save(runtime.directory / "plan.json", plan)
        runtime.persist()
        return runtime

    @classmethod
    def open(cls, workspace, run_id, quiet=False):
        if not re.fullmatch(r"r-[a-f0-9]{12}", run_id):
            raise ValueError("Invalid run ID")
        directory = Path(workspace).resolve() / ".agent-runs" / run_id
        state = load(directory / "state.json")
        validate_plan(state["plan"])
        if state["workspace"] != str(Path(workspace).resolve()):
            raise ValueError("Run belongs to a different workspace")
        return cls(workspace, run_id, state, quiet)

    def persist(self):
        save(self.directory / "state.json", self.state)

    def emit(self, kind, data=None):
        event = {"time": time.time(), "run_id": self.run_id, "type": kind, **(data or {})}
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        if not self.quiet:
            print(f"[{self.run_id}] {kind} {json.dumps(data or {}, ensure_ascii=False)}", flush=True)

    async def execute(self, resume=False):
        with workspace_lock(self.workspace):
            if resume:
                if self.state["status"] == "SUCCEEDED":
                    return self.state
                expected = self.state.get("checkpoint")
                if expected is None or snapshot(self.workspace) != expected:
                    raise ValueError("Workspace differs from the last checkpoint; inspect changes and start a new run")
            self.state["status"] = "RUNNING"
            self.persist()
            try:
                for task in self.state["plan"]["tasks"]:
                    item = self.state["tasks"][task["id"]]
                    if item["status"] == "SUCCEEDED":
                        continue
                    await self.execute_task(task, item)
                    if item["status"] != "SUCCEEDED":
                        self.state["status"] = item["status"]
                        break
                else:
                    self.state["status"] = "SUCCEEDED"
            except asyncio.CancelledError:
                self.state["status"] = "CANCELLED"
                for item in self.state["tasks"].values():
                    if item["status"] == "RUNNING":
                        item["status"] = "CANCELLED"
                self.emit("RUN_CANCELLED")
                raise
            except Exception:
                self.state["status"] = "FAILED"
                for item in self.state["tasks"].values():
                    if item["status"] == "RUNNING":
                        item["status"] = "FAILED"
                raise
            finally:
                self.state["checkpoint"] = snapshot(self.workspace)
                self.persist()
                self.emit("RUN_FINISHED", {"status": self.state["status"]})
        return self.state

    async def execute_task(self, task, item):
        plan = self.state["plan"]
        limit = plan.get("max_attempts", 3)
        timeout = plan.get("timeout_seconds", 120)
        while item["attempts"] < limit:
            item["attempts"] += 1
            item["status"] = "RUNNING"
            self.persist()
            attempt = self.directory / "tasks" / task["id"] / "attempts" / f"a-{item['attempts']:02}"
            attempt.mkdir(parents=True)
            save(attempt / "task.json", task)
            before = snapshot(self.workspace)
            self.emit("TASK_STARTED", {"task_id": task["id"], "attempt": item["attempts"]})
            result = {"schema_version": 1, "run_id": self.run_id, "task_id": task["id"],
                      "attempt": item["attempts"], "input_snapshot_id": before,
                      "verification": [], "outcome": "FAILED"}
            try:
                execution_task = dict(task)
                if item.get("error"):
                    execution_task["previous_error"] = item["error"]
                    execution_task["previous_attempt"] = str(attempt.parent / f"a-{item['attempts'] - 1:02}")
                worker = await asyncio.wait_for(
                    BACKENDS[plan["backend"]](execution_task, self.workspace, attempt, plan, self.emit), timeout)
                worker = validate_result(worker)
                result["summary"] = worker["summary"]
                if task["role"] == "scout" and before != snapshot(self.workspace):
                    raise Blocked("Scout modified the workspace; inspect changes before continuing")
                for index, argv in enumerate(task.get("checks", [])):
                    self.emit("CHECK_STARTED", {"task_id": task["id"], "argv": argv})
                    check = await check_command(argv, self.workspace, timeout, attempt / f"check-{index}.log")
                    result["verification"].append(check)
                    self.emit("CHECK_FINISHED", {"task_id": task["id"], "exit_code": check["exit_code"]})
                    if check["exit_code"] != 0:
                        raise RuntimeError(f"Check failed: {argv}; see {check['log']}")
                if task["role"] == "scout" and before != snapshot(self.workspace):
                    raise Blocked("Scout checks modified the workspace; inspect changes")
                item["status"] = "SUCCEEDED"
                item.pop("error", None)
                result["outcome"] = "COMPLETED"
            except Blocked as error:
                item["status"] = "BLOCKED"
                item["error"] = str(error)
                result["outcome"] = "BLOCKED"
            except FileNotFoundError as error:
                item["status"] = "BLOCKED"
                item["error"] = f"Missing executable: {error.filename}"
                result["outcome"] = "BLOCKED"
            except asyncio.CancelledError:
                item["status"] = "CANCELLED"
                result["outcome"] = "CANCELLED"
                raise
            except (RuntimeError, ValueError, TimeoutError, OSError) as error:
                item["status"] = "FAILED"
                item["error"] = str(error) or type(error).__name__
            finally:
                result["output_snapshot_id"] = snapshot(self.workspace)
                result["error"] = item.get("error")
                report = result.get("summary", "") + "\n\n" + (result.get("error") or "")
                (attempt / "report.md").write_text(f"# {task['id']} attempt {item['attempts']}\n\n{report}\n", encoding="utf-8")
                result["artifact_refs"] = [str((attempt / "report.md").relative_to(self.directory))]
                save(attempt / "result.json", result)
                self.state["checkpoint"] = result["output_snapshot_id"]
                self.persist()
                self.emit("TASK_FINISHED", {"task_id": task["id"], "status": item["status"]})
            if item["status"] != "FAILED":
                return
            if item["attempts"] < limit:
                self.emit("RETRY_SCHEDULED", {"task_id": task["id"]})
