"""Scoring + drafting brain (Anthropic Python SDK — pure Python, no Node).

Two passes, two models, to keep cost down and reliability up:

  1. score()  — cheap model (Haiku) rates relevance 0-100 + self-promo risk.
                Runs on every candidate. Returns structured JSON.
  2. draft()  — stronger model (Opus) writes the reply in David's voice, but
                ONLY for candidates that clear the threshold. Loads
                voice-profile.md fresh each call so edits take effect with no
                code change.

These are plain single-shot model calls (no tools, no agent loop) — exactly the
shape of the scoring/drafting work — so we use the standard Anthropic SDK
(`anthropic`) rather than the Agent SDK. That keeps the deployment Python-only
(no Node / CLI runtime). Per-call cost is computed from token usage so the run
report still tracks total_cost_usd.

The agent has no Reddit-write path anywhere. Every output is a draft for human
review.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from anthropic import Anthropic

from config import Config
from ingest_reddit import Candidate

# --- Pricing (USD per 1M tokens: input, output) ------------------------------
# Used only to report total_cost_usd in the run summary. Update if prices change.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-fable-5": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)  # conservative fallback for an unknown model id


# --- Static prompt fragments -------------------------------------------------

_STG_DOMAINS = (
    "managed print / MPS, copiers and MFPs, document workflow and scanning, "
    "DocuWare, PaperCut, print security, equipment leasing, and public-sector "
    "procurement (CMAS, NASPO)."
)

_SCORING_SYSTEM = f"""You are a strict relevance classifier for Scott Technology
Group (STG), a managed-print and office-technology provider.

STG's domains are: {_STG_DOMAINS}

Given a Reddit post or comment, rate how squarely it sits in STG's domains.
A thread merely *near* office work is NOT relevant — be strict. A printer
troubleshooting question, a copier lease decision, a document-workflow problem,
or a public-sector procurement question IS relevant. General IT, hardware
unrelated to print, or off-topic chatter is NOT.

Output ONLY a single JSON object, nothing else, no preamble, no markdown fences:
{{"relevance_score": <integer 0-100>,
  "rationale": "<one sentence: why this is or isn't a fit for STG's expertise>",
  "selfpromo_risk": "low" | "medium" | "high"}}

selfpromo_risk: "high" if the only useful answer would name a vendor or service;
"low" if an experienced operator could be helpful without naming anyone."""

_DRAFTING_INSTRUCTIONS = """You are drafting a Reddit reply in David Scott's
voice for a HUMAN to review and post manually. You cannot post anything.

Follow the voice profile above EXACTLY:
- Lead with the genuinely useful, lived-in thing. The helpfulness is the
  positioning.
- First person, plain, declarative. Short, period-chopped sentences. Reframe
  lazy framings ("It's not X. It's Y.") when it fits.
- No em-dashes, no hashtags, no corporate filler, no hype/FOMO register.
- NEVER mention STG, Scott Technology Group, its products, its site, or any
  link — unless the thread explicitly asks for a vendor/recommendation (and if
  it does, say so in notes_for_reviewer and treat the item as high self-promo
  risk).
- If you do not have an honest, useful thing to say, return an empty draft.
  Silence is a valid, correct output.

Output ONLY a single JSON object, nothing else, no preamble, no markdown fences:
{"draft_comment": "<the reply in David's voice, or empty string>",
 "notes_for_reviewer": "<anything the human should know before posting, or empty string>"}"""


@dataclass
class Evaluation:
    relevance_score: int
    rationale: str
    selfpromo_risk: str
    draft_comment: str
    notes_for_reviewer: str
    cost_usd: float


# --- Anthropic client (lazy, reused across calls) ----------------------------

_client: Anthropic | None = None


def _get_client(cfg: Config) -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=cfg.anthropic_api_key)
    return _client


def _cost(model: str, usage) -> float:
    price_in, price_out = _PRICES.get(model, _DEFAULT_PRICE)
    it = getattr(usage, "input_tokens", 0) or 0
    ot = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (
        it * price_in
        + ot * price_out
        + cr * price_in * 0.1
        + cw * price_in * 1.25
    ) / 1_000_000


# --- JSON helpers ------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from model output.

    Tolerates accidental ```json fences or stray prose by grabbing the
    outermost {...} span.
    """
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def _candidate_block(candidate: Candidate) -> str:
    return (
        f"Subreddit: r/{candidate.subreddit}\n"
        f"Type: {candidate.kind}\n"
        f"Permalink: {candidate.permalink}\n\n"
        f"Content:\n{candidate.text_for_scoring}"
    )


