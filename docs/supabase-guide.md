# Supabase backup, restore & user sync with `jt`

A step-by-step guide to set up `jt supabase` on a new machine and run the key
commands: **back up** a database, **restore prod data** into your local stack,
and **sync prod users** so you can log in locally.

> Works for any Supabase project — `myrunstreak` (STK), `missing-table` (MT), and
> any future one. Onboarding a new project is just a config block.

---

## 1. What it does

| Command | Purpose |
|---|---|
| `jt supabase backup <project>` | Timestamped DB dump, auto-pruned to your retention |
| `jt supabase backups` | List dumps + flag any backup dir over its size/retention limits |
| `jt supabase restore-from-prod <project>` | **Reset local** to migrations, then load prod data into it |
| `jt supabase sync-users <project>` | Recreate prod auth users locally with **known passwords** (so login works) |

`restore-from-prod` brings the *data*; `sync-users` brings the *logins*. Run both
to get a local stack that mirrors prod and that you can actually sign into.

---

## 2. Prerequisites

Install these once per machine (e.g. the Mac mini):

```bash
# uv — installs Python for you
curl -LsSf https://astral.sh/uv/install.sh | sh

# Docker Desktop — the local Supabase stack needs it (start it before running)
# Supabase CLI — for `supabase db reset` / local stack
brew install supabase/tap/supabase

# Postgres client (pg_dump / psql) — required for backup + restore
brew install libpq && brew link --force libpq
```

> **Why libpq?** `restore-from-prod` shells out to `pg_dump`/`psql`. Without them
> you'll see: *“pg_dump / psql not found.”* The client version must be ≥ the
> server (prod is Postgres 17; `libpq` ships a current client).

---

## 3. Install `jt` (with the Supabase extra)

`sync-users` needs the Supabase Admin API client, shipped as an optional extra so
the core CLI stays lean:

```bash
uv tool install 'janitor-cli[supabase] @ git+https://github.com/silverbeer/janitor.git'
uv tool update-shell && source ~/.zshrc      # ensure ~/.local/bin is on PATH
jt --version
jt doctor                                     # confirms Docker, Supabase CLI, etc.
```

Already installed without the extra? Reinstall to add it:
`uv tool install 'janitor-cli[supabase] @ git+https://github.com/silverbeer/janitor.git' --reinstall`.

### Installing from a local checkout — you need `--no-cache`

Working on janitor itself? `--force` alone is **not enough**. uv will happily
reinstall from a cached build and keep serving the old code:

```bash
uv tool install --force --reinstall --no-cache ~/gitrepos/janitor
```

The failure is silent and confusing: `jt` reports errors whose wording no longer
exists in the source you are reading. If a fix you just made appears to have no
effect, check the installed copy before debugging your change:

```bash
grep -n "some new string" ~/.local/share/uv/tools/janitor-cli/lib/python*/site-packages/janitor/commands/supabase.py
```

---

## 4. Configure

Janitor reads **`~/.config/janitor/config.toml`** (NOT a file in any repo). Create
it from the example:

```bash
mkdir -p ~/.config/janitor
curl -fsSL https://raw.githubusercontent.com/silverbeer/janitor/main/config.example.toml \
  -o ~/.config/janitor/config.toml
```

Then add a project block. Example for **STK (one user)**:

```toml
[supabase.projects.stk]
# Where the repo lives on THIS machine (holds supabase/migrations). Lets jt
# resolve the project by the key "stk" even though the folder is "myrunstreak.run".
path = "~/gitrepos/myrunstreak.run"

# restore-from-prod
local_db_url   = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
prod_db_url_env = "STK_PROD_DATABASE_URL"     # NAME of an env var (not the value)
data_schemas   = ["public"]

# sync-users (Admin API)
prod_api_url          = "https://<your-ref>.supabase.co"
prod_service_key_env  = "STK_PROD_SERVICE_ROLE_KEY"
local_service_key_env = "STK_LOCAL_SERVICE_ROLE_KEY"

# Runs after a successful restore-from-prod, in the project path via `bash -lc`.
# stdio is inherited, so the hook's own prompts (including Touch ID) work.
post_restore_cmd = '''jt supabase sync-users stk && eval "$(supabase status -o env | sed 's/^/export SB_/')" && SUPABASE_URL="$SB_API_URL" SERVICE_KEY="$SB_SERVICE_ROLE_KEY" uv run python scripts/seed_local_users.py'''

# The keys here ARE the default sync list → STK syncs exactly this user.
[supabase.projects.stk.user_passwords]
"you@example.com" = "letmein"
```

