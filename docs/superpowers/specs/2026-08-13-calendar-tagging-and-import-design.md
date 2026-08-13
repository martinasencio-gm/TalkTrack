# Calendar Tagging & Recording Import — Design

Date: 2026-08-13

## Summary

Two related features:

1. **Calendar tagging**: after a recording finishes (live or imported), optionally look up the
   local Outlook desktop calendar for events overlapping the recording's time window, and let
   the user tag the recording with the matching event (subject, organizer, attendees).
2. **Recording import**: let the user import an existing audio file (wav/mp3/m4a — e.g. a
   downloaded Teams/Zoom cloud recording) as a new session, running it through the same
   transcribe → diarize pipeline as a live recording.

Once a recording is calendar-tagged, its attendee list feeds the speaker-naming UI: each speaker
gets an editable dropdown of remaining (unassigned) attendees instead of a bare text field, with
names removed from other speakers' dropdowns once assigned.

## Goals

- Read-only, local, no-cloud-auth calendar lookup (Outlook desktop app via COM, not Graph API).
- Opt-in — off by default, explicit Settings toggle.
- Never block or fail existing flows if Outlook isn't installed/running — this is a nice-to-have.
- Import reuses the existing transcription/diarization pipeline rather than duplicating it.
- Calendar attendee names speed up (but never restrict) speaker naming.

## Non-goals (this iteration)

- No Microsoft Graph / OAuth integration.
- No injection of calendar metadata into AI summarizer/chat/action-item prompts (storage/display
  only for now — can be a follow-up once tagging itself is validated).
- No calendar *write* access (no creating/editing events).
- No support for non-Outlook calendar providers (Google Calendar, etc.).

## Architecture

### 1. `app/integrations/outlook_calendar.py` (new)

Plain-Python module, no Qt dependency, wrapping `win32com.client` (pywin32, already a project
dependency but previously unused).

```python
def find_overlapping_events(start: datetime, end: datetime, tolerance_minutes: int = 5) -> list[dict]:
    """Return calendar events overlapping [start - tolerance, end + tolerance].

    Each dict: {"subject": str, "start": datetime, "end": datetime,
                "organizer": str, "attendees": list[str]}

    Returns [] if Outlook isn't installed/running or any COM error occurs —
    this integration is optional and must never raise into callers.
    """
```

Implementation: `win32com.client.Dispatch("Outlook.Application").GetNamespace("MAPI")
.GetDefaultFolder(9)` (olFolderCalendar), `.Items`, sorted + `IncludeRecurrences = True`,
filtered with `.Restrict()` on the date range, then further filtered in Python for actual overlap
(Restrict's date filtering on recurring items is unreliable — over-fetch a bit and filter
precisely in Python).

All COM interaction wrapped in `try/except Exception: return []` at the function boundary — any
failure (Outlook not installed, not running, COM error, permission prompt) degrades to "no
matches" rather than propagating.

### 2. `CalendarLookupWorker` (new, `app/ui/calendar_lookup_worker.py`)

```python
class CalendarLookupWorker(QThread):
    finished = pyqtSignal(list)  # list of event dicts, possibly empty
```

Runs `find_overlapping_events` off the UI thread (COM calls can be slow). Carries a `.session`
attribute bound at creation, following the session-binding convention in
[transcription-pipeline.md](../../../.claude/rules/transcription-pipeline.md) — the
`finished` handler reads `worker.session`, never `self._current_session`.

Dispatched from `MainWindow._on_recording_finished` (live recordings) and from the import flow's
session-creation completion (section below), gated by `config.get("calendar", "enabled")`.
Not added to the serial transcription queue — calendar lookup is independent of and can run
concurrently with transcription.

### 3. Suggestion banner

New small widget (or a mode of an existing lightweight banner if one exists — otherwise a plain
`QFrame` row above the transcript viewer, styled like other Catppuccin-band UI per
[ui-patterns.md](../../../.claude/rules/ui-patterns.md)).

- Zero matches → banner never shown.
- One match → "📅 *Subject* · HH:MM–HH:MM · organizer" with **Tag Recording** / **Dismiss**
  buttons.
- Multiple matches → each candidate listed with its own **Tag** button, plus one **Dismiss**
  for all.
