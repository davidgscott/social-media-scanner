"""Persistence: seen-source dedup ledger + kept opportunities queue.

Two backends behind one interface:
  - SQLiteStore  (default, zero-config, ./stg_monitor.db)
  - SupabaseStore (used automatically when SUPABASE_URL/KEY are set)

The agent has no Reddit-write capability anywhere; this module only records
drafts for a human to review and post manually.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Protocol

from config import Config


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store(Protocol):
    def is_seen(self, source_id: str) -> bool: ...
    def mark_seen(self, source_id: str) -> None: ...
    def add_opportunity(self, record: dict) -> None: ...
    def recent_posted(self, limit: int) -> list[str]: ...
    def close(self) -> None: ...


class SQLiteStore:
    """Local file-based store. Creates its tables on first use."""

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS content_opportunities (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                source             TEXT NOT NULL DEFAULT 'reddit',
                source_id          TEXT NOT NULL,
                subreddit          TEXT NOT NULL,
                permalink          TEXT NOT NULL,
                captured_at        TEXT NOT NULL,
                relevance_score    INTEGER NOT NULL,
                rationale          TEXT,
                selfpromo_risk     TEXT NOT NULL DEFAULT 'low',
                draft_comment      TEXT,
                posted_comment     TEXT,
                notes_for_reviewer TEXT,
                status             TEXT NOT NULL DEFAULT 'pending',
                UNIQUE (source, source_id)
            );
            CREATE INDEX IF NOT EXISTS idx_opportunities_status
                ON content_opportunities (status);
            CREATE INDEX IF NOT EXISTS idx_opportunities_score
                ON content_opportunities (relevance_score DESC);

            CREATE TABLE IF NOT EXISTS seen_sources (
                source_id   TEXT PRIMARY KEY,
                captured_at TEXT NOT NULL
            );
            """
        )
        # Add posted_comment to pre-existing local DBs (no IF NOT EXISTS in older SQLite).
        try:
            self.conn.execute("ALTER TABLE content_opportunities ADD COLUMN posted_comment TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        self.conn.commit()

    def is_seen(self, source_id: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_sources WHERE source_id = ?", (source_id,)
        )
        return cur.fetchone() is not None

    def mark_seen(self, source_id: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_sources (source_id, captured_at) VALUES (?, ?)",
            (source_id, _now_iso()),
        )
        self.conn.commit()

    def add_opportunity(self, record: dict) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO content_opportunities (
                source, source_id, subreddit, permalink, captured_at,
                relevance_score, rationale, selfpromo_risk, draft_comment,
                notes_for_reviewer, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.get("source", "reddit"),
                record["source_id"],
                record["subreddit"],
                record["permalink"],
                record.get("captured_at", _now_iso()),
                int(record["relevance_score"]),
                record.get("rationale", ""),
                record.get("selfpromo_risk", "low"),
                record.get("draft_comment", ""),
                record.get("notes_for_reviewer", ""),
                record.get("status", "pending"),
            ),
        )
        self.conn.commit()

    def recent_posted(self, limit: int) -> list[str]:
        cur = self.conn.execute(
            """
            SELECT posted_comment FROM content_opportunities
            WHERE posted_comment IS NOT NULL AND TRIM(posted_comment) != ''
            ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        return [r[0] for r in cur.fetchall()]

    def close(self) -> None:
        self.conn.close()


class SupabaseStore:
    """Supabase backend. Imported lazily so SQLite users need no supabase pkg."""

    def __init__(self, url: str, key: str):
        try:
            from supabase import create_client  # lazy import
        except ImportError as exc:  # pragma: no cover
            raise SystemExit(
                "SUPABASE_URL/KEY are set but the 'supabase' package is not "
                "installed. Run: pip install supabase"
            ) from exc
        self.client = create_client(url, key)

    def is_seen(self, source_id: str) -> bool:
        resp = (
            self.client.table("seen_sources")
            .select("source_id")
            .eq("source_id", source_id)
            .limit(1)
            .execute()
        )
        return bool(resp.data)

    def mark_seen(self, source_id: str) -> None:
        self.client.table("seen_sources").upsert(
            {"source_id": source_id, "captured_at": _now_iso()},
            on_conflict="source_id",
        ).execute()

    def add_opportunity(self, record: dict) -> None:
        row = {
            "source": record.get("source", "reddit"),
            "source_id": record["source_id"],
            "subreddit": record["subreddit"],
            "permalink": record["permalink"],
            "captured_at": record.get("captured_at", _now_iso()),
            "relevance_score": int(record["relevance_score"]),
            "rationale": record.get("rationale", ""),
            "selfpromo_risk": record.get("selfpromo_risk", "low"),
            "draft_comment": record.get("draft_comment", ""),
            "notes_for_reviewer": record.get("notes_for_reviewer", ""),
            "status": record.get("status", "pending"),
        }
        self.client.table("content_opportunities").upsert(
            row, on_conflict="source,source_id"
        ).execute()

    def recent_posted(self, limit: int) -> list[str]:
        resp = (
            self.client.table("content_opportunities")
            .select("posted_comment")
            .neq("posted_comment", "")
            .not_.is_("posted_comment", "null")
            .order("id", desc=True)
            .limit(limit)
            .execute()
        )
        return [r["posted_comment"] for r in (resp.data or []) if r.get("posted_comment")]

    def close(self) -> None:  # supabase client needs no explicit close
        pass


def open_store(cfg: Config) -> Store:
    if cfg.use_supabase:
        return SupabaseStore(cfg.supabase_url, cfg.supabase_key)
    return SQLiteStore(str(cfg.sqlite_path))
