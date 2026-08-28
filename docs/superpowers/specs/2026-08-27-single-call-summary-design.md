# Single-call summary, action items folded into the summary text — design

**Date:** 2026-08-27
**Status:** implemented

## History

This spec was first approved for a version that kept a **structured**
action-items artifact: one `provider.complete()` call returning the summary
and a JSON array separated by a `===ACTION_ITEMS_JSON===` delimiter, with
`action_items.json` and the Action Items panel both retained (commit
`2ef1441`). Immediately after that landed, the direction changed: action
items become a plain `## Action Items` markdown section **inside** the
summary, `action_items.json` stops being written, and the Action Items
panel is removed entirely. This document describes that final shape. The
delimiter / JSON approach is gone — `ACTION_ITEMS_DELIMITER`,
`split_summary_response`, `parse_action_items`, and `build_action_items_prompt`
were all deleted.

## Problem

Generating an AI summary used to make **two** `provider.complete()` calls
per run (`build_summary_prompt` → markdown, `build_action_items_prompt` →
JSON), each embedding its own truncated copy of the transcript — ~2× the
input tokens plus two round-trips. And the structured `action_items.json` /
`ActionItemsPanel` pair carried real weight (a second panel, its own
signals, disk file, delete handling, export branch) for a payload that no
downstream consumer actually reads: the only proposed consumer, a Dynamics
CRM export, was never built.

## Goal

- **One** `provider.complete()` call per summary.
- Action items are the tail of the summary markdown — a section headed
  exactly `## Action Items` — not a separate file, signal, or panel.
- `summary.md` is the model response verbatim. `summary_meta.json` and the
  `transcript.md` refresh happen as before.
- Delete the `ActionItemsPanel` widget and all its wiring.

## Non-goals

- Streaming partial output.
- Changing providers, the summary's substance, or truncation strategy
  (still 60/40 head/tail via `truncate_transcript`).
- Changing the `generated_by` provenance values
  (`talktrack-app` / `talktrack-batch` / `talktrack-batch-summarize`).
