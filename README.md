# STG content-opportunity monitor

A scheduled, **headless, read-only** agent that watches Reddit for conversations
relevant to Scott Technology Group (STG), scores them for relevance, and drafts
reply comments **in David Scott's voice** into a review queue. A human reviews
the queue and posts manually.

**It never posts.** There is no Reddit-write code anywhere in this project; PRAW
runs read-only and the model calls are plain text-in/JSON-out with no tools.
Every output is a draft.

---

## How it works

```
ingest_reddit.py   →   agent.py            →   store.py        →   main.py
(PRAW, read-only)      (Anthropic SDK)         (SQLite/Supabase)    (report)

new posts + top-level   1. score relevance      queue kept           run summary:
comments + keyword         (cheap: Haiku)        opportunities,       seen / kept /
searches, dedup-aware   2. draft in voice        mark source seen     total_cost_usd
                           (strong: Opus),       (no posting)         + runs.log
                           only if it clears
                           the threshold
```

The deterministic parts (ingest, dedup, queue) are plain Python. Only the
*judgment* (relevance + voice drafting) uses the model — cheaper and more
reliable. The pipeline is idempotent: each source ID is marked seen once
processed, so the same thread is never drafted twice.

Voice rules live in [`voice-profile.md`](voice-profile.md) and are loaded fresh
into the drafting prompt every run — edit it without touching code. The
operating guardrails and JSON contract in [`CLAUDE.md`](CLAUDE.md) are mirrored
in the drafting/scoring system prompts in `agent.py`.

---

## Project layout

```
README.md            this file
requirements.txt
.env.example         copy to .env and fill in
config.yaml          subreddits, keywords, threshold, models, limits
voice-profile.md     David's voice (drop-in; referenced, loaded fresh)
CLAUDE.md            agent guardrails + output contract
src/
  main.py            orchestrates ingest -> score/draft -> queue -> report
  ingest_reddit.py   PRAW; new items since last run; dedup-aware
  agent.py           Anthropic SDK: relevance scoring + voice drafting (JSON out)
  store.py           SQLite (default) or Supabase; seen-sources + opportunities
  config.py          loads config.yaml + .env
migrations/
  001_init.sql       Supabase schema (SQLite creates its own tables)
```

---

## Setup

### 1. Python + dependencies

Requires **Python 3.10+**.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

> **Pure Python.** Scoring and drafting use the standard Anthropic SDK
> (`anthropic`) — plain single-shot model calls, no tools, no agent loop. There
> is no Node.js / CLI runtime to install.

### 2. Create a Reddit "script" app (free)

1. Go to <https://www.reddit.com/prefs/apps> and click **"create app"** (or
   "create another app") at the bottom.
2. Choose type **script**.
3. Name it anything (e.g. `stg-content-monitor`). Set the redirect URI to
   `http://localhost:8080` (required but unused for read-only).
4. After creating, note:
   - **client_id** — the short string just under the app name.
   - **client_secret** — the `secret` field.

This agent is read-only, so the Reddit username/password are optional
(app-only auth is enough). Update the `user_agent` in `config.yaml` to include
your Reddit username, per Reddit's API etiquette.

### 3. Environment variables

```bash
cp .env.example .env
```

Fill in `.env`:

| Key | Required | What |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Anthropic API key (API billing — **not** a claude.ai login). |
| `REDDIT_CLIENT_ID` | ✅ | From the Reddit script app. |
| `REDDIT_CLIENT_SECRET` | ✅ | From the Reddit script app. |
| `REDDIT_USERNAME` | — | Optional; leave blank for read-only. |
| `REDDIT_PASSWORD` | — | Optional; leave blank for read-only. |
| `SUPABASE_URL` | — | Set (with the key) to use Supabase instead of SQLite. |
| `SUPABASE_KEY` | — | Supabase service-role or anon key. |

### 4. Store

**Default: SQLite.** Nothing to do — the first run creates `./stg_monitor.db`
and its tables automatically.

**Optional: Supabase.** Set `SUPABASE_URL` + `SUPABASE_KEY` in `.env` and run
the schema once in the Supabase SQL editor:

```bash
# paste the contents of migrations/001_init.sql into the Supabase SQL editor
```

The code auto-detects the env vars and switches backends with no other changes.

---

## Run it

```bash
python src/main.py
```

