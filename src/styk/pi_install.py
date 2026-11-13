"""Install the bundled, standalone Pi extension."""

from pathlib import Path


def install_extension(destination=None, force=False):
    path = Path(destination) if destination else Path.home() / ".pi/agent/extensions/styk.ts"
    source = Path(__file__).with_name("pi") / "styk.ts"
    content = source.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == content:
        return path.resolve()
    with path.open("wb" if force else "xb") as stream:
        stream.write(content)
    return path.resolve()
