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
| `jt supabase` | Discover local projects, show status, timestamped backups + retention/size hygiene |
| `jt secrets`  | Shared Varlock + 1Password secret resolution (one schema convention across repos) |
| `jt k3s`      | Cluster/node/pod health, clean up completed jobs |
| `jt git`      | Delete merged branches, remove merged worktrees, daily via launchd |

Every destructive command honours **`--dry-run`** (preview) and **`--yes`**
(automation), and prompts for confirmation otherwise. Both are **global** flags —
they go before the subcommand:

```bash
jt --dry-run docker prune    # preview
jt --yes docker prune        # no prompt (automation)
```

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
jt docker prune --stale         # remove superseded image versions only
jt docker prune --aggressive    # remove all unused images + volumes + cache
jt docker images                # list images, flag dangling + stale
jt docker images --stale        # only superseded versions, with total
jt docker volumes               # list volumes, flag unused

```

**Three prune tiers.** Safe removes dangling images, stopped containers and
build cache. `--stale` adds superseded image versions — every tag that is not
`latest`, not referenced by any container (running *or* stopped), and not the
newest version of its repository. `--aggressive` removes everything Docker
considers unused, including images you built locally.

`--stale` exists because version-pinning tools sprawl: a `supabase` CLI upgrade
pulls new pinned images and never collects the old ones. One Mac mini had 18
versions of `realtime` on disk with 1 in use, and 84 GB of superseded Supabase
images that a safe prune could not touch.

```bash
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
jt supabase backup my-project   # timestamped DB dump (+ auto-prune to retention)
jt supabase backups             # list backups, sizes, flag retention/size breaches
jt supabase restore-from-prod my-project   # reset local + load prod data (destructive)
jt supabase sync-users my-project          # prod auth users -> local with known passwords

jt secrets base                 # write the shared base .env.schema (repos @import it)
jt secrets init [app]           # scaffold a repo .env.schema importing the base (+ gitignore fix)
jt secrets doctor               # lint name parity: .env.schema ↔ Helm secretKeyRef vars
jt secrets pull <project>       # materialize a project's prod secrets from 1Password -> env file (chmod 600)
jt secrets run -- <command>     # run a command with env resolved from 1Password (via varlock)

jt k3s status                   # nodes + pod health
jt k3s cleanup                  # delete completed jobs
jt k3s images                   # runtime images: in use / dangling / stale (+ build cache size)
jt k3s prune                    # remove unused dangling images + BuildKit build cache
jt k3s prune --no-build-cache   # images only
jt k3s prune --stale            # + superseded tags of a repo that has one in use
jt k3s trim                     # fstrim the VM so freed space returns to the host
jt k3s maintain                 # unattended: dangling images + build cache, then trim
jt k3s schedule install         # run `jt --yes k3s maintain` weekly, Sunday 04:00 (launchd)
jt k3s schedule status          # loaded? what did the last run do?

jt git audit                    # every repo under ~/gitrepos: what's prunable, what needs a human
jt git audit --repo .           # just the repo you're in (works inside a worktree)
jt git prune                    # delete merged branches + remove merged, clean worktrees
jt --dry-run git prune          # preview
jt git schedule install         # run `jt --yes git prune` daily at 03:30 (launchd)
jt git schedule status          # loaded? what did the last run do?
```

**`jt git` never deletes work that exists nowhere else.** A branch goes only
when its PR is merged *and* nothing was committed locally after; a worktree goes
only when it is unlocked, on such a branch, and has no changes except build
artefacts (`.venv`, `node_modules`…). Closed-unmerged PRs, commits after merge
and dirty worktrees are listed as **REVIEW** every run and left alone. Rules,
diagram and when to run it: [docs/git-hygiene.md](./docs/git-hygiene.md).

**k3s images on Rancher Desktop.** The runtime lives in a VM, reached with
`rdctl shell`. Two things make cleanup there easy to get wrong:

- **"In use" must include workload specs, not just containers.** A CronJob has
  no pod between runs; an image only its spec names (often a local build such as
  `trd:latest`, which can't be re-pulled) would look unused. `jt k3s` reads every
  Deployment, StatefulSet, DaemonSet, ReplicaSet, Job, CronJob and Pod spec from
  the `rancher-desktop` context (never the *current* context, which may be a
  remote cluster), and refuses to prune if any query fails.
- **Deleting inside the VM frees nothing on the Mac** until the VM discards the
  blocks. Run `jt k3s trim` after a prune. On one Mac mini, prune freed 17 GB
  inside the VM and the host gained nothing; trim then took the host from 22 to
  55 GB free.
- **Local builds leave cache the image list never shows.** `nerdctl build`
  stores its layers in BuildKit's own containerd namespace, outside the CRI, so
  `crictl images` can look tidy while the cache grows. On 2026-11-11 it reached
  54 GB and filled the Mac. `jt k3s prune` now drops it (`buildctl prune --all`;
  the cost is a slower next build), `jt k3s images` reports it, and `jt doctor`
  warns above `k3s.build_cache_warn_gb` (default 20).
- **Weekly is the cadence.** `jt k3s schedule install` runs `jt k3s maintain`
  every Sunday at 04:00: dangling images and build cache go (never `--stale`
  tags — that stays a human call), then the VM is trimmed. Weekly keeps the cache
  to about one week of builds while builds within the week stay fast. `jt doctor`
  shows the last run as **k3s upkeep** and warns if it failed or is over 10 days
  old. Day/time: `k3s.schedule_weekday|hour|minute` (0 = Sunday).

> `jt secrets run` wraps [varlock](https://varlock.dev/) (`npm install -g varlock`)
> to resolve secrets from 1Password locally. The cloud path is untouched — varlock
> never runs in a container; k8s injects secrets as env vars as before.

> `jt supabase sync-users` needs the Supabase Admin API client, which ships as
> an optional extra to keep the core CLI lean:
> `uv tool install 'janitor-cli[supabase] @ git+https://github.com/silverbeer/janitor.git'`

> **📖 Full walkthrough:** [docs/supabase-guide.md](./docs/supabase-guide.md) —
> step-by-step setup on a new machine (config, secrets, backup → restore →
> sync-users), with a copy-paste STK example and troubleshooting.

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
