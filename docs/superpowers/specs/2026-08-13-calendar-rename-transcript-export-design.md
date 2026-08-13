# Calendar-Driven Rename, LLM Transcript Export & Calendar Remap — Design

Date: 2026-08-13

Builds on [2026-08-13-calendar-tagging-and-import-design.md](2026-08-13-calendar-tagging-and-import-design.md),
which shipped opt-in Outlook calendar tagging, the attendee dropdown in speaker naming, and
recording import. This spec covers three follow-on features:

1. **Rename suggestion**: after a recording is tagged to a calendar event, offer to rename the
   recording to the event's subject.
2. **LLM-ready transcript export**: alongside the existing internal `transcript.json`, write a
   human/LLM-readable Markdown export (transcript + calendar context + notes + AI summary/action
   items) to a separate, user-configurable transcripts folder, kept up to date on every save.
3. **Calendar remap**: let the user change an already-tagged recording's calendar event to a
   different overlapping event.

A fourth item from the original request — showing calendar attendees as an editable dropdown in
speaker naming, always allowing free text — was found to already be fully implemented
(`app/ui/speaker_name_panel.py`, `SpeakerNamePanel`, attendee-mode `QComboBox` with
`setEditable(True)` and a mutual-exclusion guard). A related bug (the dropdown's option list was
only reachable via the small arrow button, not on click/focus) was found during verification and
fixed separately, out of band from this spec (issue #61, commit `8282d20`). No further work is
needed for that item.

## Goals

- Reduce manual bookkeeping: once a recording is tied to a calendar event, its name should track
  the event's subject unless the user has already set a custom name.
- Make transcripts consumable by external LLM tools without needing the app's internal JSON
  schema or a running instance of TalkTrack.
- Let the user correct a wrong calendar match without deleting and re-tagging from scratch.

## Non-goals (this iteration)

- No bulk rename/export for pre-existing recordings — this only affects tagging/export events
  going forward. (A user can still trigger a fresh export by editing any segment, since edits
  re-trigger export.)
- No cloud sync or upload of the exported transcript file — local disk only, same trust boundary
  as the rest of the app.
- No configurable export template/format — one fixed Markdown+frontmatter format for now.

## Architecture

### 1. Rename suggestion (`app/main_window.py`)

After `_on_calendar_tag_requested` writes `calendar_event.json` (both the automatic
post-recording flow and the new remap flow from item 3 below), call a new
`_maybe_suggest_rename(session, event_to_save)`:

```python
def _maybe_suggest_rename(self, session, event):
    if session is None:
        return
    if session.get("name"):  # already custom-named — never overwrite
        return
    subject = event.get("subject", "").strip()
    if not subject:
        return
    from PyQt6.QtWidgets import QInputDialog
    new_name, ok = QInputDialog.getText(
        self, "Rename Recording?",
        "Rename this recording to match the calendar event?",
        text=subject,
    )
    if ok and new_name.strip():
        self._on_recording_renamed(new_name.strip())
```

Reuses the existing `_on_recording_renamed` handler (sets `session["name"]`, persists
`metadata.json`, updates `RecordingHeader`) — no new persistence path.

`QInputDialog.getText` is a built-in editable confirm dialog: pre-filled with the subject,
user can edit before accepting, or press Cancel to leave the name untouched. No custom dialog
class needed.

Skip condition uses the same truthy check as `_display_name_from_metadata`
(`app/ui/recording_header.py`) — a recording is "already custom-named" the moment
`metadata["name"]` is set, whether that happened via manual rename or an earlier accepted
rename-suggestion.

### 2. LLM-ready transcript export

#### 2a. Config (`app/utils/config.py`)

New `DEFAULT_CONFIG["transcripts"]["directory"]`, defaulting to a sibling of the recordings
output directory. `DEFAULT_CONFIG["output"]["directory"]` (config.py:23) is currently
`str(Path(__file__).parent.parent.parent / "recordings")` — i.e. `<project_root>/recordings`.
The new default follows the identical pattern, one level up, as a sibling directory:

```python
"transcripts": {
    "directory": str(Path(__file__).parent.parent.parent / "transcripts"),
},
```

Directory creation follows the exact pattern already used for `output.directory` in
`Config.__init__`: `os.makedirs(..., exist_ok=True)` wrapped in `try/except OSError`, falling
back to the default path and retrying on failure.

#### 2b. Settings UI (`app/ui/settings_dialog.py`)

New field in the existing "Output" tab (`output_tab`, added via `tabs.addTab(output_tab,
"Output")` around line 200 — the same tab that already hosts `output_dir_edit` /
`_browse_output_dir`), added as a second row in that tab's form layout, mirroring the Output
Directory control exactly:

- `QLabel("Transcript Export Folder:")`
- `self.transcripts_dir_edit = QLineEdit()`, loaded from `config.get("transcripts", "directory")`
- `self.transcripts_browse_btn = QPushButton("Browse...")`, wired to
  `QFileDialog.getExistingDirectory`, same as `_browse_output_dir`
- On save: `config.set("transcripts", "directory", self.transcripts_dir_edit.text())`

#### 2c. Export writer (`app/utils/transcript_export.py`, new)

Pure function, no Qt dependency, so it's unit-testable without `QApplication`:

```python
def build_export_markdown(
    metadata: dict,
    transcript_segments: list,       # TranscriptSegment objects
    speaker_names: dict,             # {speaker_id: name}
    calendar_event: dict | None,
    notes: str,
    summary_markdown: str | None,
    action_items: list | None,       # list of {"assignee", "task", "due"} dicts, or None
) -> str:
    """Render the full Markdown+YAML-frontmatter export document. Pure string
    building — no file I/O."""


def export_path_for(metadata: dict, calendar_event: dict | None, transcripts_dir: Path) -> Path:
    """<transcripts_dir>/<timestamp>_<sanitized-title>.md — title is the
    calendar subject if tagged, else the recording's display name."""


def export_transcript(
    metadata, transcript_segments, speaker_names, calendar_event, notes,
    summary_markdown, action_items, transcripts_dir,
) -> None:
    """build_export_markdown + export_path_for + atomic_write_text. Wraps
    the whole thing in try/except OSError — logs and returns, never raises
    into a save path."""
```

**Frontmatter fields:** `title`, `recording_date` (ISO, from `metadata["started_at"]`),
`duration_seconds`, `source_directory` (basename of `metadata["directory"]`, so the export can be
traced back to its source recording), `calendar` block (`subject`/`organizer`/`attendees`,
omitted entirely if `calendar_event` is `None`), `speakers` block (`{speaker_id: name}`, omitted
if empty).

**Body sections**, each omitted (not emitted empty) when the underlying data is absent:
`# Summary` (from `summary_markdown`), `# Action Items` (from `action_items`, rendered as a
checklist — `- [ ] <assignee>: <task> (due <date>)`, falling back to just `<task>` when
assignee/due are missing), `# Notes` (verbatim `notes` string), `# Transcript` (always present
when there are segments — `**[HH:MM:SS] <name-or-speaker-id>:** <text>` per segment, using
`speaker_names.get(seg.speaker, seg.speaker)`).

**Filename:** `export_path_for` builds `<sanitized-title>_<YYYYMMDD>_<HHMM>.md` — sanitization
strips characters invalid in Windows filenames (`\/:*?"<>|`) and collapses whitespace to single
underscores, capped at a reasonable length (e.g. 60 chars of the title portion) so a long event
subject can't blow past `MAX_PATH`. Timestamp comes from `metadata["started_at"]`, not wall-clock
export time, so re-exporting the same recording overwrites the same file rather than
accumulating duplicates.

#### 2d. Wiring into save points (`app/main_window.py`)

New `self._export_transcript()` helper, gathering current state from `self._current_session`,
`self._transcript.segments`, `self.transcript_viewer` (speaker names), the loaded
`calendar_event`, `self.notes_panel.get_notes()`, and — if present on disk —
`summary.md` / `action_items.json` for the session, then calling `transcript_export.export_transcript`
with `self.config.get("transcripts", "directory")`.

Called from every place that currently persists `transcript.json` or related per-recording data:
- `_save_transcript()` (after transcription, after any segment edit/undo/redo, after speaker
  rename) — add the export call at the end.
- Notes save path (wherever `notes_panel`'s content is persisted to `notes.json`).
- `_on_calendar_tag_requested` (after writing `calendar_event.json`) — export picks up the new
  calendar context immediately, without waiting for another transcript edit.
- Summary/action-item generation completion handlers — export picks up the freshly generated
  content.

Each of these call sites already has a "this can be a no-op if there's no `_current_session`"
guard for their own persistence; `_export_transcript` adds the same guard (return early if
`self._current_session` or `self._transcript` is `None`) and is itself wrapped so a failure
never surfaces to the user — same best-effort framing as `export_transcript`'s own try/except.

### 3. Calendar remap

#### 3a. `RecordingHeader` (`app/ui/recording_header.py`)

New signal `change_calendar_requested = pyqtSignal()`. New `self.change_calendar_btn =
QPushButton("Change")`, added to the calendar-event row, shown/hidden in lockstep with
`self.calendar_label` (i.e. inside the existing `if calendar_event: ... else: ...` branch in
`set_recording`).

#### 3b. `MainWindow` — shared lookup dispatch

Refactor `_maybe_lookup_calendar`'s worker-creation tail into a shared helper:

```python
def _dispatch_calendar_lookup(self, session, started_dt, stopped_dt):
    worker = CalendarLookupWorker(started_dt, stopped_dt)
    worker.session = session
    worker.finished.connect(self._on_calendar_lookup_finished)
    self._calendar_lookup_workers.append(worker)
    worker.start()
```

`_maybe_lookup_calendar` keeps its existing guards (feature enabled, session not None, not
already dismissed, not already tagged, valid timestamps) and calls `_dispatch_calendar_lookup`
at the end instead of inlining worker creation.

New `_on_change_calendar_requested`, connected to `RecordingHeader.change_calendar_requested`:

```python
def _on_change_calendar_requested(self):
    session = self._current_session
    if session is None:
        return
    started, stopped = session.get("started_at"), session.get("stopped_at")
    if not started or not stopped:
        return
    from datetime import datetime
    try:
        started_dt = datetime.fromisoformat(started)
        stopped_dt = datetime.fromisoformat(stopped)
    except ValueError:
        return
    self.status_label.setText("Looking up calendar events...")
    self._dispatch_calendar_lookup(session, started_dt, stopped_dt)
```

Only the enabled-check and timestamp validity are needed here — deliberately skips the
"already tagged" and "prompt dismissed" guards, since this path only runs when the user
explicitly clicked "Change" on an already-tagged recording.

`_on_calendar_lookup_finished` (existing) needs no changes — it already just shows the banner
via `self._is_current_session(session)` + `self.calendar_banner.show_matches(events)`, which
works identically whether the lookup was automatic or manual. Add one branch: if `events` is
empty, set `self.status_label.setText("No other matching calendar events found.")` instead of
silently doing nothing (today's empty-list handling is silent, which is fine for the automatic
background trigger but would look broken for a user-initiated click).

`_on_calendar_tag_requested` (existing) needs no changes — it already unconditionally
overwrites `calendar_event.json` and refreshes the header, which is exactly "remap" behavior.
Add the `_maybe_suggest_rename` call here (see section 1) so remapping through to a new event
also offers the rename suggestion, subject to the same already-named skip check.

## Data Flow Summary

```
Recording finishes
  -> _maybe_lookup_calendar -> CalendarLookupWorker -> _on_calendar_lookup_finished
  -> banner shows matches -> user taps "Tag Recording"
  -> _on_calendar_tag_requested: write calendar_event.json, update header,
     update attendee dropdowns, _maybe_suggest_rename(), _export_transcript()

User clicks "Change" on an already-tagged recording
  -> _on_change_calendar_requested -> _dispatch_calendar_lookup -> _on_calendar_lookup_finished
  -> banner shows matches (or "no other matches" status) -> user taps "Tag Recording"
  -> _on_calendar_tag_requested (same handler, same effects as above)

Any transcript/notes/summary/action-item save
  -> existing persistence (transcript.json / notes.json / summary.md / action_items.json)
  -> _export_transcript() -> transcript_export.export_transcript() -> <transcripts_dir>/<file>.md
```

## Error Handling

- Rename dialog: `QInputDialog.getText` cancel or blank input leaves the existing name untouched
  — no error state, just a no-op.
- Transcript export: every failure mode (bad path, disk full, permission denied, malformed
  segment data) is caught inside `export_transcript` and logged; it never raises into a caller
  and never blocks the primary save it's attached to. This mirrors the existing calendar-lookup
  best-effort framing (`outlook_calendar.py`'s "no matches" degradation).
- Calendar remap: reuses all existing error handling from the original tagging flow — a failed
  or empty lookup just means no banner (or the new "no other matches" status text), same as an
  automatic lookup that finds nothing.

## Testing

- `app/utils/transcript_export.py` is pure Python (no Qt) — TDD with `unittest`/`pytest` per
  [ways-of-working.md](../../../.claude/rules/ways-of-working.md): frontmatter field presence/
  omission, section omission when data is absent, filename sanitization, action-item checklist
  rendering, speaker-name fallback to raw speaker_id.
- `_maybe_suggest_rename`'s skip logic (already-named check, blank-subject check) is plain
  Python and testable by extracting the condition into a small pure helper if the implementer
  finds `main_window.py` too heavy to unit test directly — otherwise smoke-test only, per the
  project's UI-testing convention.
- `RecordingHeader`'s new button/signal: smoke-test via `python -c` snippet (instantiate,
  call `set_recording` with a calendar event, assert button visible; call with `None`, assert
  hidden), consistent with the "no Qt widget tests beyond pure-helper unit tests" rule.
- Config default/creation path for `transcripts.directory`: extend
  `tests/test_config.py` alongside the existing `output.directory` coverage.

## Open Questions For The Plan

None — all three items are fully specified above, including exact file/line anchors for every
integration point (settings tab, save-point call sites, header widget layout).
