"""Ingest layer: F5Bot alert emails via IMAP (read-only).

The automated path. F5Bot delivers keyword-match alerts (Reddit posts/comments)
to a dedicated mailbox; this module reads new alerts over IMAP, parses each
Reddit hit out of the HTML, decodes F5Bot's wrapped links to clean Reddit
permalinks, dedups against the seen-store, and yields candidates for scoring.

Exposes the same `fetch_candidates(cfg, store) -> list[Candidate]` interface as
ingest_reddit, so the orchestrator (run_email.py) treats it identically.

No posting, ever — this only reads an inbox.
"""

from __future__ import annotations

import email
import imaplib
import re
from datetime import datetime, timezone
from email.message import Message
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from config import Config
from ingest_reddit import Candidate

# F5Bot lines look like: "Reddit Comments (/r/sysadmin/): <a ...>title</a> by user"
_HIT_PREFIX = re.compile(r"^\s*Reddit\s+(Comments|Posts)\s*\(/r/([A-Za-z0-9_]+)/\)", re.I)
_POST_ID = re.compile(r"/comments/([a-z0-9]+)", re.I)
_COMMENT_ID = re.compile(r"/comments/[a-z0-9]+/c/([a-z0-9]+)", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decode_f5bot_url(href: str) -> str:
    """F5Bot wraps links as https://f5bot.com/url?u=<encoded reddit url>&...
    Return the clean Reddit URL (or the original href if it isn't wrapped)."""
    try:
        u = parse_qs(urlparse(href).query).get("u", [None])[0]
        return unquote(u) if u else href
    except Exception:
        return href


def _source_id(reddit_url: str, kind: str) -> str | None:
    """Stable dedup id: t1_<comment> for comments, t3_<post> for posts."""
    if kind == "comment":
        m = _COMMENT_ID.search(reddit_url)
        if m:
            return "t1_" + m.group(1)
    m = _POST_ID.search(reddit_url)
    if m:
        return "t3_" + m.group(1)
    return None


def parse_f5bot_html(html: str) -> list[dict]:
    """Extract individual Reddit hits from one F5Bot alert email body."""
    soup = BeautifulSoup(html or "", "html.parser")
    hits: list[dict] = []
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        m = _HIT_PREFIX.match(text)
        if not m:
            continue
        kind = "comment" if m.group(1).lower() == "comments" else "post"
        subreddit = m.group(2)

        anchor = p.find("a", href=True)
        if not anchor:
            continue
        reddit_url = _decode_f5bot_url(anchor["href"])
        if "reddit.com" not in reddit_url:
            continue

        sid = _source_id(reddit_url, kind)
        if not sid:
            continue

        title = anchor.get_text(strip=True)
        # Body = the whole line minus the "Reddit Comments (/r/x/):" label, which
        # keeps the title + author + snippet as context for the scorer.
        body = _HIT_PREFIX.sub("", text).strip(": ").strip()

        hits.append(
            {
                "source_id": sid,
                "kind": kind,
                "subreddit": subreddit,
                "permalink": reddit_url,
                "title": title,
                "body": body,
            }
        )
    return hits


def _hit_to_candidate(hit: dict) -> Candidate:
    return Candidate(
        source_id=hit["source_id"],
        kind=hit["kind"],
        subreddit=hit["subreddit"],
        permalink=hit["permalink"],
        title=hit["title"],
        body=hit["body"],
        captured_at=_now_iso(),
    )


def _html_from_message(msg: Message) -> str:
    """Pull the text/html part out of an email (fall back to text/plain)."""
    html = ""
    plain = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype not in ("text/html", "text/plain"):
                continue
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/html":
                html = decoded
            elif ctype == "text/plain":
                plain = decoded
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            html = payload.decode(charset, errors="replace")
        except Exception:
            html = ""
    return html or plain


def fetch_candidates(cfg: Config, store) -> list[Candidate]:
    """Read unseen F5Bot alerts over IMAP and return new, deduped candidates.

    Emails are marked \\Seen after processing so re-runs don't reprocess them;
    Reddit-hit dedup against the seen-store is the real guarantee against
    re-drafting the same thread.
    """
    if not (cfg.imap_user and cfg.imap_password):
        raise SystemExit(
            "Email ingest needs IMAP_USER and IMAP_PASSWORD in .env. Use a "
            "dedicated Gmail with a 16-char App Password and point F5Bot at it."
        )

    candidates: list[Candidate] = []
    in_run: set[str] = set()

    conn = imaplib.IMAP4_SSL(cfg.imap_host)
    try:
        conn.login(cfg.imap_user, cfg.imap_password)
        conn.select(cfg.imap_folder)
        # Unseen messages from F5Bot only.
        typ, data = conn.search(None, "UNSEEN", "FROM", f'"{cfg.f5bot_sender}"')
        if typ != "OK":
            return candidates
        msg_nums = data[0].split()

        for num in msg_nums:
            typ, msg_data = conn.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            html = _html_from_message(msg)

            for hit in parse_f5bot_html(html):
                sid = hit["source_id"]
                if sid in in_run or store.is_seen(sid):
                    continue
                cand = _hit_to_candidate(hit)
                if not cand.text_for_scoring:
                    continue
                in_run.add(sid)
                candidates.append(cand)

            # Mark the email read so we don't reprocess it next run.
            conn.store(num, "+FLAGS", "\\Seen")
    finally:
        try:
            conn.close()
        except Exception:
            pass
        conn.logout()

    return candidates
