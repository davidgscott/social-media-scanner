-- Voice feedback loop (Stage 1): capture the human-edited final text alongside
-- the AI draft. When you edit a draft in the dashboard and click "Mark posted",
-- your final version is saved here. Recent posted_comments are fed back to the
-- drafting model as live voice examples. Run once in the Supabase SQL editor.

alter table content_opportunities add column if not exists posted_comment text;
