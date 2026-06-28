# Deploy — Scott Technology server + Supabase

This runbook stands the monitor up on the Scott Technology server (compute) with
**Supabase as the store** (the draft queue you review in a browser). The agent
stays read-only and never posts.

Two halves: **Supabase once**, then **the server once**, then a cron schedule.
The default Linux path is below; a Windows Server section is at the end.

---

## Part 1 — Supabase (one time)

1. In the Supabase dashboard, open **SQL Editor** → **New query**.
2. Paste the entire contents of [`migrations/001_init.sql`](migrations/001_init.sql)
   and **Run**. This creates `content_opportunities` and `seen_sources`.
3. Get the connection values: **Project Settings → API**.
   - **Project URL** → `SUPABASE_URL`
   - **service_role key** (under "Project API keys") → `SUPABASE_KEY`.
     Use the **service_role** key, not the anon key — this is a trusted
     backend job that writes to the tables. Keep it secret; it goes only in the
     server's `.env`, never in git.

> The code auto-detects `SUPABASE_URL` + `SUPABASE_KEY`. When both are set it
> uses Supabase; when they're blank it falls back to local SQLite. No code
> change is needed to switch.

---

## Part 2 — The server (one time, Linux)

Assumes a Debian/Ubuntu-style box with the existing Python sync jobs. Adjust the
install directory to wherever those jobs live.

### 2a. Prerequisites

```bash
# Python 3.10+ and git. That's the whole runtime — no Node, no CLI.
python3 --version          # must be >= 3.10
sudo apt-get update && sudo apt-get install -y python3-venv git
```

### 2b. Clone and install

```bash
cd /opt                                     # or wherever your jobs live
git clone https://github.com/davidgscott/social-media-scanner.git
cd social-media-scanner
git checkout claude/project-setup-voice-profile-w7kgde   # until merged to main

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2c. Configure secrets

```bash
cp .env.example .env
nano .env        # fill in the values below
```

Fill in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...           # API billing key (not a claude.ai login)
REDDIT_CLIENT_ID=...                   # from a Reddit "script" app (see README)
REDDIT_CLIENT_SECRET=...
REDDIT_USERNAME=                       # leave blank — read-only
REDDIT_PASSWORD=                       # leave blank — read-only
SUPABASE_URL=https://xxxx.supabase.co  # from Part 1
SUPABASE_KEY=eyJ...                    # service_role key from Part 1
```

Then set your Reddit username in the user agent (API etiquette):

```bash
nano config.yaml        # edit reddit.user_agent -> replace u/CHANGE_ME
```

### 2d. Smoke test (one manual run)

```bash
.venv/bin/python src/main.py
```

You should see `store=Supabase` in the first log line, an "ingested N
candidate(s)" line, and a `run done | ... total_cost_usd=...` summary. Check the
`content_opportunities` table in Supabase for any rows with `status=pending`.

### 2e. Schedule it (every 4 hours)

A wrapper script, [`scripts/run.sh`](scripts/run.sh), cds into the project and
uses the venv. Add to crontab:

```bash
crontab -e
```

```cron
0 */4 * * * /opt/social-media-scanner/scripts/run.sh >> /opt/social-media-scanner/runs.log 2>&1
```

(Adjust both paths to your install directory.)

That's it. The agent now wakes every 4 hours, scores new Reddit threads, drafts
the good ones into Supabase, and exits.

---

## Reviewing the queue

Open the **`content_opportunities`** table in the Supabase dashboard, filter
`status = pending`, sort by `relevance_score` descending. For each row read
`draft_comment`, check `selfpromo_risk` and `notes_for_reviewer`, and if it's
good, **post it manually** on Reddit. Then mark the row: set `status` to
`posted` or `discarded` so it drops off your review list.

Posting is always a deliberate human step — the agent has no Reddit-write path.

---

## Operating notes

- **State lives in Supabase**, not on the server. The `seen_sources` ledger
  prevents re-drafting the same thread, and it survives server rebuilds.
- **Cost** per run is logged as `total_cost_usd` (Anthropic spend) in `runs.log`
  and stdout. With Haiku scoring + Opus drafting and a 10-draft cap, expect
  single-digit cents per run; watch the first few days and tune
  `relevance_threshold` / `max_drafts_per_run` in `config.yaml`.
- **Updating the code:** `cd /opt/social-media-scanner && git pull && .venv/bin/pip install -r requirements.txt`.
- **Editing voice or rules:** change `voice-profile.md` or `CLAUDE.md` and the
  next run picks them up — no restart, no redeploy.

---

## Windows Server (alternative)

If the Scott Technology server is Windows rather than Linux:

```powershell
# After installing Python 3.10+ (no Node needed). git is already present.
cd C:\apps
git clone https://github.com/davidgscott/social-media-scanner.git
cd social-media-scanner
git checkout claude/project-setup-voice-profile-w7kgde
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
notepad .env          # fill in the same keys as Part 2c
```

Schedule with Task Scheduler (every 4 hours, runs whether or not you're logged
in). Set the task's **Start in** directory to the project root:

```powershell
schtasks /Create /TN "STG Content Monitor" /SC HOURLY /MO 4 ^
  /TR "C:\apps\social-media-scanner\.venv\Scripts\python.exe C:\apps\social-media-scanner\src\main.py" ^
  /RL LIMITED /F
```
