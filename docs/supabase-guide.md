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

# The keys here ARE the default sync list → STK syncs exactly this user.
[supabase.projects.stk.user_passwords]
"you@example.com" = "letmein"
```

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

### Sync users (so you can log in)

```bash
source ~/.config/janitor/stk.env
jt supabase sync-users stk
```

Recreates the users listed in `user_passwords` locally, **preserving their prod
ids** so the data you just restored lines up. Log in with the email + the password
you set.

### Typical fresh-local flow

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
| `Project 'stk' not found` | Set `supabase.projects.stk.path` to the repo on this machine |
| `Missing config for user sync` | Add `prod_api_url` + the `*_service_key_env` vars; the message lists exactly what's absent |
| `No prod DB URL for 'stk'` | Set `prod_db_url_env` in config and `export` that variable |
| Restore runs but login fails | Run `sync-users` after `restore-from-prod` — restore loads data, sync creates the auth login |
| Config changes ignored | Config must be at `~/.config/janitor/config.toml`, not in a repo |
