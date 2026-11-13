"""Run with PYTHONPATH=src python3 -m styk."""

import argparse
import asyncio
import json
from pathlib import Path

from .runtime import Runtime
from .storage import load


def main():
    parser = argparse.ArgumentParser(description="Styk sequential worker prototype")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run").add_argument("plan", type=Path)
    commands.add_parser("resume").add_argument("run_id")
    commands.add_parser("status").add_argument("run_id", nargs="?")
    args = parser.parse_args()
    try:
        if not args.workspace.is_dir():
            raise ValueError("Workspace must be an existing directory")
        if args.command == "status":
            if args.run_id:
                print(json.dumps(Runtime.open(args.workspace, args.run_id).state, ensure_ascii=False, indent=2))
            else:
                for path in sorted((args.workspace / ".agent-runs").glob("r-*/state.json")):
                    state = load(path)
                    print(state["run_id"], state["status"])
            return 0
        runtime = (Runtime.create(args.workspace, load(args.plan)) if args.command == "run"
                   else Runtime.open(args.workspace, args.run_id))
        state = asyncio.run(runtime.execute(resume=args.command == "resume"))
        return 0 if state["status"] == "SUCCEEDED" else 1
    except KeyboardInterrupt:
        print("Cancelled; state and attempts saved.")
        return 130
    except (ValueError, OSError) as error:
        print(f"Error: {error}")
        return 2
