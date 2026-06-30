"""Loads config.yaml + .env into a single Config object.

Deterministic, no model calls. Project paths are resolved relative to the repo
root (the parent of this src/ directory) so the agent always finds
voice-profile.md and CLAUDE.md regardless of where the cron job is launched.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# Repo root = parent of src/
ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Config:
    # Ingestion
    subreddits: list[str]
    keywords: list[str]
    max_posts_per_subreddit: int
    max_comments_per_post: int
    include_comments: bool
    keyword_search_time_filter: str
    max_keyword_results_per_query: int

    # Scoring / drafting
    relevance_threshold: int
    max_drafts_per_run: int
    scoring_model: str
    drafting_model: str
    voice_example_count: int  # recent posted comments injected as live few-shot

    # Reddit
    reddit_user_agent: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str

    # Email ingest (F5Bot alerts via IMAP)
    imap_host: str
    imap_folder: str
    f5bot_sender: str
    imap_lookback_days: int
    imap_user: str
    imap_password: str

    # Anthropic
    anthropic_api_key: str

    # Store
    supabase_url: str
    supabase_key: str

    # Paths
    root: Path = ROOT
    voice_profile_path: Path = field(default_factory=lambda: ROOT / "voice-profile.md")
    sqlite_path: Path = field(default_factory=lambda: ROOT / "stg_monitor.db")
    log_path: Path = field(default_factory=lambda: ROOT / "runs.log")

    @property
    def use_supabase(self) -> bool:
        return bool(self.supabase_url and self.supabase_key)


def _require(env_key: str) -> str:
    val = os.environ.get(env_key, "").strip()
    if not val:
        raise SystemExit(
            f"Missing required environment variable {env_key!r}. "
            "Copy .env.example to .env and fill it in."
        )
    return val


def load_config(config_path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")

    path = config_path or (ROOT / "config.yaml")
    with open(path, "r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    models = raw.get("models", {})
    email_cfg = raw.get("email", {})
    reddit = raw.get("reddit", {})

    return Config(
        subreddits=list(raw.get("subreddits", [])),
        keywords=list(raw.get("keywords", [])),
        max_posts_per_subreddit=int(raw.get("max_posts_per_subreddit", 25)),
        max_comments_per_post=int(raw.get("max_comments_per_post", 5)),
        include_comments=bool(raw.get("include_comments", True)),
        keyword_search_time_filter=str(raw.get("keyword_search_time_filter", "week")),
        max_keyword_results_per_query=int(raw.get("max_keyword_results_per_query", 10)),
        relevance_threshold=int(raw.get("relevance_threshold", 60)),
        max_drafts_per_run=int(raw.get("max_drafts_per_run", 10)),
        voice_example_count=int(raw.get("voice_example_count", 6)),
        scoring_model=str(models.get("scoring", "claude-haiku-4-5")),
        drafting_model=str(models.get("drafting", "claude-opus-4-8")),
        reddit_user_agent=str(reddit.get("user_agent", "stg-content-monitor/1.0")),
        # Reddit creds are validated at ingest time (see ingest_reddit.make_reddit),
        # not here — so scoring/drafting and the mock test can run before Reddit
        # API access is granted.
        reddit_client_id=os.environ.get("REDDIT_CLIENT_ID", "").strip(),
        reddit_client_secret=os.environ.get("REDDIT_CLIENT_SECRET", "").strip(),
        reddit_username=os.environ.get("REDDIT_USERNAME", "").strip(),
        reddit_password=os.environ.get("REDDIT_PASSWORD", "").strip(),
        # Email ingest (host/folder/sender are non-secret config; creds in .env).
        imap_host=str(email_cfg.get("imap_host", "imap.gmail.com")),
        imap_folder=str(email_cfg.get("imap_folder", "INBOX")),
        f5bot_sender=str(email_cfg.get("f5bot_sender", "admin@f5bot.com")),
        imap_lookback_days=int(email_cfg.get("lookback_days", 7)),
        imap_user=os.environ.get("IMAP_USER", "").strip(),
        imap_password=os.environ.get("IMAP_PASSWORD", "").strip(),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        supabase_url=os.environ.get("SUPABASE_URL", "").strip(),
        supabase_key=os.environ.get("SUPABASE_KEY", "").strip(),
    )
