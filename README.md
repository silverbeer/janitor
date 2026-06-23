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

### From source (uv — recommended)

```bash
git clone https://github.com/silverbeer/janitor.git
cd janitor
uv venv --python 3.14
uv pip install -e ".[dev]"
jt --help
```

### As a tool

```bash
uv tool install --from . janitor-cli   # exposes `jt` on your PATH
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
uv pip install -e ".[dev]"
ruff check .          # lint
ruff format .         # format
mypy                  # type check
pytest                # tests + coverage
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
