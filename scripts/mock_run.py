"""Mock pipeline test — validate scoring + drafting + store WITHOUT Reddit.

Feeds a few canned, Reddit-style items through the same agent.evaluate() and
store path the live pipeline uses, against the real Anthropic API. This proves
the expensive/uncertain half of the system end to end (relevance scoring, voice
drafting, cost tracking, the Supabase/SQLite queue) while Reddit Data API access
is still pending.

Needs only ANTHROPIC_API_KEY (and SUPABASE_URL/KEY if you want it to write to
Supabase; otherwise it falls back to local SQLite). No Reddit creds required.

Run (Windows):  C:\\Agents\\python\\python.exe scripts\\mock_run.py
Run (Linux):    .venv/bin/python scripts/mock_run.py

The rows it writes are tagged source='mock' so you can clean them up afterwards:
    DELETE FROM content_opportunities WHERE source = 'mock';
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import agent  # noqa: E402
from config import load_config  # noqa: E402
from ingest_reddit import Candidate  # noqa: E402
from store import open_store  # noqa: E402

# Three samples spanning the relevance range: a clear fit, a borderline fit, and
# an off-topic item that should score low and produce no draft.
SAMPLES = [
    Candidate(
        source_id="mock_t3_relevant_lease",
        kind="post",
        subreddit="printers",
        permalink="https://www.reddit.com/r/printers/comments/mock_relevant",
        title="Copier lease ending in 60 days — buy out, re-lease, or go MPS?",
        body=(
            "Our 5-year lease on two Canon imageRUNNER MFPs is up in 60 days. The "
            "vendor is pushing a new managed print services contract with a "
            "per-page rate. We run about 12k pages/month across 30 staff. Is it "
            "smarter to buy the machines out, sign a new lease, or move to MPS? "
            "What gotchas should I watch for in the contract language?"
        ),
        captured_at="2026-06-27T00:00:00+00:00",
    ),
    Candidate(
        source_id="mock_t3_borderline_papercut",
        kind="post",
        subreddit="k12sysadmin",
        permalink="https://www.reddit.com/r/k12sysadmin/comments/mock_borderline",
        title="PaperCut renewal jumped — cheaper way to track print costs + secure release?",
        body=(
            "We use PaperCut NG to track print/copy by department and do secure "
            "release at the MFPs, but the renewal quote went up a lot. K-12, ~400 "
            "staff. Anyone moved to something cheaper that still does quotas and "
            "badge release? Trying to avoid ripping out the fleet."
        ),
        captured_at="2026-06-27T00:00:00+00:00",
    ),
    Candidate(
        source_id="mock_t3_offtopic_crm",
        kind="post",
        subreddit="smallbusiness",
        permalink="https://www.reddit.com/r/smallbusiness/comments/mock_offtopic",
        title="Best simple CRM for a 3-person landscaping company?",
        body=(
            "Just want something simple to track leads and send invoices. Not very "
            "technical. What do you all use and what does it cost?"
        ),
        captured_at="2026-06-27T00:00:00+00:00",
    ),
]


def main() -> None:
    cfg = load_config()
    store = open_store(cfg)
    backend = "Supabase" if cfg.use_supabase else f"SQLite ({cfg.sqlite_path.name})"
    print(
        f"mock run | store={backend} | threshold={cfg.relevance_threshold} "
        f"| scoring={cfg.scoring_model} | drafting={cfg.drafting_model}\n"
    )

    total_cost = 0.0
    written = 0
    try:
        for cand in SAMPLES:
            ev = agent.evaluate(cand, cfg)
            total_cost += ev.cost_usd

            print(f"r/{cand.subreddit}  score={ev.relevance_score}  risk={ev.selfpromo_risk}")
            print(f"  title: {cand.title}")
            print(f"  rationale: {ev.rationale}")

            if ev.draft_comment:
                written += 1
                store.add_opportunity(
                    {
                        "source": "mock",
                        "source_id": cand.source_id,
                        "subreddit": cand.subreddit,
                        "permalink": cand.permalink,
                        "captured_at": cand.captured_at,
                        "relevance_score": ev.relevance_score,
                        "rationale": ev.rationale,
                        "selfpromo_risk": ev.selfpromo_risk,
                        "draft_comment": ev.draft_comment,
                        "notes_for_reviewer": ev.notes_for_reviewer,
                        "status": "pending",
                    }
                )
                print("  --- DRAFT (written to store as source='mock') ---")
                for line in ev.draft_comment.splitlines():
                    print(f"  | {line}")
                if ev.notes_for_reviewer:
                    print(f"  notes_for_reviewer: {ev.notes_for_reviewer}")
            else:
                print("  (below threshold — no draft, nothing written)")
            print()
    finally:
        store.close()

    print(f"done | drafts written={written} | total_cost_usd={round(total_cost, 6)}")
    print(
        "Review them in your store (status=pending). Clean up afterwards with:\n"
        "  DELETE FROM content_opportunities WHERE source = 'mock';"
    )


if __name__ == "__main__":
    main()