- **Tag Recording** writes `calendar_event.json` (schema below) into the session directory and
  hides the banner; **Dismiss** sets `metadata.json["calendar_prompt_dismissed"] = true` (so a
  freshly-reopened session doesn't re-prompt) and hides the banner.
- Follows the existing tray-notification philosophy in
  [ui-patterns.md](../../../.claude/rules/ui-patterns.md): if the window is hidden to tray when
  the worker finishes, do not show the banner immediately — flag it (reuse
  `_flag_success_notification`-style tray overlay) and show the banner next time that session is
  displayed.

### 4. Storage: `calendar_event.json`

New per-recording file, parallel to `speaker_names.json`/`chat_history.json`:

```json
{
  "subject": "Q3 Roadmap Sync",
  "start": "2026-08-13T14:00:00",
  "end": "2026-08-13T14:45:00",
  "organizer": "Jane Smith",
  "attendees": ["Jane Smith", "John Doe", "Priya Patel"]
}
```

`RecordingHeader` (`app/ui/recording_header.py`) loads this file when present (same pattern as
its other per-recording metadata reads) and shows a line: `📅 Q3 Roadmap Sync · 3 attendees`.

### 5. Settings

New `calendar` config section:

```json
{"calendar": {"enabled": false}}
```

New "Calendar" tab in `SettingsDialog` (or folded into General if it turns out to be a single
checkbox — implementer's call, but a dedicated tab keeps room for future calendar options)
with: `QCheckBox("Suggest calendar tags for finished recordings")`. Off by default. When the
underlying Outlook COM call fails at lookup time, nothing surfaces to the user beyond the debug
log — no "Outlook not found" error dialog, since this is opt-in and best-effort.

## Recording Import

### 1. Trigger

"Import..." button in `app/ui/recordings_list.py`, opens `QFileDialog.getOpenFileName` filtered
to `Audio Files (*.wav *.mp3 *.m4a)`.

### 2. Timestamp dialog

Small modal dialog (new `app/ui/import_timestamp_dialog.py`) with a `QDateTimeEdit` pre-filled
from the file's OS last-modified time (`os.path.getmtime`), editable by the user before
confirming. Cancel aborts the import with no side effects.

### 3. Session creation

1. Create a new timestamped session directory (same naming convention as live recordings:
   `recordings/YYYY-MM-DD_HH-MM-SS/`).
2. If the source is `.m4a`, convert to `.wav` via `ffmpeg` (same subprocess pattern as
   `Recorder._convert_to_mp3`); if `.wav`/`.mp3`, copy directly. If `ffmpeg` is required (m4a)
   but not found (`shutil.which("ffmpeg")` — reuse `dependency_checker.check_ffmpeg` logic),
   abort the import with a clear error message rather than partially importing.
3. Probe duration via `soundfile.info(path).duration`.
4. Compute `stopped_at = started_at + duration` from the user-confirmed timestamp.
5. Write `metadata.json`:
   ```json
   {
     "directory": "...",
     "started_at": "...",
     "stopped_at": "...",
     "duration": 1234.5,
     "audio_files": {"combined": "combined_audio.wav"},
     "imported": true,
     "source_filename": "original_name.m4a",
     "capture_mode": "imported"
   }
   ```
6. Emit the same `recording_finished`-shaped session dict `MainWindow` already handles, so the
   rest of `_on_recording_finished` (transcript viewer setup, recordings list refresh, tab
   switch, auto-transcribe) runs unmodified.

### 4. Diarization mode

Imports have no mic/system channel split, so "simple" diarization (channel-based) cannot apply.
`MainWindow`'s diarization-mode selection reads `session.get("imported")`: when true, only the
full-pyannote path is attempted (skipping the simple-diarizer branch entirely); if pyannote isn't
configured (no HF token), the transcript is produced with no speaker labels, matching today's
existing behavior when diarization is unavailable — no new error state.

### 5. Calendar lookup for imports

After metadata is written, imports go through the exact same `CalendarLookupWorker` dispatch as
live recordings (using the user-confirmed `started_at`/computed `stopped_at`).

## Speaker Naming from Calendar Attendees

`SpeakerNamePanel.set_speakers(segments, speaker_names=None, attendees=None)` gains an optional
`attendees` parameter (list of names from `calendar_event.json`, or `None`/`[]` when no tag
exists).

- **No attendees**: unchanged — each row is a plain `QLineEdit`, exactly as today.
- **Attendees present**: each row becomes an editable `QComboBox` (`setEditable(True)`, so a
  name not on the invite can still be typed freely). Items: `["", *available_attendees]`, where
  `available_attendees` excludes names already selected in *other* rows.
- **Mutual exclusion**: the panel maintains its own `{speaker_id: name}` view of current
  selections. On any row's `currentTextChanged`/selection change, `_refresh_combo_options()`
  rebuilds every *other* row's item list, so each attendee can be assigned to at most one
  speaker. The changed row's own list always retains its current value.
- `get_speaker_names()` behavior is unchanged (reads current text from whichever widget type is
  active).

## Error Handling

- Outlook COM unavailable/errors: swallowed in `outlook_calendar.py`, `[]` returned, DEBUG log
  only, no UI error surfaced (opt-in, best-effort feature).
- Import: missing `ffmpeg` for m4a → clear inline error, import aborted, no partial session left
  behind (delete the partially-created directory on failure, mirroring
  `Recorder.stop_recording`'s min-duration-discard cleanup).
- Import: file unreadable / zero duration → same abort-and-cleanup path.

## Testing

- `tests/test_outlook_calendar.py` (new): mock `win32com.client.Dispatch`; cases — no overlap,
  single overlap, multiple overlaps, dispatch raises → `[]`, boundary/tolerance edge cases.
- Import session-construction logic extracted into a plain testable function (not embedded in a
  Qt slot) — `tests/test_recording_import.py` (new): metadata field construction, m4a-requires-ffmpeg
  abort path (ffmpeg subprocess mocked), duration probing.
- `tests/test_speaker_name_panel.py` (extend): attendee-mode row creation, mutual exclusion
  across multiple selections/reselections, free-text entry still accepted, no-attendees fallback
  unchanged.
- New UI pieces (banner, Calendar settings tab, import timestamp dialog) smoke-tested via
  `python -c "from app.x import Y; ..."` per
  [ways-of-working.md](../../../.claude/rules/ways-of-working.md) — no full Qt widget tests.

## Open Items for Implementation

- Confirmed: the codebase's only existing warning UI (`_check_silent_capture` in
  `main_window.py`) is a one-shot `QMessageBox`, not a persistent widget — unsuitable here since
  the calendar banner needs per-candidate action buttons and must stay visible until the user
  acts. Build a new lightweight `QFrame`-based banner widget; do not force-fit `QMessageBox`.
- Settings tab placement (dedicated "Calendar" tab vs. folding into "General") — leaning
  dedicated tab per above, but not load-bearing either way.
