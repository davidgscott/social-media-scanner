"""Scoring + drafting brain (Claude Agent SDK).

Two passes, two models, to keep cost down and reliability up:

  1. score()  — cheap model (Haiku) rates relevance 0-100 + self-promo risk.
                Runs on every candidate. Returns structured JSON only.
  2. draft()  — stronger model (Opus) writes the reply in David's voice, but
                ONLY for candidates that clear the threshold. Loads
                voice-profile.md fresh as system-prompt context and reads the
                project CLAUDE.md via setting_sources=["project"].

The agent has NO tools (allowed_tools=[]) — it cannot touch Reddit or the
filesystem. Every output is a draft for human review.

API surface used (claude-agent-sdk):
  query(prompt=..., options=ClaudeAgentOptions(...)) -> async iterator
  ClaudeAgentOptions(system_prompt, model, cwd, setting_sources, allowed_tools,
                     permission_mode, max_turns)
  ResultMessage.result          -> final text
  ResultMessage.total_cost_usd  -> per-call cost (summed into the run total)
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from config import Config
from ingest_reddit import Candidate

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

Return ONLY valid JSON, no preamble, no markdown fences:
{{"relevance_score": <0-100 integer>,
  "rationale": "<one sentence: why this is or isn't a fit for STG's expertise>",
  "selfpromo_risk": "low" | "medium" | "high"}}

selfpromo_risk: "high" if the only useful answer would name a vendor or service;
"low" if an experienced operator could be helpful without naming anyone."""

_DRAFTING_INSTRUCTIONS = """You are drafting a Reddit reply in David Scott's
voice for a HUMAN to review and post manually. You cannot post anything.

Follow the voice profile above and the project's CLAUDE.md guardrails EXACTLY:
- Lead with the genuinely useful, lived-in thing. The helpfulness is the
  positioning.
- First person, plain, declarative. Short, period-chopped sentences. Reframe
  lazy framings ("It's not X. It's Y.") when it fits.
- No em-dashes, no hashtags, no corporate filler, no hype/FOMO register.
- NEVER mention STG, Scott Technology Group, its products, its site, or any
  link — unless the thread explicitly asks for a vendor/recommendation (and if
  it does, flag that in notes_for_reviewer and treat selfpromo_risk as high).
- If you do not have an honest, useful thing to say, return an empty draft.
  Silence is a valid, correct output.

Return ONLY valid JSON, no preamble, no markdown fences:
{"draft_comment": "<the reply in David's voice, or empty string>",
 "notes_for_reviewer": "<anything the human should know before posting, optional>"}"""


@dataclass
class Evaluation:
    relevance_score: int
    rationale: str
    selfpromo_risk: str
    draft_comment: str
    notes_for_reviewer: str
    cost_usd: float


# --- JSON helpers ------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from model output.

    Tolerates accidental ```json fences or leading/trailing prose by grabbing
    the outermost {...} span.
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


async def _run_agent(
    *,
    prompt: str,
    system_prompt: str,
    model: str,
    cfg: Config,
    load_project_settings: bool,
) -> tuple[str, float]:
    """Run one single-turn, tool-free agent call. Returns (text, cost_usd)."""
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        cwd=str(cfg.root),
        # Load CLAUDE.md (project memory) for the drafting pass per the brief.
        setting_sources=["project"] if load_project_settings else [],
        allowed_tools=[],          # no tools: cannot post, cannot touch the FS
        permission_mode="bypassPermissions",  # headless; nothing to approve anyway
        max_turns=1,
    )

    result_text = ""
    cost = 0.0
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            cost = message.total_cost_usd or 0.0
            if message.result:
                result_text = message.result
    return result_text, cost


# --- Public API --------------------------------------------------------------

async def score(candidate: Candidate, cfg: Config) -> tuple[dict, float]:
    """Cheap relevance pass. Returns (parsed_json, cost_usd)."""
    prompt = (
        "Score this Reddit item for relevance to STG's domains.\n\n"
        + _candidate_block(candidate)
    )
    text, cost = await _run_agent(
        prompt=prompt,
        system_prompt=_SCORING_SYSTEM,
        model=cfg.scoring_model,
        cfg=cfg,
        load_project_settings=False,
    )
    return _extract_json(text), cost


async def draft(candidate: Candidate, cfg: Config) -> tuple[dict, float]:
    """Voice-drafting pass. Returns (parsed_json, cost_usd).

    Loads voice-profile.md fresh each call so edits take effect without a code
    change, and pulls CLAUDE.md via project settings.
    """
    voice_profile = cfg.voice_profile_path.read_text(encoding="utf-8")
    system_prompt = (
        "# VOICE PROFILE (single source of truth — follow exactly)\n\n"
        + voice_profile
        + "\n\n# DRAFTING TASK\n\n"
        + _DRAFTING_INSTRUCTIONS
    )
    prompt = (
        "Draft a reply in David's voice for this Reddit item, following the "
        "voice profile and guardrails.\n\n" + _candidate_block(candidate)
    )
    text, cost = await _run_agent(
        prompt=prompt,
        system_prompt=system_prompt,
        model=cfg.drafting_model,
        cfg=cfg,
        load_project_settings=True,
    )
    return _extract_json(text), cost


async def evaluate(candidate: Candidate, cfg: Config) -> Evaluation:
    """Full pipeline for one candidate: score, then draft if it clears the bar."""
    score_json, score_cost = await score(candidate, cfg)

    relevance = int(score_json.get("relevance_score", 0) or 0)
    rationale = str(score_json.get("rationale", "")).strip()
    selfpromo_risk = str(score_json.get("selfpromo_risk", "low")).strip() or "low"

    draft_comment = ""
    notes = ""
    draft_cost = 0.0

    if relevance >= cfg.relevance_threshold:
        draft_json, draft_cost = await draft(candidate, cfg)
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
