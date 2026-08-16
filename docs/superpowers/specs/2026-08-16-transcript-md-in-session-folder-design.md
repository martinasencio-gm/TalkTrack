# Transcript.md moves into the recording folder

**Date:** 2026-08-16
**Status:** Approved

## Problem

The LLM-ready Markdown export currently lives in a separate, configurable
`transcripts/` folder (`transcripts.directory`). That folder needs its own
management surface: a Settings row, an Open Transcripts Folder menu item, a
legacy-path migration (`transcripts_migration.py`), name/timestamp keyed
filenames (`export_path_for`), delete-scope coordination
(`_delete_exported_transcript`), an export-backed "Transcribed" pill
(`has_exported_transcript`), and a forced export-before-delete step (#73).

Decision: the Markdown export becomes `transcript.md` inside each recording's
own session folder — an enhancement of `transcript.json`, not a separate
managed artifact. All transcripts-folder management is removed.

## Design

### 1. Export target

- `transcript_export.export_transcript()` writes `transcript.md` (fixed
  filename) into the session directory, alongside `transcript.json`.
- Same triggers as today: every transcript/notes/summary/action-items save.
- Atomic write (`atomic_io`) as before.
- `export_path_for()`'s sanitized-name + timestamp filename logic is removed;
  the file is keyed by location, so renames/retags need no handling.
- `has_exportable_content()` gate (skip empty transcripts) stays.

### 2. Config and UI removal

- Remove config keys `transcripts.directory` and
  `transcripts.legacy_import_done`.
- Remove the Settings > Output transcripts-folder row (line edit + browse
  button + path cleanup).
- Remove File > Open Transcripts Folder.
- Delete `app/utils/transcripts_migration.py` and its `MainWindow.__init__`
  call site.

### 3. One-time import of existing exports

- New config flag `transcripts.session_import_done` (default False).
- On first launch after upgrade (from `MainWindow.__init__`), scan the
  previously configured transcripts dir (read the raw stored
  `transcripts.directory` value if present) and the legacy repo-relative
  `transcripts/` default. For each `*.md` whose filename prefix matches an
  existing recording folder name under `output.directory`, move it into that
  folder as `transcript.md` — skipping if a `transcript.md` already exists
  there (leave the old export in place in that case).
- Orphaned exports (no matching recording folder — e.g. recordings deleted
  under the old "recordings only" scope, where the export is the only
  surviving copy) are left untouched, forever.
- Set the flag afterwards; the app never reads or writes the old folder
  again.

### 4. Delete scopes (`RecordingsList._perform_delete`)

- **Everything** (`DELETE_BOTH`): `rmtree` the session folder. The external
  `_delete_exported_transcript` call is removed (nothing external anymore).
- **Transcriptions only** (`DELETE_TRANSCRIPTIONS`): delete the transcript
  artifacts — current `TRANSCRIPTION_FILENAMES` plus `transcript.md`. Audio
  and the rest survive. If the session has no audio files (a prior
  recordings-only delete removed them), the folder holds nothing meaningful
  and is `rmtree`'d instead ("folder deleted if neither recording nor
  transcript exist").
- **Recordings only** (`DELETE_RECORDINGS`): delete only the audio files —
  the mic/system/combined tracks listed in metadata plus stray
  `*.wav`/`*.mp3` chunk files in the folder. `metadata.json`,
  `transcript.json`, `transcript.md`, `speaker_names.json`, `summary.md`,
  `action_items.json`, `notes.txt`, `chat_history.json`,
  `calendar_event.json`, `embeddings.npz` all survive: the session remains a
  transcript-only entry (readable, searchable, chattable, not playable). If
  the session has no `transcript.json` and no `transcript.md`, the scope
  degenerates to `rmtree` of the whole folder. The forced
  export-before-delete logic (#73) is removed.
- `about_to_delete` still fires before any audio removal;
  `recording_files_changed` fires for partial deletes that keep the folder,
  `recording_deleted` when the folder goes.

### 5. Transcribed pill

- The recordings-list "Transcribed" pill reverts to checking
  `transcript.json` in the session folder (covers pre-export-era recordings;
  json and md are written together going forward).
- `has_exported_transcript()` and `_delete_exported_transcript()` are
  removed. Context-menu Transcribe/Export actions unchanged (already key off
  `transcript.json`).

### 6. Audio-less sessions in the UI

- Rows whose metadata audio files are all missing: Play action does nothing
  today if files are absent — verify and, where cheap, disable/hide Play.
- Delete dialog still offers all scopes for such rows; recordings-only on an
  audio-less row finds no audio files to remove, and the degenerate-rmtree
  rule in section 4 handles the no-transcript case.
- Segment playback and waveform features already tolerate missing audio;
  verify, don't rebuild.

### 7. External consumers

- Update the `talktrack-transcripts` Claude skill: locate TalkTrack's
  `output.directory` from `~/.talktrack/settings.json` and glob
  `<recordings_dir>/*/transcript.md`. Same change for
  `talktrack-batch-summarize` if it reads the transcripts folder.

### 8. Tests and docs

- Rework: `test_transcript_export.py` (path/keying tests),
  `test_recordings_list_delete_scope.py` (new scope semantics),
  `test_recordings_list_badges.py` (pill source), `test_config.py`
  (removed/added keys), `test_settings_dialog_path_cleanup.py` (removed row).
- New tests: one-time session import (match, skip-existing, orphan
  untouched, flag set), selective audio delete (survivor list, degenerate
  rmtree cases).
- Docs: CLAUDE.md feature/config/structure sections updated.
- GitHub issue filed on origin (`--repo` per memory) before the work is
  committed.

## Out of scope

- Reconstructing session folders for orphaned exports.
- Any change to what the Markdown export contains.
- Live-updating open UI when files are deleted externally.
