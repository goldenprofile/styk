"""Create a runnable starter plan without overwriting user files."""

import json
import sys
from pathlib import Path


def initialize(workspace, filename, backend):
    path = Path(filename)
    if not path.is_absolute():
        path = Path(workspace) / path
    plan = {
        "backend": backend,
        "max_attempts": 3 if backend == "pi" else 1,
        "timeout_seconds": 120 if backend == "pi" else 10,
        "tasks": [
            {
                "id": "scout", "role": "scout",
                "objective": "Inspect project instructions and list workspace entries. Do not modify files.",
                "acceptance_criteria": ["Report identifies project structure and instructions"],
            },
            {
                "id": "hello", "role": "builder", "dependencies": ["scout"],
                "objective": "Create hello.txt containing exactly Hello from Styk! followed by a newline. If it exists, return BLOCKED.",
                "acceptance_criteria": ["hello.txt contains the exact greeting"],
                "checks": [[sys.executable, "-c",
                            "from pathlib import Path; assert Path('hello.txt').read_text(encoding='utf-8') == 'Hello from Styk!\\n'"]],
            },
        ],
    }
    if backend == "pi":
        plan["pi_executable"] = "pi"
    # Exclusive creation also protects against concurrent initialization and symlinks.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return path.resolve()
