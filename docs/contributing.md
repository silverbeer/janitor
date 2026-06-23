# Contributing

Thanks for helping improve Janitor! This guide covers local setup, the quality
gates, and conventions.

## Setup

```bash
git clone https://github.com/silverbeer/janitor.git
cd janitor
uv sync --extra dev          # creates .venv, installs jt + dev tools
source .venv/bin/activate
```

> CI runs the gates with `uv run <tool>` (no activation needed). Locally you can
> do the same, or activate the venv once and call the tools directly.

## Quality gates

All of these run in CI and must pass before a PR is merged:

```bash
ruff check src tests        # lint
ruff format --check src tests  # format (drop --check to apply)
mypy                        # strict type checking
pytest                      # tests + coverage (target ≥ 90%)
```

Run them all in one go:

```bash
ruff check src tests && ruff format --check src tests && mypy && pytest
```

## Conventions

- **Python 3.14+, fully typed.** `mypy --strict` must pass; no untyped defs.
- **Services return models, commands render.** Never put subprocess or
  filesystem logic in a command module — it belongs in a service.
- **All shell calls go through `ShellRunner`.** This is the single mock seam and
  the place dry-run is enforced. Mark state-changing calls `mutating=True`.
- **Destructive actions confirm.** Use `utils.prompt.confirm` and honor `--yes`.
  Support `--dry-run` on any command that mutates state.
- **Tests for everything.** Unit-test services with `FakeRunner`
  (`tests/conftest.py`); add CLI tests with Typer's `CliRunner`. Mark end-to-end
  tests with `@pytest.mark.integration`.

## Commits & PRs

- Keep PRs focused; one logical change per PR.
- Reference the relevant roadmap item if applicable.
- CI runs on Linux and macOS for Python 3.14.

## Adding a command group

See [architecture.md](./architecture.md#adding-a-new-command-group) for the
five-step contract (model → service → command → wire → test).
