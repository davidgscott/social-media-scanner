# Reddit Data API access — application notes

As of **November 11, 2025**, Reddit's Responsible Builder Policy requires
pre-approval before it will issue Data API credentials — even for small,
read-only projects. You can no longer self-serve a script app at
`reddit.com/prefs/apps`; you submit an access request first and wait for
approval (reportedly ~2–4 weeks).

This file is paste-ready copy for that request. **Answer truthfully.** This is
use *on behalf of a business* (Scott Technology Group), which Reddit treats as
commercial — do not describe it as a personal hobby project to skip the queue.
Our actual use case is strong precisely because it is conservative: read-only,
no automated posting, human-in-the-loop, low volume.

---

## The honest one-paragraph description

> Scott Technology Group is a managed-print and office-technology provider. We
> want to monitor a small set of public, work-related subreddits for
> conversations where we can be genuinely helpful (printer/MFP troubleshooting,
> managed print services, copier leasing, document workflow, print security,
> public-sector procurement). The application is **read-only**: it pulls new
> public posts and top-level comments, scores them for topical relevance, and —
> for the small fraction that are a fit — drafts a candidate reply that a
> **human reviews and posts manually** through the normal Reddit interface. The
> software has **no write access to Reddit** and never posts, votes, or messages
> automatically. We do not resell Reddit data, use it to train machine-learning
> models, or display it publicly. Expected volume is low: a single OAuth client
> polling roughly every four hours, well under the 100 queries/minute limit.

## Suggested answers to common application fields

| Field | Answer |
|---|---|
| App name | `stg-content-monitor` |
| App type | Script (server-side, confidential client) |
| Purpose / description | The paragraph above |
| Commercial use? | **Yes** — used by/for a business (Scott Technology Group). Not a monetized product; internal lead-/relationship-building. Answer honestly and let Reddit classify. |
| Read or write? | **Read only.** No posting, voting, commenting, or messaging via the API. |
| Data accessed | Public posts and top-level comments in a fixed list of subreddits, by `new` feed and keyword search. No private user data, no DMs. |
| Data storage / retention | Source IDs (dedup ledger) and a small review queue of drafted opportunities, stored in our own database. No bulk archiving of Reddit content. |
| Public display of Reddit content? | No. |
| Used to train ML models? | No. |
| Expected request volume | ~1 OAuth client, polling every ~4 hours; far below 100 req/min. |
| Redirect URI | `http://localhost:8080` (unused — app-only/script auth) |
| User-Agent | `windows:stg-content-monitor:1.0 (by /u/<your-username>)` |

## What helps approval (per the policy)

- A **clear, specific** project description (vague descriptions get auto-rejected).
- Emphasis on **read-only + manual posting** — you are not automating engagement,
  which is the behavior the policy targets.
- A correctly-formatted **User-Agent** identifying the app and your Reddit user.
- Truthful commercial classification.

## After approval

1. Create the script app at `reddit.com/prefs/apps` (now unblocked).
2. Copy the **client_id** (under the app name) and **secret** into `.env`:
   `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
3. Set your Reddit username in `config.yaml` → `reddit.user_agent`.
4. Smoke test: `python src/main.py` (see DEPLOY.md), then schedule it.

## Where to apply

Start from Reddit's own help articles (you must be logged in; they 403 external
tools):
- Developer Platform & Accessing Reddit Data — Reddit's routing for Data API access
- Reddit Data API Wiki — technical terms and rate limits
- Responsible Builder Policy — the rules you're agreeing to

If `reddit.com/prefs/apps` keeps redirecting to the Responsible Builder Policy,
that is the gate: the access request must be submitted and approved first. Follow
the link Reddit presents on that policy page to the request/registration form.
