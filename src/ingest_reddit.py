"""Ingest layer (plain Python, PRAW) — read-only.

Pulls new posts + top-level comments per subreddit, plus targeted keyword
searches, and yields candidate dicts that have NOT been seen before. Scoring is
the model's job (agent.py); this layer is purely deterministic collection.

To add X/Twitter later, write src/ingest_twitter.py exposing the same
`fetch_candidates(cfg, store) -> list[Candidate]` interface and call it from
main.py. Nothing else has to change.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import praw

from config import Config
from store import Store


@dataclass
class Candidate:
    source_id: str          # PRAW fullname: t3_xxx (post) or t1_xxx (comment)
    kind: str               # "post" | "comment"
    subreddit: str
    permalink: str          # absolute URL
    title: str              # post title; "" for comments
    body: str               # selftext or comment body
    captured_at: str

    @property
    def text_for_scoring(self) -> str:
        parts = [p for p in (self.title, self.body) if p]
        return "\n\n".join(parts).strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_reddit(cfg: Config) -> praw.Reddit:
    """Build a read-only PRAW client.

    App-only (client-credentials) auth is enough for reading. If a username and
    password are supplied we use them, but we still force read_only mode so the
    agent can never write.
    """
    if not (cfg.reddit_client_id and cfg.reddit_client_secret):
        raise SystemExit(
            "Reddit API credentials are not set. Add REDDIT_CLIENT_ID and "
            "REDDIT_CLIENT_SECRET to .env once Reddit grants Data API access. "
            "(Scoring/drafting and scripts/mock_run.py work without them.)"
        )

    kwargs = dict(
        client_id=cfg.reddit_client_id,
        client_secret=cfg.reddit_client_secret,
        user_agent=cfg.reddit_user_agent,
    )
    if cfg.reddit_username and cfg.reddit_password:
        kwargs["username"] = cfg.reddit_username
        kwargs["password"] = cfg.reddit_password

    reddit = praw.Reddit(**kwargs)
    reddit.read_only = True  # hard guarantee: no writes, ever
    return reddit


def _post_to_candidate(submission) -> Candidate:
    return Candidate(
        source_id=submission.fullname,  # t3_...
        kind="post",
        subreddit=str(submission.subreddit),
        permalink=f"https://www.reddit.com{submission.permalink}",
        title=submission.title or "",
        body=(submission.selftext or "").strip(),
        captured_at=_now_iso(),
    )


def _comment_to_candidate(comment) -> Candidate:
    return Candidate(
        source_id=comment.fullname,  # t1_...
        kind="comment",
        subreddit=str(comment.subreddit),
        permalink=f"https://www.reddit.com{comment.permalink}",
        title="",
        body=(comment.body or "").strip(),
        captured_at=_now_iso(),
    )


def fetch_candidates(cfg: Config, store: Store) -> list[Candidate]:
    """Return new, unseen candidates across all configured subreddits.

    Dedup happens twice: against the persistent seen-store (across runs) and
    against an in-run set (so a post surfaced by both .new() and a keyword
    search isn't added twice).
    """
    reddit = make_reddit(cfg)
    candidates: list[Candidate] = []
    in_run_seen: set[str] = set()

    def consider(cand: Candidate) -> None:
        if cand.source_id in in_run_seen:
            return
        if store.is_seen(cand.source_id):
            return
        if not cand.text_for_scoring:
            return
        in_run_seen.add(cand.source_id)
        candidates.append(cand)

    for name in cfg.subreddits:
        try:
            subreddit = reddit.subreddit(name)

            # 1. New posts (and their top-level comments).
            for submission in subreddit.new(limit=cfg.max_posts_per_subreddit):
                consider(_post_to_candidate(submission))

                if cfg.include_comments and cfg.max_comments_per_post > 0:
                    submission.comments.replace_more(limit=0)
                    for comment in submission.comments[: cfg.max_comments_per_post]:
                        consider(_comment_to_candidate(comment))

            # 2. Targeted keyword searches within the subreddit.
            for keyword in cfg.keywords:
                try:
                    results = subreddit.search(
                        keyword,
                        sort="new",
                        time_filter=cfg.keyword_search_time_filter,
                        limit=cfg.max_keyword_results_per_query,
                    )
                    for submission in results:
                        consider(_post_to_candidate(submission))
                except Exception as exc:  # one bad query shouldn't kill the run
                    print(f"  [warn] search '{keyword}' in r/{name} failed: {exc}")

        except Exception as exc:
            print(f"  [warn] subreddit r/{name} failed: {exc}")

        # Be gentle on the API between subreddits. PRAW also rate-limits itself.
        time.sleep(1)

    return candidates