def _run_model(
    *, cfg: Config, model: str, system_prompt: str, prompt: str, max_tokens: int
) -> tuple[str, float]:
    """One single-shot, tool-free model call. Returns (text, cost_usd)."""
    client = _get_client(cfg)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
    )
    cost = _cost(model, resp.usage)

    if resp.stop_reason == "refusal":
        return "", cost

    text = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text = block.text
            break
    return text, cost


# --- Public API --------------------------------------------------------------

def score(candidate: Candidate, cfg: Config) -> tuple[dict, float]:
    """Cheap relevance pass. Returns (parsed_json, cost_usd)."""
    prompt = (
        "Score this Reddit item for relevance to STG's domains.\n\n"
        + _candidate_block(candidate)
    )
    text, cost = _run_model(
        cfg=cfg,
        model=cfg.scoring_model,
        system_prompt=_SCORING_SYSTEM,
        prompt=prompt,
        max_tokens=512,
    )
    return _extract_json(text), cost


def _voice_examples_block(examples: list[str] | None) -> str:
    """Live few-shot: David's most-recent actually-posted comments. These are the
    strongest signal for his current voice, so they sit right before the task."""
    examples = [e.strip() for e in (examples or []) if e and e.strip()]
    if not examples:
        return ""
    blocks = []
    for i, ex in enumerate(examples, 1):
        blocks.append(f"--- Example {i} (David posted this) ---\n{ex}")
    return (
        "\n\n# HOW DAVID ACTUALLY WRITES (recent replies he published)\n"
        "These are real comments David recently posted, after his own edits. They "
        "are the strongest, most current signal for his voice — match their rhythm, "
        "length, sentence shape, and word choice over any generic guidance above.\n\n"
        + "\n\n".join(blocks)
    )


def draft(
    candidate: Candidate, cfg: Config, voice_examples: list[str] | None = None
) -> tuple[dict, float]:
    """Voice-drafting pass. Returns (parsed_json, cost_usd).

    Loads voice-profile.md fresh each call so edits take effect with no code
    change, and injects David's recent actually-posted comments as live few-shot
    examples (the voice feedback loop).
    """
    voice_profile = cfg.voice_profile_path.read_text(encoding="utf-8")
    system_prompt = (
        "# VOICE PROFILE (single source of truth — follow exactly)\n\n"
        + voice_profile
        + _voice_examples_block(voice_examples)
        + "\n\n# DRAFTING TASK\n\n"
        + _DRAFTING_INSTRUCTIONS
    )
    prompt = (
        "Draft a reply in David's voice for this Reddit item, following the "
        "voice profile and guardrails.\n\n" + _candidate_block(candidate)
    )
    text, cost = _run_model(
        cfg=cfg,
        model=cfg.drafting_model,
        system_prompt=system_prompt,
        prompt=prompt,
        max_tokens=1024,
    )
    return _extract_json(text), cost


def _clamp_score(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, n))


def evaluate(
    candidate: Candidate, cfg: Config, voice_examples: list[str] | None = None
) -> Evaluation:
    """Full pipeline for one candidate: score, then draft if it clears the bar.

    voice_examples (David's recent posted comments) are passed through to the
    drafting step as live few-shot anchors.
    """
    score_json, score_cost = score(candidate, cfg)

    relevance = _clamp_score(score_json.get("relevance_score", 0))
    rationale = str(score_json.get("rationale", "")).strip()
    selfpromo_risk = str(score_json.get("selfpromo_risk", "low")).strip() or "low"

    draft_comment = ""
    notes = ""
    draft_cost = 0.0

    if relevance >= cfg.relevance_threshold:
        draft_json, draft_cost = draft(candidate, cfg, voice_examples)
        draft_comment = str(draft_json.get("draft_comment", "")).strip()
        notes = str(draft_json.get("notes_for_reviewer", "")).strip()

    return Evaluation(
        relevance_score=relevance,
        rationale=rationale,
        selfpromo_risk=selfpromo_risk,
        draft_comment=draft_comment,
        notes_for_reviewer=notes,
        cost_usd=score_cost + draft_cost,
    )
