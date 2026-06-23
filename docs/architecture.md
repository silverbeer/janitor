# Architecture

Janitor is a layered Typer application. Each layer has a single responsibility,
which keeps the code testable, typed, and easy to extend.

```
┌─────────────────────────────────────────────────────────────┐
│  main.py            global flags, logging, command wiring     │
├─────────────────────────────────────────────────────────────┤
│  commands/*.py      Typer sub-apps — parse args, render Rich  │
├─────────────────────────────────────────────────────────────┤
│  services/*.py      business logic — return Pydantic models   │
│  services/shell.py  the ONE subprocess seam (mocked in tests) │
├─────────────────────────────────────────────────────────────┤
│  models/*.py        typed Pydantic data structures            │
│  config.py          Pydantic Settings (file + env + defaults) │
│  utils/*.py         console, formatting, confirmation prompts │
└─────────────────────────────────────────────────────────────┘
```

## Layers

### `main.py` — entry point

Defines the root Typer app, the global options (`--dry-run`, `--yes`,
`--verbose`, `--log-json`, `--version`), and registers each command group with
`app.add_typer(...)`. The callback builds an `AppState` (config + shell runner)
and stashes it on `ctx.obj` so every command shares it.

### `commands/` — presentation

One module per command group. Commands are intentionally thin: they read
`ctx.obj`, call a service, and render the result with Rich. They contain **no**
subprocess or filesystem logic, which keeps them simple and lets the services be
unit-tested in isolation.

### `services/` — business logic

Each service wraps one external tool (`docker`, `brew`, `kubectl`, `supabase`)
or one concern (`disk`, `logs`, `system`). Services return Pydantic models, never
raw strings, so callers get typed, validated data.

The critical design choice is **`services/shell.py`**: every external command
flows through `ShellRunner.run()`. This gives us:

- **One mock seam.** Tests inject a `FakeRunner` (see `tests/conftest.py`) and
  stub command output by prefix — no real subprocesses required.
- **Centralized dry-run.** Commands marked `mutating=True` are skipped (and
  logged) when `--dry-run` is active. No command module re-implements this.
- **Centralized logging.** Every invocation is logged via structlog.

### `models/` — data

Plain Pydantic v2 models with derived properties (e.g. `DiskUsage.percent_used`,
`K3sStatus.failed_pods`, `DockerUsage.total_reclaimable`). Keeping computed logic
on the models keeps both services and commands declarative.

### `config.py` — configuration

`JanitorConfig` is a `pydantic_settings.BaseSettings`. Precedence, highest first:

1. CLI flags (applied in `main.py` after load).
2. Environment variables (`JANITOR_*`, `__` for nesting).
3. `~/.config/janitor/config.toml` (seeded via a custom settings source so it
   ranks *below* env but *above* defaults).
4. Field defaults.

## Safety model

- Destructive commands require confirmation via `utils.prompt.confirm`, which is
  short-circuited by `--yes`.
- `--dry-run` previews every mutating action without executing it.
- Every action is logged through structlog (`--log-json` for machine ingestion).

## Adding a new command group

Janitor is designed for the roadmap items (terraform, aws, certs, …). To add one:

1. **Model** — add typed results in `models/<area>.py`.
2. **Service** — add `services/<area>.py`; take a `ShellRunner` in `__init__`,
   return models, mark state-changing calls `mutating=True`.
3. **Command** — add `commands/<area>.py` as a `typer.Typer()` sub-app reading
   `ctx.obj` and rendering with Rich.
4. **Wire** — one line in `main.py`: `app.add_typer(area.app, name="area")`.
5. **Test** — unit-test the service with `FakeRunner`; add a CLI test with
   Typer's `CliRunner`.

No other layer needs to change. That is the extensibility contract.
