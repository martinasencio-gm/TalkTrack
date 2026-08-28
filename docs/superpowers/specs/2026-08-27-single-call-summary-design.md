# Single-call summary + action items — design

**Date:** 2026-08-27
**Status:** approved, ready for plan

## Problem

Generating an AI summary for a recording currently makes **two** `provider.complete()`
calls per run:

1. `build_summary_prompt(...)` → markdown summary
2. `build_action_items_prompt(...)` → JSON array of `{task, assignee, deadline}`

Each call embeds a full (independently truncated) copy of the transcript. So a
summarize action costs ~2× the transcript tokens on input plus two request
latencies. This has been the shape since `ca2f387` ("add meeting summary and
action items panels"); the recent batch-summarization work (`75bbc0f`) reused it
verbatim in `_run_batch_summary`.

We want **one** call that returns both, without losing the structured
action-items artifact.

## Goal

One `provider.complete()` call produces both the summary and a structured
action-items list. The app still writes `summary.md`, `action_items.json`,
`summary_meta.json`, and refreshes `transcript.md` exactly as today; both the
Summary panel and the Action Items panel stay; Dynamics CRM export (which reads
`action_items.json`) is unaffected.

## Non-goals

- Action items becoming plain summary text / losing `action_items.json` or the
  Action Items panel. (Explicitly rejected — CRM export and the standalone panel
  depend on the structured file.)
- Streaming partial output.
- Changing providers, prompts' substance, truncation strategy, or the
  `generated_by` provenance values.
- Combining the two UI panels into one.
- Independent regeneration of summary vs. action items. It does not exist today
  either — `summary_panel.regenerate_requested` and
  `action_items_panel.regenerate_requested` both already call
  `_regenerate_summary`, which re-runs the whole thing.

## Response format

The model is asked to return:

```
<concise markdown-bullet summary>
===ACTION_ITEMS_JSON===
[{"task": "...", "assignee": "...", "deadline": "..."}, ...]
```

- The delimiter is the literal line `===ACTION_ITEMS_JSON===` on its own.
- Everything before the delimiter is the summary; everything after is the JSON
  array.
- Chosen over a single `{"summary": ..., "action_items": ...}` JSON object
  because the summary stays first-class markdown (models — especially local GGUF
  — handle long markdown-in-a-JSON-string worse), and a malformed object would
  lose both halves at once. Chosen over a ```` ```json ```` fence because an
  explicit delimiter is more deterministic to locate and can't be confused with
  a code sample inside the summary.

## Changes

### `app/ai/summarizer.py`

**`build_summary_prompt(segments, speaker_names, notes="", instruction="", max_transcript_chars=None)`**
— now asks for summary **and** action items in one prompt:

- Keeps the existing summary framing (key discussion points, decisions,
  outcomes; markdown bullets; incorporate notes; follow additional
  instructions).
- Appends the action-items framing (tasks / follow-ups / commitments; extract
  from notes too; `{task, assignee, deadline}`, deadline `""` when none).
- Specifies the `===ACTION_ITEMS_JSON===` delimiter and "nothing after the
  array".
- Transcript is formatted and `truncate_transcript`'d **once** (down from twice).
- `_format_transcript`, `_format_notes`, `_format_instruction`,
  `truncate_transcript` unchanged.

**`build_action_items_prompt(...)`** — **removed.** Only callers are the two call
sites changed here plus its own test.

**`split_summary_response(response) -> (summary_markdown: str, action_items: list)`**
— new:

- Find the **last** line equal to `===ACTION_ITEMS_JSON===` (stripped).
- **Delimiter present:** text before it (stripped) → `summary_markdown`; text
  after it → `parse_action_items()` (unchanged; its permissive outermost-`[...]`
  extraction still copes with stray fences/prose). If the tail is garbage,
  `parse_action_items` returns `[]` and the summary is still whatever came
  before the delimiter — the delimiter line and everything after it never leak
  into the summary.
- **Delimiter absent:** `summary_markdown` = the whole response (stripped),
  `action_items = []`.
- Either way this mirrors today's "bad action-items JSON ⇒ empty list, keep the
  summary" behaviour — never fatal.
- Edge: a response whose *summary* text contains `===ACTION_ITEMS_JSON===` is not
  a realistic concern for a "concise bullet summary", and using the **last**
  occurrence makes the trailing real delimiter win anyway.

**`parse_action_items(response)`** — unchanged.

### `app/main_window.py` — `SummarizeWorker`

- `run()` makes **one** `self._provider.complete(prompt)` call, where `prompt =
  build_summary_prompt(segments, names, notes, instruction, max_transcript_chars=max_chars)`.
- Then `summary, actions = split_summary_response(response)`.
- Emit `summary_ready.emit(summary, meta)` then `actions_ready.emit(actions)`
  back-to-back. Both existing signals and their slots (`_on_summary_ready`,
  `_on_actions_ready`, `_on_action_items_changed`) are **unchanged** — every disk
  write (`summary.md`, `summary_meta.json`, `action_items.json`,
  `_export_transcript()`) happens exactly as now.
- `meta` unchanged in shape; `generated_by` stays `"talktrack-app"`;
  `meta["seconds"]` now times the single call.
- **Remove** the `phase_changed` signal and its two `emit("summary")` /
  `emit("actions")` calls.

### `app/main_window.py` — progress UI

The two-phase progress machinery exists only to flip the activity label from
"Generating summary" to "Extracting action items". With one call:

- **Remove** `_on_ai_phase_changed` and the `phase_changed` connection.
- `_current_phase_label()` AI branch collapses to a single string
  `"Generating summary"` (no dict lookup on `_ai_phase`).
- `_ai_phase` is set once (to `"summary"`) when the worker starts, or dropped
  entirely if nothing else reads it — implementer's call during the plan.
- `_ai_start_time`, `_ai_tick`, `_end_ai_phase()` stay. `_end_ai_phase()` is
  still called from `_on_actions_ready` (whole run done) and
  `_on_summarize_error`.
- `_ai_busy()`, `_should_hide_to_tray`, `resolve_activity_state`, activity-pill
  / capital-bar / compact-strip wiring — all unchanged.

### `app/batch/pipeline.py` — `_run_batch_summary`

- One `provider.complete(build_summary_prompt(...))` call.
- `summary, actions = split_summary_response(response)`.
- `session_io.write_summary(session, summary, actions, meta)` — unchanged.
- `meta` unchanged (`generated_by: "talktrack-batch"`, `describe_ai_model`,
  `seconds`, `generated_at`); `seconds` now times the single call.
- Exception handling unchanged — still a non-fatal warning on an otherwise-OK
  job in `run_job`.

## Error handling

| Situation | Behaviour |
|---|---|
| `complete()` raises (network / auth / SDK) | In-app: `error` → `_on_summarize_error` → both panels `set_error()`, prior content restored where present. Batch: caught in `run_job`, appended to `warnings`, job still succeeds on its transcript. Same as today, now genuinely atomic — no state where `summary.md` was rewritten but `action_items.json` was not. |
| Call OK, delimiter missing or tail unparseable | Whole response → summary; `action_items = []` written (identical to a meeting with no action items). Not an error. |
| Call OK, well-formed | Summary + parsed items written as today. |

## Testing

- **`tests/test_summarizer.py`**
  - Drop `test_build_action_items_prompt`.
  - `build_summary_prompt`: keep existing asserts; add asserts that the prompt
    now contains the action-items instruction (`"action item"`), the field names,
    and the `===ACTION_ITEMS_JSON===` delimiter token.
  - New `TestSplitSummaryResponse`: clean split; missing delimiter
    (all-summary + `[]`); malformed JSON after the delimiter (all-summary +
    `[]`); summary body containing a `[ ... ]` but a real trailing delimiter
    (items still parsed from the tail); delimiter with surrounding whitespace.
  - `truncate_transcript` tests unchanged.
- **`tests/test_batch_pipeline.py`** — summary-stage tests already fake the
  provider; update the fake `complete()` to return
  `summary + "\n===ACTION_ITEMS_JSON===\n" + json_array` and assert both
  `summary.md` and `action_items.json` are written with the expected content.
  Add one case: fake returns summary with no delimiter → `summary.md` written,
  `action_items.json` is `[]`.
- **`SummarizeWorker`** is defined inline in `main_window.py` with no unit test
  today; covered by the ways-of-working smoke check
  (`python -c "from app.ai.summarizer import build_summary_prompt, split_summary_response"`).
- Full suite green: `.venv\Scripts\python.exe -m pytest tests/ -q`.

## Companion skill — `.claude/skills/talktrack-batch-summarize/SKILL.md`

- Merge **Step 5 (Generate the summary)** and **Step 6 (Generate action items)**
  into one step: answer a single combined prompt (summary framing + action-items
  framing) and produce your answer as
  `<summary markdown>` + newline + `===ACTION_ITEMS_JSON===` + newline +
  `<JSON array>`, then split your own answer on that delimiter — `summary` before,
  `action_items` (parsed the same permissive way) after; missing/garbled tail ⇒
  `action_items = []`, keep the summary.
- Renumber: old Step 7 (Write back) → Step 6, old Step 8 (Report) → Step 7. Their
  content is unchanged — still writes `summary.md`, `action_items.json`,
  `summary_meta.json` (`generated_by: "talktrack-batch-summarize"`, no `seconds`
  key), and refreshes `transcript.md`.
- The provenance / speaker-name-map wording already fixed earlier today stays.

**`.claude/skills/talktrack-transcripts/SKILL.md` — no change.** It only *reads*
`transcript.md`; the export's `## Summary` / `## Action Items` sections are still
produced by `export_session_markdown` in the same shape. Nothing in this change
touches the export format.

## Docs

- `.claude/rules/ai-providers.md` — add under a short "Summary / action items"
  note: one combined `provider.complete()` call; response is markdown summary +
  `===ACTION_ITEMS_JSON===` delimiter + JSON array; `summarizer.split_summary_response`
  does the split; a missing delimiter degrades to summary-only + `[]`.
- The 2026-08-27 batch-summarization spec is historical — left as written.
- No GitHub issue (issue filing is paused per project memory).

## Rollout

Single commit (prefix `feat:` or `refactor:`), tests green, suite green, plus a
one-line smoke import. No migration — existing `summary.md` / `action_items.json`
on disk are untouched; only the generation path changes.