- A migration for existing on-disk `summary.md` / `action_items.json`.
  Existing files are left as-is; a stale `action_items.json` is treated as
  legacy cleanup only (swept by the transcriptions delete scope and the
  summary panel's Delete button).

## Response format

The whole `provider.complete()` response **is** `summary.md`. The model is
asked for:

```
- <concise markdown-bullet summary: key discussion points, decisions, outcomes>

## Action Items
- **Owner:** the task (deadline, if one was mentioned)
- ...
```

- The `## Action Items` heading is required and literal.
- When there are no action items, the only line under the heading is
  `_None._`.
- No delimiter, no JSON, nothing to split or parse. A model that ignores
  the heading simply yields a summary without that section — degraded, not
  an error.

## Changes

### `app/ai/summarizer.py`

- `build_summary_prompt(segments, speaker_names, notes="", instruction="", max_transcript_chars=None)`
  asks for the one markdown document described above (summary framing +
  the `## Action Items` section instruction + `_None._` rule). Transcript
  is formatted and `truncate_transcript`'d once.
- `truncate_transcript`, `_format_transcript`, `_format_notes`,
  `_format_instruction` unchanged.
- **Removed:** `ACTION_ITEMS_DELIMITER`, `split_summary_response`,
  `parse_action_items`, `build_action_items_prompt`, and the now-unused
  `import json` / `TranscriptSegment` import.

### `app/main_window.py`

- `SummarizeWorker`: `summary_ready = pyqtSignal(str, dict)` only
  (`actions_ready` removed). `run()` does one
  `self._provider.complete(build_summary_prompt(...))` and emits
  `summary_ready`. `meta["seconds"]` times that one call;
  `generated_by` stays `"talktrack-app"`.
- `_on_summary_ready` is the terminal handler — it calls `_end_ai_phase()`
  (including on its `OSError` early-return path). `_on_actions_ready` and
  `_on_action_items_changed` are deleted.
- All `self.action_items_panel.*` calls removed
  (`clear`/`set_ready`/`set_loading`/`set_error`), and the
  `_do_on_recording_selected` block that loaded `action_items.json`.
- `_delete_summary_and_actions`: title "Delete summary?", clears only the
  summary panel; still unlinks `("summary.md", "summary_meta.json",
  "action_items.json")` — the last purely as legacy cleanup.
- `from app.ui.action_items_panel import ActionItemsPanel` removed;
  `inspector.add_summary_panel(self.summary_panel)` (one arg).

### `app/ui/inspector.py`

- `add_summary_panel(summary_panel)` — single panel. Section renamed
  `"Summary"` (was `"Summary & Actions"`). `set_ai_configured` toggles only
  the summary panel vs. the "connect a provider" message.

### `app/ui/action_items_panel.py`

- Deleted.

### `app/utils/session_io.py`

- `write_summary(session, summary_markdown, meta)` — 3-arg. Writes
  `summary.md` + `summary_meta.json`, refreshes `transcript.md`. No
  `action_items` parameter, no `action_items.json`.
- `export_session_markdown` no longer reads `action_items.json` or passes
  it on.

### `app/utils/transcript_export.py`

- `build_export_markdown(metadata, transcript_data, speaker_names, calendar_event, notes, summary_markdown)`
  and `export_transcript(...)` — `action_items` parameter dropped. The
  `# Action Items` checklist block is gone; the `# Summary` section carries
  the `## Action Items` sub-section as part of `summary_markdown`.

### `app/batch/pipeline.py` — `_run_batch_summary`

- One `provider.complete(build_summary_prompt(...))`;
  `session_io.write_summary(session, summary, meta)`.
- `meta` unchanged (`generated_by: "talktrack-batch"`).

### `app/ui/recordings_list.py`

- `TRANSCRIPTION_FILENAMES` keeps `action_items.json` (legacy sweep) with a
  comment saying so.

## Error handling

| Situation | Behaviour |
|---|---|
| `complete()` raises | In-app: `error` → `_on_summarize_error` → summary panel `set_error()` (prior content restored where present), `_end_ai_phase()`. Batch: caught in `run_job`, appended to `warnings`, job still succeeds on its transcript. |
| Call OK, model omitted the `## Action Items` heading | Summary written as-is. Not an error. |
| Call OK | Response written verbatim to `summary.md`. |

## Testing

- **`tests/test_summarizer.py`** — dropped `TestSplitSummaryResponse`,
  `TestParseActionItems`, `TestParseActionItemsHardening`. `build_summary_prompt`
  now asserts the prompt contains `## Action Items` and `_None._` and does
  **not** contain `JSON` / `===`. `truncate_transcript` tests unchanged.
- **`tests/test_session_io.py`** — `write_summary` 3-arg; asserts
  `action_items.json` is **not** written.
- **`tests/test_transcript_export.py`** — every builder/export call drops
  the trailing arg; the checklist tests are replaced by one asserting the
  `## Action Items` sub-section rides inside `summary_markdown`.
- **`tests/test_batch_pipeline.py`** — `_FakeProvider.complete` returns
  plain markdown ending with a `## Action Items` section; asserts one call,
  no `action_items.json`.
- **`tests/test_inspector.py`** — `add_summary_panel` single arg.
- **`tests/test_resizable_panels.py`** — `ActionItemsPanel` import and its
  resize test removed.
- Smoke: `python -c "from app.ai.summarizer import build_summary_prompt; import app.main_window; import app.batch.pipeline"`.
- Full suite green: `.venv\Scripts\python.exe -m pytest tests/ -q`.

## Companion skills

- **`.claude/skills/talktrack-batch-summarize/SKILL.md`** — Step 5 is one
  prompt for a single markdown document ending with `## Action Items`; no
  delimiter, no JSON, no split. Step 6 writes back only `summary.md` +
  `summary_meta.json` + the `transcript.md` Summary section (which now
  carries the action items itself); `action_items.json` is not written, and
  a legacy standalone Action Items block in `transcript.md` is removed when
  the Summary section is rewritten.
- **`.claude/skills/talktrack-transcripts/SKILL.md`** — the "Export format"
  section now shows `## Action Items` as a sub-section of `# Summary`, with
  a note that a standalone block / `action_items.json` is legacy.

## Docs

- `.claude/rules/ai-providers.md` — "Summary — one call, action items are a
  section inside it".
- `CLAUDE.md`, `README.md`, `docs/batch-transcription.md` — updated to drop
  `action_items.json` and describe the `## Action Items` section.
- No GitHub issue (issue filing is paused per project memory).

## Rollout

Landed across two commits on `feature/ui-redesign`: `2ef1441` (single call,
delimiter/JSON form) then the follow-up that folded action items into the
summary text and removed the panel. No migration.
