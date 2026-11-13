"""Atomic state publication and a repository-scoped POSIX execution lock."""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load(path):
    return json.loads(Path(path).read_text())


@contextmanager
def workspace_lock(workspace):
    directory = Path(workspace) / ".agent-runs"
    directory.mkdir(exist_ok=True)
    with (directory / ".lock").open("a+") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("Another Styk run holds the workspace lock") from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def snapshot(workspace):
    """Content fingerprint, including untracked files, without following directory symlinks."""
    root = Path(workspace)
    digest = hashlib.sha256()
    ignored = {".git", ".agent-runs", "__pycache__", ".venv", "node_modules", ".ruff_cache"}
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in ignored)
        for name in sorted(files + [d for d in dirs if (Path(folder) / d).is_symlink()]):
            path = Path(folder) / name
            digest.update(str(path.relative_to(root)).encode())
            digest.update(b"\0")
            if path.is_symlink():
                digest.update(b"link:" + os.readlink(path).encode())
            elif path.is_file():
                digest.update(str(path.stat().st_mode & 0o777).encode())
                with path.open("rb") as stream:
                    while chunk := stream.read(65536):
                        digest.update(chunk)
            digest.update(b"\0")
    return digest.hexdigest()
