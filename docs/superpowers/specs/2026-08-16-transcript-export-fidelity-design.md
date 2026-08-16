# Transcript Export Fidelity

## Goal

Make the exported Markdown transcript a genuine superset of the source
`transcript.json`, so a recording's audio can eventually be deleted without
losing information — and stop exporting transcripts that contain nothing.

## Background

Each transcribed recording produces two transcript artifacts:

- `<recordings>/<session>/transcript.json` — the transcriber's raw output.
  Per-segment `start`, `end`, `text`, `speaker`, `confidence`; top-level
  `language`, `duration`, `model_size`, `transcribe_seconds`.
- `<transcripts>/<session>_<stamp>.md` — the export built by
  `app/utils/transcript_export.py`. Adds title, calendar context, resolved
  speaker names, summary, action items, and notes.

Neither is a superset of the other. The export currently drops `end`,
`confidence`, `language`, and `model_size`. That gap does not matter while
both files exist, but the intended direction is a durable transcript corpus
for AI analysis with audio deleted — at which point the `.md` is what
survives, and the dropped fields are unrecoverable.

Measured against the current corpus (3 transcribed recordings, 7 exports):

- Segment confidence runs 0.65–0.79, average 0.70–0.79. There is no clean
  separation between "good" and "junk" on confidence, which is why this
  design records the value rather than thresholding it.
- Two exports are 179 and 281 bytes: frontmatter plus an empty `# Transcript`
  heading, from recordings that transcribed to zero segments. The smallest
  legitimate transcript is 145 words. The junk is characterized by having no
  segments at all, not by being short.

## Design

All three changes are confined to `app/utils/transcript_export.py`, which is
Qt-free and already unit-tested.

### 1. Segment lines carry end time and confidence

The transcript loop in `build_export_markdown()` renders a time range and an
inline confidence value:

```
**[00:00:01–00:00:09] Alice (0.76):** And with the upcoming elections...
```

The separator is an en-dash (U+2013). `atomic_write_text` writes UTF-8, so
this is safe.

Each part degrades independently, so transcripts produced before this change
— and any segment missing a field — still render:

| Segment fields present | Rendered line |
|---|---|
| `end`, `speaker`, `confidence` | `**[00:00:01–00:00:09] Alice (0.76):** text` |
| `end`, `speaker` | `**[00:00:01–00:00:09] Alice:** text` |
| `end`, `confidence`, no speaker | `**[00:00:01–00:00:09] (0.76)** text` |
| `end` only, no speaker | `**[00:00:01–00:00:09]** text` |
| no `end` (missing, `None`, or `<= start`) | `**[00:00:01] Alice:** text` — today's format |

Rules:

- The range is used only when `end` is a number greater than `start`.
  Otherwise the single-timestamp form is emitted unchanged, so a malformed
  or absent `end` can never produce a backwards or zero-length range.
- Confidence is formatted to two decimals (`0.76`). It is omitted entirely
  when the field is absent, `None`, or not a number.
- The colon after the closing `**` appears only when a speaker name is
  present, matching the current behavior.

### 2. Frontmatter carries transcription provenance

Two YAML lines are added, each only when the corresponding key is present
and non-empty in `transcript_data`:

```yaml
language: "en"
model_size: "small"
```

They are placed after `duration_seconds` and before `source_directory`.
`model_size` is what tells a later reader how much to trust a transcript.

`transcribe_seconds` is deliberately **not** exported — it is performance
telemetry about the machine that ran the transcription, with no bearing on
the transcript's content or trustworthiness.

### 3. Empty transcripts are not exported

A new module-level predicate:

```python
def has_exportable_content(transcript_data):
    """No segments means nothing was heard — an export would be frontmatter
    and an empty heading, which only pollutes the corpus."""
    return bool((transcript_data or {}).get("segments"))
```

`export_transcript()` calls it first and returns early when it is false,
logging at INFO with the source directory so a skip is diagnosable. The skip
is a normal return, not an exception; the existing best-effort `try/except`
contract is unchanged.

`build_export_markdown()` deliberately does **not** consult the predicate. It
is a pure builder; the decision of whether a document is worth writing is
policy and belongs at the write site. Callers and tests can still build a
document from any input.

A `.md` written by an earlier version for a now-skipped recording is left
untouched. `export_transcript` has never deleted a file and this change does
not make it start; removing stale exports is the user's call, and the
existing "delete recording" flow already offers it.

## Testing

Extends `tests/test_transcript_export.py`. All cases are plain unit tests —
no Qt, no filesystem beyond `tmp_path`.

Rendering (against `build_export_markdown`):

1. Segment with `end`, `speaker`, and `confidence` renders the full form.
2. Segment with `end` and `speaker` but no `confidence` omits the parenthetical.
3. Segment with `end` and `confidence` but no speaker renders without a colon.
4. Segment with no `end` falls back to the single-timestamp form.
5. Segment whose `end` is `None`, non-numeric, or `<= start` falls back to the
   single-timestamp form (no backwards or zero-length range is ever emitted).
6. Confidence of `None` or a non-numeric value is omitted rather than raising.
7. Confidence is rendered to exactly two decimals (`0.7590766…` → `0.76`).

Frontmatter:

8. `language` and `model_size` present in `transcript_data` appear as quoted
   YAML scalars in the expected position.
9. Both absent — neither line appears, and the document is otherwise unchanged.

Skip behavior (against `export_transcript` and `has_exportable_content`):

10. `has_exportable_content` is false for `{}`, `None`, `{"segments": []}`;
    true for a dict with at least one segment.
11. `export_transcript` with an empty transcript writes no file.
12. `export_transcript` with an empty transcript leaves an existing `.md` at
    the target path byte-for-byte unchanged.
13. `export_transcript` with a non-empty transcript still writes, unchanged
    from today (regression guard).

## Out of scope

Discussed alongside this work and deliberately deferred:

- Removing `transcript.txt` (confirmed to have zero readers).
- Promoting the export to system of record with visible failures and startup
  reconciliation.
- Separating `transcripts.directory` from the recordings tree.
- An audio-retention policy.
- Improving export titles, which currently fall back to the session directory
  name (`recording_20260815_102906`) when there is no calendar event or
  user-supplied name.
