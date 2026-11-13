"""Bounded subprocess execution; cancellation terminates the process group."""

import asyncio
import os
import signal
import subprocess


def process_options():
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


async def terminate(process):
    if os.name == "nt":
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await killer.wait()
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        await process.wait()
        return
    # Kill descendants even if the group leader has already exited.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    await asyncio.sleep(0.15)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def start_process(*argv, **kwargs):
    """Reap a child even when cancellation arrives during process creation."""
    creation = asyncio.create_task(asyncio.create_subprocess_exec(*argv, **kwargs))
    try:
        return await asyncio.shield(creation)
    except asyncio.CancelledError:
        process = await creation
        await terminate(process)
        raise


async def check_command(argv, cwd, timeout, log):
    with log.open("wb") as output:
        process = await start_process(
            *argv, cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
            stdout=output, stderr=asyncio.subprocess.STDOUT,
            **process_options(),
        )
        try:
            await asyncio.wait_for(process.wait(), timeout)
            return {"argv": argv, "exit_code": process.returncode, "log": str(log)}
        finally:
            await terminate(process)
