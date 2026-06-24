# Janitor 🧹

> A Swiss Army knife for workstation and platform housekeeping.

**Janitor** (`jt`) automates common maintenance, cleanup, health checks, and
operational tasks across local machines, Docker, Kubernetes (k3s), Homebrew,
and developer tooling. Built for developers, SREs, platform engineers, and
homelab operators.

[![CI](https://github.com/silverbeer/janitor/actions/workflows/ci.yml/badge.svg)](https://github.com/silverbeer/janitor/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.14%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Features

| Command group | What it does |
|---------------|--------------|
| `jt doctor`   | One-shot system health summary (disk, Docker, Homebrew, Kubernetes, Supabase, Python, uv) |
| `jt docker`   | Inspect usage, list images/volumes, prune reclaimable space (safe + aggressive) |
| `jt disk`     | Filesystem usage, largest files/dirs, common space offenders |
| `jt brew`     | Outdated packages, upgrades, cleanup of old versions |
| `jt logs`     | Find large logs, delete stale ones |
| `jt supabase` | Discover local projects, show status, timestamped backups |
| `jt k3s`      | Cluster/node/pod health, clean up completed jobs |

Every destructive command supports **`--dry-run`** (preview) and **`--yes`**
(automation), and prompts for confirmation otherwise.

## Installation

> **Prerequisite:** [uv](https://docs.astral.sh/uv/) — it installs Python 3.14
> for you, so it's the only thing you need first:
>
> ```bash
> curl -LsSf https://astral.sh/uv/install.sh | sh
> ```
>
> Then restart your shell (or `source ~/.zshrc`) so `uv` and `~/.local/bin`
> are on your `PATH`.

### Quick install — global `jt` (recommended)

No clone required. Installs the `jt` command straight onto your `PATH`:

```bash
uv tool install --from git+https://github.com/silverbeer/janitor.git janitor-cli
jt --help
jt doctor
```

Update later with `uv tool upgrade janitor-cli`; remove with
`uv tool uninstall janitor-cli`.

### Set up on a new machine (e.g. a second Mac)

Janitor is per-machine — install it on every box you want to keep tidy
(MacBook Air, Mac mini, homelab nodes). On a fresh machine:

```bash
# 1. Install uv (skip if already present)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Install jt globally — uv fetches Python 3.14 + all deps
uv tool install --from git+https://github.com/silverbeer/janitor.git janitor-cli

# 3. Ensure ~/.local/bin is on PATH, then reload your shell
uv tool update-shell
source ~/.zshrc          # or open a new terminal

# 4. Verify
jt --version
jt doctor                # confirms Docker, Homebrew, k8s, Supabase, disk
```

> **If `jt: command not found`:** the install succeeded but `~/.local/bin`
> isn't on your `PATH`. `uv tool update-shell` fixes this; restart the shell
> afterward. Confirm with `which jt` → `~/.local/bin/jt`.

Keep machines in sync by re-running `uv tool upgrade janitor-cli` on each after
a new release.

### From a clone (for development)

```bash
git clone https://github.com/silverbeer/janitor.git
cd janitor
uv sync --extra dev          # creates .venv, installs jt + dev tools
source .venv/bin/activate    # puts `jt` on your PATH for this shell
jt --help
```

### Homebrew (planned)

```bash
brew install silverbeer/tap/janitor    # future — see Roadmap
```

## Usage

```bash
jt doctor                       # full health summary
jt version                      # show version

jt docker status                # docker system df
jt docker reclaim               # how much is reclaimable
jt docker prune                 # safe prune (confirms first)
jt docker prune --aggressive    # remove all unused images + volumes + cache
jt docker images                # list images, flag dangling
jt docker volumes               # list volumes, flag unused

jt disk usage /                 # filesystem utilization
jt disk largest-files ~ -n 30   # 30 biggest files under home
jt disk largest-dirs ~          # biggest directories
jt disk reclaim                 # highlight caches / node_modules / build artifacts

jt brew status                  # outdated packages
jt brew upgrade                 # upgrade everything
jt brew cleanup                 # prune old versions

jt logs size                    # large log files
jt logs clean --max-age 14      # delete logs older than 14 days

jt supabase list                # discover + status
jt supabase backup my-project   # timestamped DB dump

jt k3s status                   # nodes + pod health
jt k3s cleanup                  # delete completed jobs
```

### Global flags

```bash
jt --dry-run docker prune       # preview only, never mutates
jt --yes brew cleanup           # no prompts (automation / cron)
jt --verbose doctor             # DEBUG-level structured logs
jt --log-json doctor            # JSON logs for ingestion
```

## Configuration

Janitor reads `~/.config/janitor/config.toml` (or `$XDG_CONFIG_HOME/janitor/config.toml`).
Copy [`config.example.toml`](./config.example.toml) to get started.

Precedence (highest first): **CLI flags → environment (`JANITOR_*`) → config file → defaults**.

```toml
[disk]
scan_paths = ["~/"]
top_n = 20
min_size_mb = 100

[logs]
paths = ["/var/log", "~/Library/Logs"]
max_age_days = 30
```

Environment overrides use the `JANITOR_` prefix with `__` for nesting:

```bash
export JANITOR_DRY_RUN=true
export JANITOR_DISK__TOP_N=50
```

## Architecture

```
src/janitor/
├── main.py          # Typer app: global flags + command wiring
├── config.py        # Pydantic Settings (file + env + defaults)
├── logging.py       # structlog configuration
├── context.py       # AppState passed via Typer ctx.obj
├── version.py       # single source of version truth
├── models/          # Pydantic models (typed results)
├── services/        # business logic — thin, testable wrappers over CLIs
│   └── shell.py     # the single subprocess seam (mock here in tests)
├── commands/        # Typer sub-apps — render service output with Rich
└── utils/           # console, formatting, prompts
```

**Design principles:**

- **One subprocess seam.** Every external command flows through `ShellRunner`,
  so tests mock one place and `--dry-run` is enforced centrally.
- **Services return models, commands render.** Business logic is decoupled from
  presentation, making services trivially unit-testable.
- **Typed end to end.** `mypy --strict` clean; Pydantic models everywhere.
- **Extensible by convention.** Add a command group by dropping a `services/x.py`
  + `commands/x.py` and registering one `app.add_typer(...)` line.

See [`docs/architecture.md`](./docs/architecture.md) for the full write-up and
[`docs/screenshots.md`](./docs/screenshots.md) for sample output.

## Development

```bash
uv sync --extra dev       # set up .venv with dev tools
uv run ruff check .       # lint
uv run ruff format .      # format
uv run mypy               # type check
uv run pytest             # tests + coverage
```

See [`docs/contributing.md`](./docs/contributing.md).

## Roadmap

Extension points are designed in now; planned modules:

- `jt terraform` — state/plan hygiene
- `jt gha` — GitHub Actions cache & artifact maintenance
- `jt aws` — account health checks
- `jt certs` — TLS certificate expiration checks
- `jt tailscale` — node/ACL management
- `jt models` — local AI model management
- `jt backup` — backup automation
- `jt schedule` — scheduled housekeeping jobs

## License

MIT — see [`LICENSE`](./LICENSE).
