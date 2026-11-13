"""Demo worker and Pi RPC transport. Neither transport is an OS sandbox."""

import asyncio
import json
import os
import shutil
from pathlib import Path

from .process import process_options, start_process, terminate


class Blocked(Exception):
    pass


def validate_result(value):
    if not isinstance(value, dict) or value.get("outcome") not in {"COMPLETED", "BLOCKED"}:
        raise ValueError("Worker must return outcome COMPLETED or BLOCKED")
    if not isinstance(value.get("summary"), str) or not value["summary"].strip():
        raise ValueError("Worker summary must be a nonempty string")
    if len(json.dumps(value).encode()) > 8192:
        raise ValueError("Worker result exceeds 8 KiB")
    if value["outcome"] == "BLOCKED":
        raise Blocked(value["summary"])
    return value


async def demo(task, workspace, attempt, config, emit):
    if task["role"] == "scout":
        names = sorted(p.name for p in workspace.iterdir() if p.name != ".agent-runs")
        return {"outcome": "COMPLETED", "summary": "Workspace entries: " + ", ".join(names)}
    path = workspace / "hello.txt"
    if path.exists():
        raise Blocked("Demo refuses to overwrite existing hello.txt")
    path.write_text("Hello from Styk!\n")
    return {"outcome": "COMPLETED", "summary": "Created hello.txt"}


async def pi(task, workspace, attempt, config, emit):
    tools = "read,grep,find,ls" if task["role"] == "scout" else "read,grep,find,ls,edit,write,bash"
    executable = config.get("pi_executable", "pi")
    if os.name == "nt":
        # Prefer Windows launchers over a co-located extensionless POSIX script.
        candidates = [executable + ext for ext in (".exe", ".cmd", ".bat")] if not Path(executable).suffix else []
        executable = next((found for candidate in [*candidates, executable]
                           if (found := shutil.which(candidate))), executable)
    argv = [executable, "--mode", "rpc", "--no-session",
            "--no-extensions", "--no-mcp", "--no-skills", "--no-prompt-templates",
            "--tools", tools]
    if config.get("model"):
        argv += ["--model", config["model"]]
    if config.get("provider"):
        argv += ["--provider", config["provider"]]
    inputs = []
    for path in sorted(attempt.parents[2].glob("*/attempts/*/report.md")):
        inputs.append(str(path))
    prompt = (
        "You are a worker, not a supervisor. Do not create subagents. "
        "Do not modify .agent-runs or unrelated files. Preserve existing user changes. "
        "Return ONLY JSON: {\"outcome\":\"COMPLETED\",\"summary\":\"...\"}. "
        "If blocked use outcome BLOCKED. Do not claim that checks ran unless they did.\n"
        + json.dumps(task, ensure_ascii=False)
        + "\nPrior reports (read only relevant sections): " + json.dumps(inputs)
    )
    stderr_path = attempt / "pi.stderr.log"
    with stderr_path.open("wb") as stderr, (attempt / "pi.events.jsonl").open("wb") as raw:
        try:
            process = await start_process(
                *argv, cwd=workspace, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=stderr, **process_options(),
                limit=8 * 1024 * 1024,
            )
        except FileNotFoundError as error:
            raise Blocked("Pi executable not found; install Pi or set pi_executable") from error
        try:
            process.stdin.write(json.dumps({"id": "work", "type": "prompt", "message": prompt}).encode() + b"\n")
            await process.stdin.drain()
            text = ""
            accepted = False
            while line := await process.stdout.readline():
                raw.write(line)
                raw.flush()
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("Pi RPC record must be a JSON object")
                kind = event.get("type")
                if kind == "response" and event.get("id") == "work":
                    if not event.get("success"):
                        raise Blocked(str(event.get("error", "Pi rejected prompt")))
                    if event.get("data", {}).get("disposition") == "handled":
                        raise Blocked("Pi handled prompt without starting a worker")
                    accepted = True
                elif kind == "message_end":
                    message = event.get("message", {})
                    if message.get("role") == "assistant":
                        if message.get("stopReason") in {"error", "aborted"}:
                            raise RuntimeError(message.get("errorMessage", "Pi model failed"))
                        text = "".join(x.get("text", "") for x in message.get("content", [])
                                       if x.get("type") == "text")
                elif kind == "tool_execution_start":
                    emit("TOOL_STARTED", {"tool": event.get("toolName")})
                elif kind == "agent_settled":
                    if event.get("aborted"):
                        raise RuntimeError("Pi worker aborted")
                    if not accepted:
                        raise RuntimeError("Pi settled without accepting prompt")
                    return validate_result(json.loads(text))
            raise RuntimeError("Pi exited before agent_settled; inspect pi.stderr.log")
        finally:
            await terminate(process)


BACKENDS = {"demo": demo, "pi": pi}
