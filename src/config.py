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

    # Reddit
    reddit_user_agent: str
    reddit_client_id: str
    reddit_client_secret: str
    reddit_username: str
    reddit_password: str

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
        scoring_model=str(models.get("scoring", "claude-haiku-4-5")),
        drafting_model=str(models.get("drafting", "claude-opus-4-8")),
        reddit_user_agent=str(reddit.get("user_agent", "stg-content-monitor/1.0")),
        reddit_client_id=_require("REDDIT_CLIENT_ID"),
        reddit_client_secret=_require("REDDIT_CLIENT_SECRET"),
        reddit_username=os.environ.get("REDDIT_USERNAME", "").strip(),
        reddit_password=os.environ.get("REDDIT_PASSWORD", "").strip(),
        anthropic_api_key=_require("ANTHROPIC_API_KEY"),
        supabase_url=os.environ.get("SUPABASE_URL", "").strip(),
        supabase_key=os.environ.get("SUPABASE_KEY", "").strip(),
    )
