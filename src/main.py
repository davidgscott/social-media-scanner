"""Orchestrator: ingest -> score/draft -> queue -> report.

Safe to re-run and idempotent: every candidate's source ID is marked seen once
processed, so the same thread is never scored or drafted twice. Read-only on
Reddit by construction — there is no posting code anywhere in this project.

Run:  python src/main.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from config import Config, load_config
from store import Store, open_store
import agent
from ingest_reddit import Candidate, fetch_candidates


def _log_line(cfg: Config, message: str) -> None:
    stamp = datetime.now(timezone.utc).isoformat()
    line = f"{stamp}  {message}"
    print(line)
    with open(cfg.log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


async def _process(
    cfg: Config, store: Store, candidates: list[Candidate]
) -> dict:
    seen = 0
    scored = 0
    kept = 0
    drafts = 0
    total_cost = 0.0

    for candidate in candidates:
        seen += 1
        try:
            evaluation = await agent.evaluate(candidate, cfg)
        except Exception as exc:
            print(f"  [warn] evaluate failed for {candidate.source_id}: {exc}")
            # Don't mark seen — let a future run retry this candidate.
            continue

        scored += 1
        total_cost += evaluation.cost_usd

        cleared = evaluation.relevance_score >= cfg.relevance_threshold
        has_draft = bool(evaluation.draft_comment)

        # Respect the per-run draft cap: once hit, we stop drafting but keep
        # marking items seen so we don't re-pay scoring on them next run.
        over_cap = drafts >= cfg.max_drafts_per_run

        if cleared and has_draft and not over_cap:
            store.add_opportunity(
                {
                    "source": "reddit",
                    "source_id": candidate.source_id,
                    "subreddit": candidate.subreddit,
                    "permalink": candidate.permalink,
                    "captured_at": candidate.captured_at,
                    "relevance_score": evaluation.relevance_score,
                    "rationale": evaluation.rationale,
                    "selfpromo_risk": evaluation.selfpromo_risk,
                    "draft_comment": evaluation.draft_comment,
                    "notes_for_reviewer": evaluation.notes_for_reviewer,
                    "status": "pending",
                }
            )
            kept += 1
            drafts += 1
            print(
                f"  [kept] r/{candidate.subreddit} score={evaluation.relevance_score} "
                f"risk={evaluation.selfpromo_risk} {candidate.permalink}"
            )

        store.mark_seen(candidate.source_id)

    return {
        "seen": seen,
        "scored": scored,
        "kept": kept,
        "drafts": drafts,
        "total_cost_usd": round(total_cost, 6),
    }


async def run() -> None:
    cfg = load_config()
    store = open_store(cfg)
    backend = "Supabase" if cfg.use_supabase else f"SQLite ({cfg.sqlite_path.name})"
    _log_line(cfg, f"run start | store={backend} | subreddits={len(cfg.subreddits)}")

    try:
        candidates = fetch_candidates(cfg, store)
        print(f"  ingested {len(candidates)} new candidate(s)")
        summary = await _process(cfg, store, candidates)
    finally:
        store.close()

    _log_line(
        cfg,
        "run done | "
        f"seen={summary['seen']} scored={summary['scored']} "
        f"kept={summary['kept']} drafts={summary['drafts']} "
        f"total_cost_usd={summary['total_cost_usd']}",
    )


if __name__ == "__main__":
    asyncio.run(run())
