"""Bounded subprocess execution; cancellation terminates the process group."""

import asyncio
import os
import signal


async def terminate(process):
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


async def check_command(argv, cwd, timeout, log):
    with log.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            *argv, cwd=cwd, stdout=output, stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout)
            return {"argv": argv, "exit_code": process.returncode, "log": str(log)}
        finally:
            await terminate(process)