### `post_restore_cmd` — one command instead of three

Without it, a fresh local needs `restore-from-prod`, then `sync-users`, then any
project-specific seeding. With it, `restore-from-prod` chains them itself, so the
DB is usable in one step. It runs only on a real restore — never on `--dry-run`,
and never if the restore failed. A non-zero exit from the hook fails the command.

### Project name: key vs folder

You refer to a project by its **config key** (`stk`) in every command. Because the
folder is `myrunstreak.run`, set `path` so the key resolves to the right repo. With
`path` set, `jt supabase backup stk` / `restore-from-prod stk` / `sync-users stk`
all work under the one name.

### Secrets are never stored in config

`prod_db_url_env` / `*_service_key_env` hold the **name** of an environment
variable — `jt` reads the secret from your environment at run time. Keep the config
file free of secrets. (The `user_passwords` values are throwaway local-dev
passwords, not secrets.)

---

## 5. Set the secret env vars

Get these from the Supabase dashboard → your project → **Settings → Database**
(connection string) and **Settings → API** (service-role key). Export them in your
shell, or keep them in a gitignored file you `source`:

```bash
# ~/.config/janitor/stk.env  (chmod 600; never commit)
export STK_PROD_DATABASE_URL='postgresql://postgres.<ref>:<pw>@aws-0-...pooler.supabase.com:6543/postgres'
export STK_PROD_SERVICE_ROLE_KEY='eyJ...'      # prod service_role key
export STK_LOCAL_SERVICE_ROLE_KEY='eyJ...'     # local service_role key (from `supabase status`)
```

```bash
source ~/.config/janitor/stk.env       # before running jt supabase commands
```

> The local service-role key comes from `supabase status` (run in the repo) — the
> `service_role key` line.

### Or: populate the file from 1Password with `jt secrets pull`

If your prod secrets live in a 1Password item (SB convention: one item `stk-prod`,
a field per secret — `db_url`, `service_role_key`), let `jt` write the env file for
you instead of copy-pasting. Add to the project's config block:

```toml
[supabase.projects.stk]
secrets_vault = "Private"     # your 1Password vault
secrets_item  = "stk-prod"    # item title; fields addressed as op://Private/stk-prod/<field>
```

Then:

```bash
jt secrets pull stk           # op read -> ~/.config/janitor/stk.env (chmod 600)
source ~/.config/janitor/stk.env
```

It resolves `prod_db_url_env` from `op://<vault>/<item>/db_url` and
`prod_service_key_env` from `.../service_role_key`, writes them shell-quoted, and
preserves any real `STK_LOCAL_SERVICE_ROLE_KEY` you already set (the local key isn't
in 1Password — it comes from `supabase status`). Requires the 1Password CLI (`op`)
installed and its desktop-app integration unlocked. `jt --dry-run secrets pull stk`
previews without reading any secret.

This is the preferred route for STK — it is one command, it never puts a secret
in your shell history, and it is the same path an agent can tell you to run.

### Use the pooler URL, not the direct host

Supabase gives you two connection strings. Only one of them works on a typical
home network:

| Host | DNS | Usable on IPv4-only? |
|---|---|---|
| `db.<ref>.supabase.co` (direct) | **AAAA only — no A record** | ✗ No route to host |
| `aws-N-<region>.pooler.supabase.com:6543` | A records (IPv4) | ✓ Yes |

Verified 2026-08-01: `db.<ref>.supabase.co` resolves to an IPv6 address and
publishes no A record at all, and the Mac mini has no global IPv6 address. The
direct host is therefore unreachable, and `pg_dump` fails with *"No route to
host"*. This is a property of the network, not a misconfiguration — nothing in
janitor can work around it.

Two things to get right in the pooler URL:

* the username is **`postgres.<ref>`**, not `postgres` — the pooler routes by
  tenant and rejects the bare username with a confusing tenant error
* the port is **6543** (session pooler), not 5432

```bash
export STK_PROD_DATABASE_URL='postgresql://postgres.<ref>:<pw>@aws-0-us-east-1.pooler.supabase.com:6543/postgres'
```

### When the pooler will not work either — the REST fallback

