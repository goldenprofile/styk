# Repository Guidelines

## Project Structure & Module Organization

Styk is a Python 3.12+ prototype with no runtime dependencies.

- `src/styk/`: CLI, sequential runtime, storage, process management, and backends.
- `tests/`: deterministic unittest integration tests.
- `examples/`: demo and Pi JSON plans.
- `integrations/pi/styk.ts`: Pi extension with `/styk-run` and `/styk-cancel`.
- `docs/`: architectural proposals; README describes implemented behavior.

Keep configuration at the repository root. Create directories only as needed.

## Build, Test, and Development Commands

Run from the repository root:

- Install: `python3 -m venv .venv`, activate it, then `python -m pip install -e .`.
- Develop: `PYTHONPATH=src python3 -m styk --help`.
- Test: `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
- Syntax: `python3 -m compileall -q src tests`.
- Build: `python -m pip wheel --no-deps . --wheel-dir dist`.
- Lint/format (install Ruff separately): `ruff check src tests` and `ruff format --check src tests`.

GitHub Actions checks installation, tests, syntax, and the demo on Linux with Python 3.12.
Pi requires its own installation and authorization; its live integration is not covered by CI.

## Coding Style & Naming Conventions

Follow the selected language’s standard conventions. Configure a formatter and linter early, and use consistent indentation within each file type. Prefer descriptive names, small modules, and explicit interfaces. Avoid unrelated formatting changes. Commit dependency lockfiles when supported by the chosen package manager.

## Testing Guidelines

Use standard-library unittest; no coverage threshold is established. Add tests alongside new behavior and regression tests for bug fixes. Use descriptive test names that state the expected behavior, following the selected framework’s naming convention. Keep tests deterministic and document how to run them locally.

## Commit & Pull Request Guidelines

No Git history is available to establish existing commit conventions. Use concise, imperative commit subjects, such as `Add initial application scaffold`. Keep each commit focused.

Pull requests should explain the change, its purpose, and validation performed. Link relevant issues and include screenshots for visible interface changes. Explicitly note checks that could not be run.

## Security & Configuration

Never commit credentials, tokens, or private configuration. Provide placeholder values in an example configuration file and document required environment variables when configuration is introduced.
