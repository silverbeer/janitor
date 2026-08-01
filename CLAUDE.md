# janitor — `jt`, workstation & platform housekeeping

A Typer CLI for the chores that accumulate across machines: Docker/brew/disk
cleanup, k3s checks, log tailing, secret scanning, and Supabase backup/restore.
Full design: [docs/architecture.md](docs/architecture.md).

## Commands

```bash
uv sync --extra dev              # install (creates .venv with jt + dev tools)
uv run pytest                    # tests + coverage (target ≥ 90%)
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy                      # strict — no untyped defs
```

All four gates run in CI and must pass before merge.

## Installing the CLI you are editing

```bash
uv tool install --force --reinstall --no-cache ~/gitrepos/janitor
```

**`--no-cache` is not optional.** Without it uv reinstalls from a cached build
and silently keeps serving old code — the symptom is `jt` printing error text
that no longer exists in the source. Before debugging "my fix did nothing",
confirm what is actually installed:

```bash
grep -rn "<a string you just added>" \
  ~/.local/share/uv/tools/janitor-cli/lib/python*/site-packages/janitor/
```

## Architecture rules (enforce in review)

- **Commands render, services decide.** `commands/*.py` read `ctx.obj`, call a
  service, print with Rich. No subprocess or filesystem logic in a command.
- **Every external command goes through `ShellRunner.run()`**
  ([services/shell.py](src/janitor/services/shell.py)). It is the single mock
  seam, the central `--dry-run` gate (`mutating=True` calls are skipped), and the
  logging point. A service that shells out directly breaks all three.
- **Services return Pydantic models, never raw strings.** Computed logic lives on
  the model (`DiskUsage.percent_used`, `K3sStatus.failed_pods`).
- **Config is `pydantic_settings`** ([config.py](src/janitor/config.py)), read
  from `~/.config/janitor/config.toml` — never a file inside a repo.
- **Secrets are referenced by env-var NAME, never by value.** Config holds
  `prod_db_url_env = "STK_PROD_DATABASE_URL"`; `jt` reads the value at run time.
  A secret in `config.toml` is a bug.
- **Destructive commands confirm** via `utils/prompt.confirm` and honour
  `--yes`. Anything that targets a database also needs a loopback guard proving
  the target is local (see `restore_from_prod`).
- Tests inject `FakeRunner` from `tests/conftest.py` and stub output by command
  prefix. No test spawns a real subprocess.

### Config paths: use `ExpandedPath`, never bare `Path`

`~` in a TOML value does not expand on its own — `Path("~/gitrepos").is_dir()` is
always `False`, and `discover()` skips missing directories in silence, so the
symptom is a confident "no projects found" while the project sits right there.

`config.py` solves this once, at parse time:

```python
ExpandedPath = Annotated[Path, AfterValidator(lambda p: p.expanduser())]
```

**Any new path-shaped config field must use `ExpandedPath`** (or
`list[ExpandedPath]`), not `Path`. Do not add another use-site `.expanduser()` —
that is the pattern this replaced.

### First rule when `jt` misbehaves: check what is installed

If `jt` prints an error whose wording is not in the source you are reading, or a
fixed bug appears to still be present, your installed copy or your checkout is
stale. Both happened on 2026-08-01: a checkout two commits behind `origin/main`
produced a "bug report" for something already fixed in #14.

```bash
git -C ~/gitrepos/janitor fetch origin && git -C ~/gitrepos/janitor status -sb
uv tool install --force --reinstall --no-cache ~/gitrepos/janitor
```

Verify the source before diagnosing the behaviour.

## Supabase: prod → local

The full guide is [docs/supabase-guide.md](docs/supabase-guide.md). The short
version, and the parts that are easy to get wrong:

```bash
source ~/.config/janitor/stk.env          # exports STK_PROD_DATABASE_URL etc.
jt supabase restore-from-prod stk         # resets local, loads prod, syncs users
```

- `restore-from-prod` brings **data**; `sync-users` brings **logins**. A project
  with `post_restore_cmd` (STK has one) chains both, so one command is enough.
- **Use the pooler URL, not the direct host.** `db.<ref>.supabase.co` publishes
  only an AAAA record; on an IPv4-only network it is unreachable and `pg_dump`
  fails with "No route to host". The pooler has A records. Username must be
  `postgres.<ref>`, port 6543.
- **REST fallback:** if the pooler rejects the tenant,
  `~/gitrepos/myrunstreak.run/scripts/restore_prod_to_local.sh` does the same job
  over HTTPS. It is STK-specific and slower; prefer `jt`. If the pooler turns out
  to be permanently unavailable, that transport belongs in janitor (SB-406,
  SB-516).
- Both paths need 1Password (`op`) for secrets, which requires biometric auth —
  **they cannot run in a non-interactive agent shell.** Ask the user to run them.

## Adding a project to `jt supabase`

Config only, no code. In `~/.config/janitor/config.toml`:

```toml
[supabase.projects.<key>]
path            = "/absolute/path/to/repo"   # NOT ~ — see the trap above
local_db_url    = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
prod_db_url_env = "<KEY>_PROD_DATABASE_URL"  # env var NAME
data_schemas    = ["public"]
```

The config key is what every command takes (`jt supabase backup <key>`), so it
can differ from the folder name — that is why `path` exists.
