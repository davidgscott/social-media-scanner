"""Distill David's draft->posted edits into a reviewed corrections list.

This is the "zero in on what I dislike" pass. It reads recent
(draft_comment, posted_comment) pairs from the store, shows the model exactly
what David changed, and asks it to extract concrete, recurring edit rules — the
abbreviations he expands, the phrasings he cuts, the openers he deletes. It
writes them to voice-corrections.md for David to read and edit.

That file is then injected into the drafting prompt (see agent._corrections_block)
right under the voice profile, so future drafts stop making the same mistakes.

Human-in-the-loop by design: the model proposes rules, David keeps the veto.
Nothing here posts, and nothing overrides voice-profile.md — corrections only
refine it. Run it once you have a handful of edits banked (10-15 is plenty);
before that there is no pattern to find.

Run:  python src/analyze_edits.py [max_pairs]   (default 60)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import agent
from config import Config, load_config
from store import open_store

_DEFAULT_MAX_PAIRS = 60

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
            "click 'Mark posted' first, then run this again."
        )
        return

    print(f"Analyzing {len(pairs)} draft->posted edit(s)...")
    diffs = _format_pairs(pairs)
    if not diffs.strip():
        print("The edits had no word-level changes to learn from.")
        return

    prompt = (
        "Here are David's recent edits to AI-drafted replies. Extract the "
        "recurring corrections as instructed.\n\n" + diffs
    )
    text, cost = agent._run_model(
        cfg=cfg,
        model=cfg.drafting_model,
        system_prompt=_SYSTEM,
        prompt=prompt,
        max_tokens=1024,
    )
    rules = (text or "").strip()
    if not rules:
        print("The model returned no rules. Nothing written.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    header = (
        f"_Generated {stamp} from {len(pairs)} of David's edits. Review and edit "
        "freely — the drafting model follows this list verbatim, under the voice "
        "profile. Delete anything wrong; add your own rules anytime._\n\n"
    )
    cfg.voice_corrections_path.write_text(header + rules + "\n", encoding="utf-8")

    print(f"\nWrote {cfg.voice_corrections_path.name} (analysis cost ${cost:.4f}):\n")
    print(rules)
    print(
        f"\nReview {cfg.voice_corrections_path.name} and edit as needed. It feeds "
        "the next drafting run automatically."
    )


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        max_pairs = int(arg) if arg else _DEFAULT_MAX_PAIRS
    except ValueError:
        max_pairs = _DEFAULT_MAX_PAIRS
    run(max_pairs)
