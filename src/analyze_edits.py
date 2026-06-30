"""Distill David's draft->posted edits into a reviewed corrections list.

This is the "zero in on what I dislike" pass. It reads recent
(draft_comment, posted_comment) pairs from the store, shows the model exactly
what David changed, and asks it to extract concrete, recurring edit rules — the
abbreviations he expands, the phrasings he cuts, the openers he deletes. It
writes them to voice-corrections.md, which the drafting prompt injects right
under the voice profile (see agent._corrections_block) so future drafts stop
making the same mistakes.

Designed to run unattended (e.g. a weekly Task Scheduler / cron job). To make
that safe, voice-corrections.md has two zones:

  * MY RULES (top)        — authoritative, hand-edited by David, NEVER overwritten.
  * AUTO-DISTILLED (below)— regenerated on every run from the latest edits.

The run reads David's own rules and tells the model not to repeat or contradict
them, so his vetoes and additions stick. Human keeps the veto; nothing here
posts, and nothing overrides voice-profile.md — corrections only refine it.

Run:  python src/analyze_edits.py [max_pairs]   (default 60)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import agent
from config import Config, load_config
from store import open_store

_DEFAULT_MAX_PAIRS = 60

# Everything below this marker is machine-owned and rewritten each run. Anything
# above it (David's own rules) is preserved verbatim.
_MARKER = (
    "<!-- ===== AUTO-DISTILLED BELOW: regenerated automatically each run. "
    "Edits below this line are overwritten; put rules you want to keep above it. "
    "===== -->"
)

_HUMAN_HEADER = "## My rules (authoritative — edit freely; never overwritten)"
_HUMAN_PLACEHOLDER = (
    "- (Add your own voice rules here. Anything in this section stays put and is "
    "never overwritten by the weekly job.)"
)

_SYSTEM = """You analyze how David edits AI-drafted Reddit replies before he
posts them. Your job is to find the RECURRING, CONCRETE changes he makes, so a
drafting model can stop making them in the first place.

You will be given a series of edits. For each, [- ...] is text David removed,
[+ ...] is text he added, and [- x -> + y] is a replacement.

Extract durable rules, not one-offs. Good rules are specific and checkable:
- word/abbreviation substitutions he makes consistently (e.g. always expands an
  abbreviation back to the full word)
- phrasings, openers, or sentence shapes he repeatedly deletes (e.g. a rhetorical
  framing that reads as an AI tell)
- filler or hedging he cuts
- punctuation or formatting habits

Rules:
- Only include a pattern you can see in the edits. Do NOT invent style advice.
- If a change appears more than once, note the count like "(seen 3x)". Frequency
  is the signal that it's a habit, not a one-off.
- Ignore edits that are purely about the specific thread's facts (a changed
  detail, a different example) — those don't generalize.
- Phrase each rule as a direct instruction to the drafter ("Never abbreviate
  'cartridge' to 'cart'. Expand it.").
- Be concise. 3-12 bullets. No preamble, no closing remarks.

Output ONLY a markdown bullet list. Nothing else."""


def _format_pairs(pairs: list[tuple[str, str]]) -> str:
    blocks = []
    for i, (draft_text, posted_text) in enumerate(pairs, 1):
        diff = agent._word_diff(draft_text, posted_text)
        if diff:
            blocks.append(f"--- Edit {i} ---\n{diff}")
    return "\n\n".join(blocks)


def _read_human_section(cfg: Config) -> str:
    """Return the human-owned top of voice-corrections.md (everything above the
    auto-distilled marker), or a fresh placeholder section if the file is new."""
    try:
        existing = cfg.voice_corrections_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        existing = ""
    if _MARKER in existing:
        return existing.split(_MARKER, 1)[0].rstrip() + "\n"
    if existing.strip():
        # Older / hand-made file with no marker: keep all of it as David's, so we
        # never destroy hand-written content. The marker gets added below it.
        return existing.rstrip() + "\n"
    # Brand new file.
    return f"# Voice corrections\n\n{_HUMAN_HEADER}\n{_HUMAN_PLACEHOLDER}\n"


def _human_rules_text(human_section: str) -> str:
    """Just the bullet lines from David's section, for feeding the model so it
    doesn't duplicate or contradict rules he already maintains."""
    lines = []
    for line in human_section.splitlines():
        s = line.strip()
        if s.startswith(("-", "*")) and _HUMAN_PLACEHOLDER not in line:
            lines.append(s)
    return "\n".join(lines)


def run(max_pairs: int = _DEFAULT_MAX_PAIRS) -> None:
    cfg = load_config()
    store = open_store(cfg)
    try:
        pairs = store.recent_edit_pairs(max_pairs)
    finally:
        store.close()

    if not pairs:
        print(
            "No edited drafts found yet. Edit a few drafts in the dashboard and "
            "click 'Mark posted' first, then run this again. Left "
            f"{cfg.voice_corrections_path.name} untouched."
        )
        return

    print(f"Analyzing {len(pairs)} draft->posted edit(s)...")
    diffs = _format_pairs(pairs)
    if not diffs.strip():
        print("The edits had no word-level changes to learn from. Nothing written.")
        return

    human_section = _read_human_section(cfg)
    already = _human_rules_text(human_section)

    prompt = "Here are David's recent edits to AI-drafted replies.\n\n" + diffs
    if already:
        prompt += (
            "\n\nDavid already maintains the rules below. Do NOT repeat or "
            "contradict them — only surface NEW patterns from the edits:\n" + already
        )
    prompt += "\n\nExtract the recurring corrections as instructed."

    text, cost = agent._run_model(
        cfg=cfg,
        model=cfg.drafting_model,
        system_prompt=_SYSTEM,
        prompt=prompt,
        max_tokens=1024,
    )
    rules = (text or "").strip()
    if not rules:
        print("The model returned no rules. Left the existing file untouched.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    auto_section = (
        f"{_MARKER}\n\n## Auto-distilled from my recent edits\n"
        f"_Generated {stamp} from {len(pairs)} edit(s). Move any keeper up into "
        "'My rules' so it survives the next run._\n\n" + rules + "\n"
    )
    cfg.voice_corrections_path.write_text(
        human_section.rstrip() + "\n\n" + auto_section, encoding="utf-8"
    )

    print(f"\nWrote {cfg.voice_corrections_path.name} (analysis cost ${cost:.4f}):\n")
    print(rules)
    print(
        f"\nIt feeds the next drafting run automatically. Your 'My rules' section "
        "at the top is never overwritten — move any auto rule up there to keep it."
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        max_pairs = int(arg) if arg else _DEFAULT_MAX_PAIRS
    except ValueError:
        max_pairs = _DEFAULT_MAX_PAIRS
    run(max_pairs)
