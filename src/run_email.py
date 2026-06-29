"""Orchestrator for the automated email path: F5Bot inbox -> score/draft -> queue.

Reads new F5Bot alert emails, runs each Reddit hit through the same scoring and
voice-drafting pipeline used everywhere else, and writes the kept opportunities
to the store (Supabase or SQLite) as a ranked review list with status='pending'.

Idempotent and safe to schedule: dedup via the seen-store means re-runs never
re-draft the same thread. Read-only — nothing is ever posted.

Run:  python src/run_email.py
Schedule it nightly so the list is ready when you arrive in the morning.
"""

from __future__ import annotations

import ingest_email
import main as orchestrator  # reuse _process() and _log_line()
from config import load_config
from store import open_store


def run() -> None:
    cfg = load_config()
    store = open_store(cfg)
    backend = "Supabase" if cfg.use_supabase else f"SQLite ({cfg.sqlite_path.name})"
    orchestrator._log_line(cfg, f"email run start | store={backend} | inbox={cfg.imap_user or 'UNSET'}")

    try:
        candidates = ingest_email.fetch_candidates(cfg, store)
        print(f"  ingested {len(candidates)} new candidate(s) from F5Bot alerts")
        summary = orchestrator._process(cfg, store, candidates)
    finally:
        store.close()

    orchestrator._log_line(
        cfg,
        "email run done | "
        f"seen={summary['seen']} scored={summary['scored']} "
        f"kept={summary['kept']} drafts={summary['drafts']} "
        f"total_cost_usd={summary['total_cost_usd']}",
    )


if __name__ == "__main__":
    run()