Each run prints a summary and appends to `runs.log`, e.g.:

```
2026-06-27T20:00:00+00:00  run start | store=SQLite (stg_monitor.db) | subreddits=6
  ingested 42 new candidate(s)
  [kept] r/printers score=78 risk=low https://www.reddit.com/r/printers/...
2026-06-27T20:03:11+00:00  run done | seen=42 scored=42 kept=3 drafts=3 total_cost_usd=0.0184
```

`total_cost_usd` is computed from each call's token usage against a price table
in `agent.py` (update it if Anthropic prices change), summed for the run.

### Validate without Reddit (while Data API access is pending)

Reddit now gates Data API credentials behind an approval process (see
[`docs/reddit-api-application.md`](docs/reddit-api-application.md)). You can still
prove the whole scoring → drafting → store path end to end with no Reddit creds:

```bash
python scripts/mock_run.py        # Windows: C:\Agents\python\python.exe scripts\mock_run.py
```

It runs three canned, Reddit-style threads (a clear fit, a borderline one, an
off-topic one) through the real Anthropic API and writes the drafted ones to your
store tagged `source='mock'`. Needs only `ANTHROPIC_API_KEY` (plus Supabase
vars if you want it in Supabase; otherwise SQLite). Clean up after with
`DELETE FROM content_opportunities WHERE source = 'mock';`.

Reddit credentials are only required for the live `src/main.py` ingest — the app
loads and the mock test runs fine before they exist.

---

## Schedule it (run without your PC on)

Point cron / Task Scheduler at the venv's Python so dependencies resolve. Runs
every 4 hours.

**Linux / macOS (cron):** `crontab -e`

```cron
0 */4 * * * cd /path/to/social-media-scanner && /path/to/social-media-scanner/.venv/bin/python src/main.py >> runs.log 2>&1
```

**Windows (Task Scheduler):**

```powershell
# Run every 4 hours, no console window, whether or not you're logged in.
schtasks /Create /TN "STG Content Monitor" /SC HOURLY /MO 4 ^
  /TR "C:\path\to\social-media-scanner\.venv\Scripts\python.exe C:\path\to\social-media-scanner\src\main.py" ^
  /RL LIMITED /F
```

(Set the task's "Start in" directory to the project root so `config.yaml` and
`.env` are found. Paths in `config.py` are resolved relative to the repo, so the
agent finds `voice-profile.md` / `CLAUDE.md` regardless of the launch directory.)

---

## Review the queue

Kept opportunities land in `content_opportunities` with `status='pending'`.

**SQLite:**

```bash
sqlite3 stg_monitor.db \
  "SELECT relevance_score, selfpromo_risk, subreddit, permalink, draft_comment
   FROM content_opportunities
   WHERE status='pending'
   ORDER BY relevance_score DESC;"
```

**Supabase:** open the `content_opportunities` table in the dashboard, filter
`status = pending`, sort by `relevance_score` desc. Read `draft_comment`, check
`selfpromo_risk` and `notes_for_reviewer`, then post manually if it's good. Mark
rows you've handled by setting `status` to `posted` or `discarded`.

---

## Tuning

Everything tunable lives in `config.yaml`:

- `subreddits`, `keywords` — what to watch.
- `relevance_threshold` (default 60) — raise to be pickier, lower for more drafts.
- `max_drafts_per_run` (default 10) — cost cap on the drafting model per run.
- `models.scoring` / `models.drafting` — swap models. Defaults:
  `claude-haiku-4-5` (cheap scoring) and `claude-opus-4-8` (voice drafting).
  Drop drafting to `claude-sonnet-4-6` to cut cost.
- `max_posts_per_subreddit`, `max_comments_per_post`, keyword search limits —
  ingestion breadth vs Reddit rate limits.

---

## Design notes

- **Read-only by construction.** No posting tool, no Reddit write path, PRAW
  forced to `read_only = True`, model calls have no tools. Posting is a
  deliberate human step.
- **Twitter/X is out of scope for v1** (live monitoring needs the paid X API).
  The ingest layer is built behind a `fetch_candidates(cfg, store)` interface, so
  a future `src/ingest_twitter.py` drops in with no other changes.
- **Idempotent + re-runnable.** Dedup via the `seen_sources` ledger; failed
  evaluations are *not* marked seen, so they retry next run.