If the pooler rejects the tenant, STK keeps a REST-based restore that needs no
Postgres connection at all, only HTTPS:

```bash
cd ~/gitrepos/myrunstreak.run && ./scripts/restore_prod_to_local.sh
```

It dumps prod over the Supabase REST API with the service-role key, calls
`jt supabase sync-users stk`, then reseeds the coach/athlete fixtures. Slower and
STK-specific, but it works anywhere HTTPS works. It excludes auth users, OAuth
tokens and invites by design.

**Prefer `jt supabase restore-from-prod stk`.** Reach for the script only when the
pooler path is blocked — and if it turns out the pooler is permanently
unavailable here, the REST transport belongs in janitor rather than in one repo's
`scripts/` directory (SB-406, SB-516).

---

## 6. Run the commands

Start Docker + the local stack first (for STK, via its dev script):

```bash
cd ~/gitrepos/myrunstreak.run
./myrunstreak.sh db up          # or: supabase start
```

### Back up

```bash
jt supabase backup stk          # writes a timestamped dump, prunes old ones
jt supabase backups             # list dumps + sizes; warns if a dir is too big
```

### Restore prod data into local (destructive)

```bash
source ~/.config/janitor/stk.env
jt supabase restore-from-prod stk
```

Resets the local DB to migrations, then loads prod `public` data. **Wipes local
data first** — it confirms before doing so. A loopback guard refuses to run if
`local_db_url` isn’t local, so it can never touch prod.

If the project sets `post_restore_cmd` (STK does), user sync and dev seeding run
automatically at the end — you do not need the separate `sync-users` call below.

### Sync users (so you can log in)

```bash
source ~/.config/janitor/stk.env
jt supabase sync-users stk
```

Recreates the users listed in `user_passwords` locally, **preserving their prod
ids** so the data you just restored lines up. Log in with the email + the password
you set.

### Typical fresh-local flow

For a project with `post_restore_cmd` (STK) — one command, logins included:

```bash
source ~/.config/janitor/stk.env
jt supabase restore-from-prod stk
```

Without the hook, run both:

```bash
source ~/.config/janitor/stk.env
jt supabase restore-from-prod stk   # data
jt supabase sync-users stk          # logins
```

---

## 7. Global flags

```bash
jt --dry-run supabase restore-from-prod stk   # preview, mutate nothing
jt --yes supabase backup stk                  # skip confirmation (automation)
```

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `pg_dump / psql not found` | `brew install libpq && brew link --force libpq` |
| `Project 'stk' not found` | Set `supabase.projects.stk.path` to the repo on this machine. If it *is* set, your `jt` is stale — reinstall with `--no-cache` (see §3) |
| `Project 'stk' not found in search paths.` | Wording from a pre-SB-205 build. Reinstall with `--no-cache` |
| `Missing config for user sync` | Add `prod_api_url` + the `*_service_key_env` vars; the message lists exactly what's absent |
| `No prod DB URL for 'stk'` | Set `prod_db_url_env` in config and `export` that variable |
| `No route to host` / connection times out | You are using the direct `db.<ref>.supabase.co` host, which is IPv6-only. Switch to the pooler URL (§5) |
| `permission denied: "RI_ConstraintTrigger_…" is a system trigger` | Fixed in SB-517. Your `jt` predates it — reinstall (§3). The load no longer uses `--disable-triggers`, which needs a superuser Supabase does not give you |
| `duplicate key value violates unique constraint` during the load | Fixed in SB-518 — the reset now passes `--no-seed`, so dev fixtures no longer collide with prod rows. Reinstall (§3). Put fixtures in `post_restore_cmd` (§4), which runs after the load |
| Pooler rejects the tenant | Username must be `postgres.<ref>`, not `postgres`. Still failing? Use the REST fallback (§5) |
| `No Supabase projects found in configured search paths` | `~` in `search_paths` is expanded as of #14 — if you still see this, your `jt` predates it. Reinstall with `--no-cache` (§3) |
| Restore runs but login fails | Run `sync-users` after `restore-from-prod` — restore loads data, sync creates the auth login. Or set `post_restore_cmd` (§4) so it happens automatically |
| A code change to janitor has no effect | `uv tool install --force` served a cached build. Add `--no-cache` (§3) |
| Config changes ignored | Config must be at `~/.config/janitor/config.toml`, not in a repo |
