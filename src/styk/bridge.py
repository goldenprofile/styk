"""JSON-lines transport for the Pi supervisor extension."""

import asyncio
import json
import os
import sys
import threading

from .runtime import Runtime
from .storage import load


def publish(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def summarize(runtime):
    tasks = []
    for task_id, item in runtime.state["tasks"].items():
        task = {"id": task_id, **item}
        attempts = sorted((runtime.directory / "tasks" / task_id / "attempts").glob("*/result.json"))
        if attempts:
            result = load(attempts[-1])
            task["summary"] = str(result.get("summary", ""))[:1200]
            task["error"] = str(result.get("error", ""))[:2000]
            task["verification"] = result.get("verification", [])
            task["result_path"] = str(attempts[-1])
            task["report_path"] = str(attempts[-1].with_name("report.md"))
        tasks.append(task)
    return {"run_id": runtime.run_id, "status": runtime.state["status"],
            "directory": str(runtime.directory), "tasks": tasks}


def input_lines():
    # Avoid buffered stdin locks during interpreter shutdown while the daemon waits.
    pending = b""
    while chunk := os.read(sys.stdin.fileno(), 4096):
        pending += chunk
        while b"\n" in pending:
            line, pending = pending.split(b"\n", 1)
            yield line.decode("utf-8")
    if pending:
        yield pending.decode("utf-8")


async def execute(workspace, request, lines):
    action = request.get("action", "run")
    if action == "run":
        runtime = Runtime.create(workspace, request["plan"], quiet=True)
    elif action in {"resume", "status"}:
        runtime = Runtime.open(workspace, request["run_id"], quiet=True)
    else:
        raise ValueError("Unknown bridge action")
    if action == "status":
        publish({"type": "result", **summarize(runtime)})
        return 0
    original_emit = runtime.emit

    def emit(kind, data=None):
        original_emit(kind, data)
        publish({"type": "event", "run_id": runtime.run_id, "event": kind, **(data or {})})

    runtime.emit = emit
    publish({"type": "started", "run_id": runtime.run_id})
    loop = asyncio.get_running_loop()
    job = asyncio.create_task(runtime.execute(resume=action == "resume"))

    def cancel():
        if not job.done() and not job.cancelling():
            job.cancel()

    def listen():
        for line in lines:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get("cancel"):
                try:
                    loop.call_soon_threadsafe(cancel)
                except RuntimeError:
                    pass
                return

    threading.Thread(target=listen, daemon=True).start()
    try:
        await job
    except asyncio.CancelledError:
        pass
    except Exception as error:
        publish({"type": "error", "error": str(error), "run_id": runtime.run_id})
    publish({"type": "result", **summarize(runtime)})
    return 0 if runtime.state["status"] == "SUCCEEDED" else 1


def main(workspace):
    try:
        lines = input_lines()
        request = json.loads(next(lines, ""))
        if not isinstance(request, dict):
            raise ValueError("Bridge request must be an object")
        return asyncio.run(execute(workspace, request, lines))
    except (ValueError, KeyError, OSError, TypeError) as error:
        publish({"type": "error", "error": str(error)})
        return 2
