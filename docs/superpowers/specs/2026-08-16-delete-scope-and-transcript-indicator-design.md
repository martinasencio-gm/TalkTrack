# Delete Scope and the Transcript Indicator

Issue: martinasencio-gm/TalkTrack#73

## Goal

Make deleting a recording actually empty its folder, keep the exported
Markdown transcript in step with the delete that should remove it, and make
the "Transcribed" pill in the recordings list report the export the user can
actually open — not an internal file they never see.

## Background

A recording owns two separate artifacts in two separate places:

- `<recordings>/<session>/` — audio WAVs, `metadata.json`, `transcript.json`,
  `summary.md`, `action_items.json`, `speaker_names.json`, plus incidental
  files the app writes as it goes: `embeddings.npz`, `chat_history.json`,
  `calendar_event.json`, `notes.txt`.
- `<transcripts>/<session>_<stamp>.md` — the export written by
  `app/utils/transcript_export.py`, keyed off the session directory name and
  `started_at` so renames and calendar retagging keep hitting the same file.

Three defects follow from that split.

**Deleting "recordings only" leaves the folder standing.**
`_delete_audio_files()` walks `metadata["audio_files"]` and removes exactly
those paths, then rewrites `metadata.json` with an empty `audio_files`. Every
other file in the directory survives — including ones no scope ever removes,
like `embeddings.npz` and `chat_history.json`.

**The "Transcribed" pill checks the wrong file.** `_build_row_widget()` tests
`<session>/transcript.json`. A recording whose export was never written (a
zero-segment transcript is deliberately skipped, see `c496b59`) or was
written before the data-dir move still shows "Transcribed" while the
transcripts folder holds nothing for it.

**Old exports are stranded.** `DEFAULT_CONFIG["transcripts"]["directory"]`
still resolves repo-relative (`<repo>/transcripts`), while `APP_DATA_DIR`
moved to `Documents/TalkTrack` in `c49d8c6`/`d8e86fc`. On this machine the
configured folder holds 3 exports whose recordings are long deleted, and the
repo folder holds all 5 exports ever written — including both currently
transcribed recordings. Writes and deletes read the same config key today, so
this is historical divergence rather than an ongoing bug, but the files stay
split until something moves them.

Deleting the export on a transcript delete already works
(`_delete_exported_transcript`, added in `83d2f87`); it looked broken only
because of the stranded-files problem above.

## Design

### 1. Delete scopes

`_perform_delete()` in `app/ui/recordings_list.py` becomes:

| Scope | Recording folder | `transcripts/*.md` | Signal |
|---|---|---|---|
| Recordings only | `_rmtree_robust()` | kept | `recording_deleted` |
| Transcriptions only | transcript artifacts removed, audio stays | deleted | `recording_files_changed` |
| Both | `_rmtree_robust()` | deleted | `recording_deleted` |

"Recordings only" now removes the session directory outright, so it emits
`recording_deleted` rather than `recording_files_changed` — the session is
gone, not merely changed. `about_to_delete` still fires first for both
folder-removing scopes so playback releases its handles.

The distinction between "recordings only" and "both" is now exactly one
thing: whether the exported Markdown survives. That is the intended product
behaviour — the export is the durable artifact, audio is the disposable one —
but the dialog must say so. `DeleteScopeDialog` labels become:

- *Recording folder — audio and transcript files; keeps the exported transcript in transcripts/*
- *Transcriptions only — transcript/summary/action items; keeps audio*
- *Everything — including the exported transcript*

`_delete_audio_files()` and `TRANSCRIPTION_FILENAMES`-adjacent behaviour: the
former loses its last caller and is deleted along with its tests.
`_delete_transcription_files()` stays as-is for the transcriptions-only path.

### 2. "Transcribed" pill follows the export

New pure helper alongside `export_path_for()` in `transcript_export.py`:

```python
def has_exported_transcript(metadata, transcripts_dir) -> bool
```

Returns False for a missing/empty `transcripts_dir` rather than raising, and
otherwise tests `export_path_for(...).exists()`. It lives in
`transcript_export.py` because that module already owns the naming rule; a
second module deriving export filenames would be a second place to get the
rule wrong.

`_build_row_widget()` calls it for the "Transcribed" pill. The "Audio" pill is
unchanged.

`_selected_transcribed()` and `_selected_untranscribed()` deliberately keep
testing `transcript.json`. They drive the right-click **Transcribe N
Recordings** and **Export N Transcripts** actions: `transcript.json` is what
the app can re-export from, and re-running Whisper on already-transcribed
audio costs minutes and produces nothing new. The practical result is that a
recording with a transcript but no export shows as untranscribed and offers
"Export Transcripts" — one click to regenerate the missing file. This
divergence between pill and menu is intentional and belongs in a comment at
both sites.

### 3. One-time import of stranded exports

New module `app/utils/transcripts_migration.py`:

```python
def import_legacy_exports(legacy_dir, transcripts_dir) -> list[str]
```

Moves `*.md` from `legacy_dir` into `transcripts_dir`, skipping any filename
already present in the target, and returns the moved filenames. No-ops when
`legacy_dir` is missing, when the two paths resolve to the same directory, or
when nothing matches. Nothing is deleted or overwritten — a name collision
leaves both files where they are, since the target copy is the newer one.

`main.py` calls it once after config load, guarded by a new config flag
`transcripts.legacy_import_done` (default `false`, set `true` after the call
regardless of how many files moved, so a repeat launch does no filesystem
work). `legacy_dir` is `DEFAULT_CONFIG["transcripts"]["directory"]`; the
target is the configured value.

Orphaned exports whose recordings no longer exist are left alone. Sweeping
them automatically would delete exactly the files "Recordings only" is
designed to preserve.

## Testing

TDD (`tests/`, unittest + mocks, run under `.venv\Scripts\python.exe -m pytest`):

- `test_recordings_list_delete_scope.py` — each scope against a populated tmp
  session directory: folder removed vs. retained, export removed vs. retained,
  which signal fired. Covers the leftovers (`embeddings.npz`,
  `chat_history.json`) that motivated the change.
- `test_transcript_export.py` — `has_exported_transcript` true/false, missing
  transcripts dir, missing/unparseable `started_at`.
- `test_transcripts_migration.py` — moves new files, skips existing names,
  no-op on same dir, no-op on missing legacy dir, returns moved names.

UI wiring (`DeleteScopeDialog` labels, `_build_row_widget`) gets an import
smoke test only, per the project's PyQt convention.

## Out of scope

- Changing `DEFAULT_CONFIG`'s repo-relative defaults. They are the fallback
  when `Documents` is unwritable and are load-bearing in `Config.load()`.
- Any automatic cleanup of orphaned exports.
- Undo for deletes.
