# Phase 2: Multi-Select Transcript Creation — Design

## Status

Story 1.3 (Delete Recordings) is already fully implemented in `app/ui/recordings_list.py`
(`_delete_recording`, `_delete_selected_recordings`, confirmation dialogs, `about_to_delete`/
`recording_deleted` signals for cache release). No further work needed — marked done.

This spec covers Story 2.1: Multi-Select Transcript Creation, combining batch transcription
and batch Markdown export from the recordings list's multi-select context menu.

## Background

`MainWindow` already has two pieces of pipeline logic built for single recordings that turn
out to be directly reusable for a batch of recordings, with no new worker or persistence code:

- `_start_transcription(audio_path, session=None)` (`app/main_window.py:963`) queues onto
  `self._pending_transcriptions` when a transcription is already running, and processes the
  queue serially via `_process_pending_transcriptions`. `session` is just the recording's
  metadata dict (needs a `"directory"` key).
- When a transcription finishes for a session that is no longer the one open in the UI,
  `_display_final_transcript` (`app/main_window.py:1233`) detects that via `_is_current_session`
  and calls `_write_transcript_for_session` (writes `transcript.json`/`transcript.txt` to that
  recording's own directory) followed by `_export_transcript(session)` — all without touching
  `self.transcript_viewer` / `self.notes_panel`. It also calls `self.recordings_list.refresh()`
  so the "Transcribed" badge updates.
- `_export_transcript(session=None)` (`app/main_window.py:1149`) is already a self-contained,
  disk-driven operation: given a session dict, it reads `transcript.json`, `speaker_names.json`,
  `notes.txt`, `summary.md`, `action_items.json` from `session["directory"]` and writes the
  Markdown export via `transcript_export.export_transcript`. It returns silently (no-op) if
  `transcript.json` doesn't exist yet.

Because both operations already tolerate being called for a recording that isn't the one
currently open, batch transcribe and batch export need no new background-thread machinery —
just a loop over the selection calling these two existing methods.

## UI: RecordingsList multi-select context menu

`app/ui/recordings_list.py`, `_show_context_menu`, multi-select branch: add two new
`QAction`s after the existing "Open Recordings Folder" / "Open Transcripts Folder" actions,
before the separator + Delete action.

- **"Transcribe N Recordings"** — N = count of selected recordings whose directory has no
  `transcript.json`. Disabled (and not shown with a stale count) if that count is 0. Emits
  `transcribe_selected_requested` with the list of metadata dicts for just that untranscribed
  subset.
- **"Export N Transcripts"** — N = count of selected recordings whose directory *has*
  `transcript.json`. Disabled if that count is 0. Emits `export_selected_requested` with the
  list of metadata dicts for just that transcribed subset.

Both counts are computed once per menu invocation by checking
`(Path(metadata["directory"]) / "transcript.json").exists()` for each selected item, matching
the existing "Transcribed" badge check in `_build_row_widget`.

New signals on `RecordingsList`:
```python
transcribe_selected_requested = pyqtSignal(list)  # list[dict] metadata, untranscribed only
export_selected_requested = pyqtSignal(list)      # list[dict] metadata, transcribed only
```

## Wiring in MainWindow

In `_setup_ui`, alongside the other `self.recordings_list.*.connect(...)` calls:

```python
self.recordings_list.transcribe_selected_requested.connect(self._on_transcribe_selected)
self.recordings_list.export_selected_requested.connect(self._on_export_selected)
```

New handlers:

```python
def _on_transcribe_selected(self, recordings):
    queued = 0
    for metadata in recordings:
        audio_files = metadata.get("audio_files", {})
        audio_path = (audio_files.get("combined") or audio_files.get("system")
                      or audio_files.get("mic"))
        if audio_path:
            self._start_transcription(audio_path, session=metadata)
            queued += 1
    if queued:
        self.status_label.setText(f"Queued {queued} recording(s) for transcription.")

def _on_export_selected(self, recordings):
    for metadata in recordings:
        self._export_transcript(metadata)
    self.status_label.setText(f"Exported {len(recordings)} transcript(s).")
```

`_on_transcribe_selected` calls `_start_transcription` once per recording in a tight loop.
The first call starts the worker immediately; every subsequent call finds
`_transcription_busy()` true and appends to `_pending_transcriptions` — reusing the existing
serial queue exactly as it already behaves for the "recording finished → auto-transcribe next"
and cancel-and-resume paths. Per-item progress/status text and per-item error handling
(`_on_transcription_error`) are unchanged; nothing here needs to distinguish a batch-queued
item from any other queued item.

`_on_export_selected` runs synchronously on the UI thread, same as the existing single-session
call site in `_on_recording_selected`. Each recording's `transcript.json` is typically small
and export is disk I/O + string formatting, no model inference — consistent with treating it
as fire-and-forget already elsewhere in the codebase.

## Error handling

- A recording with no audio files at all (`audio_path` is `None`) is silently skipped in
  `_on_transcribe_selected` — it can't be transcribed, and this can't happen for anything
  showing in the list since `refresh()` requires `metadata.get("directory")` but doesn't
  guarantee audio; skip rather than pop a dialog per bad item in a multi-item batch.
  (This mirrors `_start_diarization`'s existing `if not audio_path: ...` guard style.)
- `_export_transcript` already no-ops per-recording if `transcript.json` is missing or
  unreadable — but the menu action only ever sends recordings that were confirmed to have
  `transcript.json` at menu-build time, so this is only a race-condition safety net (e.g. the
  file was deleted between menu open and click).
- Per-item transcription failures go through the existing `_on_transcription_error` path
  (a status message / notification), then `_process_pending_transcriptions()` continues to the
  next queued item — one bad recording doesn't stop the batch.

## Testing

- `tests/test_recordings_list_folder_actions.py`-style additions (or a new
  `tests/test_recordings_list_batch_transcript.py`) covering `RecordingsList`:
  - Multi-select menu computes the untranscribed/transcribed counts correctly from a mix of
    recordings with and without `transcript.json`.
  - `transcribe_selected_requested` fires with exactly the untranscribed subset.
  - `export_selected_requested` fires with exactly the transcribed subset.
  - Both actions are absent/disabled-equivalent (not emitted) when their respective count is 0.
- `MainWindow` handler tests (mocking `_start_transcription` and `_export_transcript`) asserting:
  - `_on_transcribe_selected` calls `_start_transcription` once per recording with a resolved
    audio path, skipping recordings with no audio files.
  - `_on_export_selected` calls `_export_transcript` once per recording, passing the metadata
    dict straight through.

## Out of scope

- A visible progress/queue panel showing per-item batch status (confirmed not wanted this pass
  — status-label text is sufficient, matching how single-item transcription already reports
  progress).
- Any change to `_export_transcript`, `_start_transcription`, or the transcription/diarization
  worker classes themselves — this story is UI wiring only, reusing existing methods unchanged.
- Story 1.3 additions (already complete, see Status above).

## Self-review

- No placeholders — every handler and action above has concrete code.
- Internal consistency: the "Out of scope" section matches what was declined during design
  (progress panel), and the Status section matches the confirmed answer that 1.3 needs no
  further work.
- Scope: single cohesive story (multi-select transcribe + export), one plan.
- Ambiguity: "N Recordings"/"N Transcripts" counts are pinned to exact per-menu-open
  recomputation logic; skip-vs-error behavior for missing audio is explicit.
