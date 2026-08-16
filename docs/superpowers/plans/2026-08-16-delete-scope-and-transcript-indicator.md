# Delete Scope and Transcript Indicator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deleting a recording removes its entire folder, deleting a transcript removes the exported Markdown, and the "Transcribed" pill reports the export in the transcripts folder rather than the internal `transcript.json`.

**Architecture:** Three independent changes plus one migration. A new pure predicate in `app/utils/transcript_export.py` answers "does an export exist for this recording"; `app/ui/recordings_list.py` uses it for the row badge and switches the recordings-only delete scope to a full `rmtree`; a new `app/utils/transcripts_migration.py` moves pre-migration exports out of the repo-relative folder into the configured one, called once from `main.py`.

**Tech Stack:** Python 3, PyQt6, unittest + pytest runner, pathlib/shutil.

**Spec:** `docs/superpowers/specs/2026-08-16-delete-scope-and-transcript-indicator-design.md`
**Issue:** martinasencio-gm/TalkTrack#73

## Global Constraints

- Run the suite with the venv interpreter: `.venv\Scripts\python.exe -m pytest tests/ -q`. Global `python` has no pytest; never use bare `uv run`.
- Commits go directly to `master`, one per task, no `--amend`, never add `Co-Authored-By` lines. Reference `#73` in each commit message.
- File issues / PRs only against `martinasencio-gm/TalkTrack` (pass `--repo`); `gh` defaults to the wrong remote in this clone.
- Durable user-data writes go through `app/utils/atomic_io.py`. (No new durable writes in this plan — moves and deletes only.)
- Tests use `unittest` classes with `pytest` as the runner; PyQt tests set `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` before importing PyQt6.
- PyQt6 gotcha: never use truthiness on Qt containers — use `is None` / `is not None`.

---

### Task 1: `has_exported_transcript` predicate

**Files:**
- Modify: `app/utils/transcript_export.py` (add after `export_path_for`, around line 96)
- Test: `tests/test_transcript_export.py` (append a new test class)

**Interfaces:**
- Consumes: `export_path_for(directory_name, timestamp_iso, transcripts_dir) -> Path` (already exists in this module).
- Produces: `has_exported_transcript(metadata, transcripts_dir) -> bool` — `metadata` is a recording's metadata dict (uses `"directory"` and `"started_at"`), `transcripts_dir` is a path string or `None`. Task 3 consumes this.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_transcript_export.py`:

```python
class TestHasExportedTranscript(unittest.TestCase):
    """The recordings list uses this to decide whether a recording has an
    export the user could actually open, which is a different question from
    whether transcript.json exists inside the recording's own folder."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()
        self.metadata = {
            "directory": str(Path(self.tmp) / "recordings" / "session1"),
            "started_at": "2026-08-15T10:29:06",
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_true_when_the_export_file_exists(self):
        path = export_path_for("session1", "2026-08-15T10:29:06", str(self.transcripts_dir))
        path.write_text("# exported", encoding="utf-8")
        self.assertTrue(has_exported_transcript(self.metadata, str(self.transcripts_dir)))

    def test_false_when_no_export_was_written(self):
        self.assertFalse(has_exported_transcript(self.metadata, str(self.transcripts_dir)))

    def test_false_when_a_different_recordings_export_exists(self):
        other = export_path_for("session2", "2026-08-15T10:29:06", str(self.transcripts_dir))
        other.write_text("# exported", encoding="utf-8")
        self.assertFalse(has_exported_transcript(self.metadata, str(self.transcripts_dir)))

    def test_false_for_missing_or_empty_transcripts_dir(self):
        self.assertFalse(has_exported_transcript(self.metadata, None))
        self.assertFalse(has_exported_transcript(self.metadata, ""))
        self.assertFalse(has_exported_transcript(self.metadata, str(Path(self.tmp) / "nope")))

    def test_missing_started_at_uses_the_zero_stamp_and_still_matches(self):
        metadata = {"directory": str(Path(self.tmp) / "recordings" / "session1")}
        path = export_path_for("session1", "", str(self.transcripts_dir))
        path.write_text("# exported", encoding="utf-8")
        self.assertTrue(has_exported_transcript(metadata, str(self.transcripts_dir)))
```

Add to that file's imports if not already present: `import shutil`, `import tempfile`, `from pathlib import Path`, and extend the `app.utils.transcript_export` import with `export_path_for, has_exported_transcript`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transcript_export.py -q`
Expected: FAIL — `ImportError: cannot import name 'has_exported_transcript'`

- [ ] **Step 3: Write minimal implementation**

In `app/utils/transcript_export.py`, immediately after `export_path_for`:

```python
def has_exported_transcript(metadata, transcripts_dir):
    """Whether an export exists in the transcripts folder for this recording.

    This is the question the recordings list's "Transcribed" pill answers —
    deliberately not the same as "transcript.json exists in the recording's
    own folder". A zero-segment transcript is never exported (see
    has_exportable_content), and a transcripts-only delete removes the export
    while leaving the recording's directory in place, so the two can honestly
    disagree.

    A missing/empty transcripts_dir means we cannot know, which for a badge
    is the same as "no" — never raise into the row builder.
    """
    if not transcripts_dir:
        return False
    directory_name = Path(metadata.get("directory", "")).name
    path = export_path_for(directory_name, metadata.get("started_at", ""), transcripts_dir)
    return path.exists()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transcript_export.py -q`
Expected: PASS (all tests in the file)

- [ ] **Step 5: Commit**

```bash
git add app/utils/transcript_export.py tests/test_transcript_export.py
git commit -m "feat: add has_exported_transcript predicate (#73)"
```

---

### Task 2: "Recordings only" deletes the whole folder

**Files:**
- Modify: `app/ui/recordings_list.py` — `_perform_delete` (line ~638), delete `_delete_audio_files` (lines 223-242)
- Modify: `app/ui/delete_scope_dialog.py:29-35` (radio label wording)
- Test: `tests/test_recordings_list_delete_scope.py` (replace the `TestDeleteAudioFiles` class and update two scope tests)

**Interfaces:**
- Consumes: `_rmtree_robust(directory)` and `_delete_exported_transcript(metadata, transcripts_dir)`, both already in `recordings_list.py`.
- Produces: no new callables. Behaviour contract for Task 3 and for `main_window`: `DELETE_RECORDINGS` now emits `about_to_delete` then `recording_deleted` (previously `recording_files_changed`).

- [ ] **Step 1: Write the failing test**

In `tests/test_recordings_list_delete_scope.py`:

1. Delete the entire `class TestDeleteAudioFiles` (lines 42-96) — the function under test is going away.
2. Remove `_delete_audio_files` from the `from app.ui.recordings_list import (...)` list.
3. Replace `test_scope_recordings_emits_files_changed_not_deleted` with:

```python
    def test_scope_recordings_removes_the_whole_folder(self):
        """"Recordings only" deletes the recording's directory outright.
        Anything the app dropped in there — embeddings.npz, chat_history.json,
        stray chunk WAVs — goes with it; only the exported Markdown in the
        separate transcripts/ folder survives (covered below)."""
        metadata = self._make_session()
        (self.session_dir / "embeddings.npz").write_text("npz", encoding="utf-8")
        (self.session_dir / "chat_history.json").write_text("[]", encoding="utf-8")
        widget = RecordingsList(self.recordings_dir)
        about_to_delete = []
        files_changed = []
        deleted = []
        widget.about_to_delete.connect(about_to_delete.append)
        widget.recording_files_changed.connect(files_changed.append)
        widget.recording_deleted.connect(deleted.append)

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse(self.session_dir.exists())
        self.assertEqual(about_to_delete, [str(self.session_dir)])
        self.assertEqual(deleted, [str(self.session_dir)])
        self.assertEqual(files_changed, [])
```

4. In `TestPerformDeleteRemovesExportedTranscript`, replace `test_scope_recordings_leaves_the_export_copy_alone` with:

```python
    def test_scope_recordings_leaves_the_export_copy_alone(self):
        """The export is the durable artifact: deleting the recording folder
        is exactly how a user keeps the transcript and reclaims the audio."""
        metadata = self._make_session()
        widget = RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

        widget._perform_delete(metadata, DELETE_RECORDINGS)

        self.assertFalse(self.session_dir.exists())
        self.assertTrue(self.export_path.exists())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recordings_list_delete_scope.py -q`
Expected: FAIL — `test_scope_recordings_removes_the_whole_folder` asserts `session_dir` is gone but the directory still exists (only the WAV was removed), and `deleted` is empty while `files_changed` holds the path.

- [ ] **Step 3: Write minimal implementation**

In `app/ui/recordings_list.py`, delete the whole `_delete_audio_files` function (lines 223-242), then change the `DELETE_RECORDINGS` branch of `_perform_delete`:

```python
            if scope == DELETE_BOTH:
                _rmtree_robust(directory)
                _delete_exported_transcript(metadata, transcripts_dir)
                self.recording_deleted.emit(directory)
            elif scope == DELETE_RECORDINGS:
                # The whole session directory goes, not just the audio files
                # metadata happens to list: embeddings.npz, chat_history.json,
                # calendar_event.json and any stray chunk WAVs live here too,
                # and leaving them behind left "deleted" recordings on disk.
                # The exported Markdown in transcripts/ is what survives — it
                # is the only difference between this scope and DELETE_BOTH.
                _rmtree_robust(directory)
                self.recording_deleted.emit(directory)
            elif scope == DELETE_TRANSCRIPTIONS:
```

Update the `_perform_delete` docstring's second paragraph to say the notification fires before either folder-removing scope.

In `app/ui/delete_scope_dialog.py`, replace the three radio labels:

```python
        self._recordings_radio = QRadioButton(
            "Recording folder — audio and transcript files; "
            "keeps the exported transcript in transcripts/"
        )
        self._transcriptions_radio = QRadioButton(
            "Transcriptions only — transcript/summary/action items; keeps audio"
        )
        self._both_radio = QRadioButton("Everything — including the exported transcript")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recordings_list_delete_scope.py tests/test_delete_scope_dialog.py tests/test_main_window_delete_scope.py -q`
Expected: PASS. (`test_delete_scope_dialog.py` asserts only on `selected_scope()` and window titles, so the wording change is safe.)

- [ ] **Step 5: Commit**

```bash
git add app/ui/recordings_list.py app/ui/delete_scope_dialog.py tests/test_recordings_list_delete_scope.py
git commit -m "feat: recordings-only delete removes the whole session folder (#73)"
```

---

### Task 3: "Transcribed" pill follows the export

**Files:**
- Modify: `app/ui/recordings_list.py` — imports (line 23) and `_build_row_widget` (lines ~454-482)
- Test: `tests/test_recordings_list_badges.py`

**Interfaces:**
- Consumes: `has_exported_transcript(metadata, transcripts_dir)` from Task 1; `self.config.get("transcripts", "directory")` — `self.config` may be `None` on a bare `RecordingsList`.
- Produces: no new callables. `_selected_transcribed` / `_selected_untranscribed` are deliberately NOT changed.

- [ ] **Step 1: Write the failing test**

Rewrite `tests/test_recordings_list_badges.py`'s session helper and transcript cases. Add near the top, after the imports:

```python
class _FakeConfig:
    """(section, key) -> value stub; these tests build a bare RecordingsList
    with no MainWindow, so there is no real Config to reuse."""

    def __init__(self, transcripts_dir):
        self._transcripts_dir = transcripts_dir

    def get(self, section, key):
        assert (section, key) == ("transcripts", "directory")
        return self._transcripts_dir
```

Add `self.transcripts_dir` to `setUp`:

```python
        self.transcripts_dir = Path(self.tmp) / "transcripts"
        self.transcripts_dir.mkdir()
```

Note `self.recordings_dir = Path(self.tmp)` currently makes `transcripts/` a child of the recordings dir; change `setUp` to keep them siblings:

```python
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()
```

Replace `_make_session` with a version whose transcript flag writes the export, and add a widget factory:

```python
    def _widget(self):
        return RecordingsList(self.recordings_dir, config=_FakeConfig(str(self.transcripts_dir)))

    def _make_session(self, name, with_audio, with_transcript, with_export=None):
        """with_transcript writes transcript.json inside the recording folder;
        with_export writes the Markdown export in the transcripts folder and
        defaults to matching with_transcript (the normal, in-sync case)."""
        d = self.recordings_dir / name
        d.mkdir()
        started_at = "2026-08-14T10:00:00"
        audio_files = {}
        if with_audio:
            audio_path = d / "combined_audio.wav"
            audio_path.write_text("wav", encoding="utf-8")
            audio_files["combined"] = str(audio_path)
        if with_transcript:
            (d / "transcript.json").write_text("{}", encoding="utf-8")
        if with_export is None:
            with_export = with_transcript
        if with_export:
            export_path_for(name, started_at, str(self.transcripts_dir)).write_text(
                "# exported", encoding="utf-8"
            )
        metadata = {
            "directory": str(d),
            "name": name,
            "started_at": started_at,
            "duration": 60,
            "audio_files": audio_files,
        }
        (d / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
        return metadata
```

Import `export_path_for`: `from app.utils.transcript_export import export_path_for`.

Change every existing `RecordingsList(self.recordings_dir)` in this file to `self._widget()`, then add the two cases that pin the new meaning:

```python
    def test_no_transcribed_badge_when_transcript_json_has_no_export(self):
        """The pill reports the file the user can open in the transcripts
        folder. transcript.json alone is not enough."""
        widget = self._widget()
        metadata = self._make_session(
            "no_export", with_audio=True, with_transcript=True, with_export=False
        )
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Audio", texts)
        self.assertNotIn("Transcribed", texts)

    def test_transcribed_badge_when_export_survives_a_folder_delete(self):
        """After a "Recordings only" delete the folder is gone but the export
        remains; a row rebuilt from stale metadata must still say Transcribed."""
        widget = self._widget()
        metadata = self._make_session(
            "kept_export", with_audio=False, with_transcript=False, with_export=True
        )
        texts = self._badge_texts(widget, metadata)
        self.assertIn("Transcribed", texts)

    def test_no_transcribed_badge_without_config(self):
        """A RecordingsList with no config cannot resolve the transcripts
        folder — degrade to no badge rather than raising in the row builder."""
        widget = RecordingsList(self.recordings_dir)
        metadata = self._make_session("no_config", with_audio=True, with_transcript=True)
        texts = self._badge_texts(widget, metadata)
        self.assertNotIn("Transcribed", texts)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recordings_list_badges.py -q`
Expected: FAIL — `test_no_transcribed_badge_when_transcript_json_has_no_export` finds "Transcribed" because the badge still reads `transcript.json`.

- [ ] **Step 3: Write minimal implementation**

In `app/ui/recordings_list.py`, extend the import on line 23:

```python
from app.utils.transcript_export import export_path_for, has_exported_transcript
```

In `_build_row_widget`, replace the `has_transcript` line and its comment block:

```python
        # Checked live against disk rather than trusting metadata — a delete
        # can remove audio or transcript without the other, and the row needs
        # to reflect that on the very next refresh() with no cached state.
        #
        # "Transcribed" deliberately reports the Markdown export in the
        # transcripts folder, not transcript.json inside the recording: the
        # export is the artifact the user opens and the one that outlives a
        # "Recording folder" delete. The context-menu Transcribe/Export
        # actions still key off transcript.json (see _selected_transcribed) —
        # they need the file the app can re-export FROM, so a recording with a
        # transcript but no export shows no pill and offers "Export".
        audio_files = metadata.get("audio_files", {}) or {}
        has_audio = any(p and Path(p).exists() for p in audio_files.values())
        transcripts_dir = self.config.get("transcripts", "directory") if self.config else None
        has_transcript = has_exported_transcript(metadata, transcripts_dir)
```

Add the matching note above `_selected_transcribed`:

```python
    def _selected_transcribed(self, items):
        """Selected recordings that have transcript.json on disk.

        Intentionally NOT has_exported_transcript: this drives Export/
        Transcribe, which need the source the export is built from. The row
        badge answers a different question — see _build_row_widget.
        """
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_recordings_list_badges.py tests/test_recordings_list_batch_transcript.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ui/recordings_list.py tests/test_recordings_list_badges.py
git commit -m "feat: Transcribed badge reports the transcripts/ export (#73)"
```

---

### Task 4: One-time import of stranded exports

**Files:**
- Create: `app/utils/transcripts_migration.py`
- Create: `tests/test_transcripts_migration.py`
- Modify: `app/utils/config.py` — add `"legacy_import_done": False` to the `"transcripts"` block (line 30-32)
- Modify: `app/main_window.py:62` — call immediately after `self.config = Config()` (note: `main.py` never builds a `Config`; `MainWindow.__init__` is the only place it is constructed)

**Interfaces:**
- Consumes: `DEFAULT_CONFIG["transcripts"]["directory"]` from `app.utils.config` (the repo-relative legacy path).
- Produces: `import_legacy_exports(legacy_dir, transcripts_dir) -> list[str]` — returns the moved filenames (not full paths), empty list on any no-op.

- [ ] **Step 1: Write the failing test**

Create `tests/test_transcripts_migration.py`:

```python
"""Tests for the one-time move of Markdown exports out of the old
repo-relative transcripts folder into the configured one.

Exports written before the Documents data-dir move (c49d8c6/d8e86fc) landed
in <repo>/transcripts while the app now reads and writes
Documents/talktrack/transcripts, leaving them invisible to the app.
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from app.utils.transcripts_migration import import_legacy_exports


class TestImportLegacyExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.legacy = Path(self.tmp) / "legacy"
        self.legacy.mkdir()
        self.target = Path(self.tmp) / "target"
        self.target.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_moves_markdown_files_into_the_target(self):
        (self.legacy / "a_20260815_1002.md").write_text("# a", encoding="utf-8")
        (self.legacy / "b_20260815_1013.md").write_text("# b", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(sorted(moved), ["a_20260815_1002.md", "b_20260815_1013.md"])
        self.assertTrue((self.target / "a_20260815_1002.md").exists())
        self.assertFalse((self.legacy / "a_20260815_1002.md").exists())

    def test_skips_names_already_present_in_the_target(self):
        """The target copy is the newer one — never overwrite it, and leave
        the legacy file alone so nothing is silently destroyed."""
        (self.legacy / "dup.md").write_text("old", encoding="utf-8")
        (self.target / "dup.md").write_text("new", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(moved, [])
        self.assertEqual((self.target / "dup.md").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.legacy / "dup.md").exists())

    def test_ignores_non_markdown_files(self):
        (self.legacy / "notes.txt").write_text("x", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.target))

        self.assertEqual(moved, [])
        self.assertTrue((self.legacy / "notes.txt").exists())

    def test_same_directory_is_a_noop(self):
        (self.legacy / "a.md").write_text("# a", encoding="utf-8")

        moved = import_legacy_exports(str(self.legacy), str(self.legacy))

        self.assertEqual(moved, [])
        self.assertTrue((self.legacy / "a.md").exists())

    def test_missing_legacy_dir_is_a_noop(self):
        moved = import_legacy_exports(str(Path(self.tmp) / "gone"), str(self.target))
        self.assertEqual(moved, [])

    def test_missing_target_is_created(self):
        (self.legacy / "a.md").write_text("# a", encoding="utf-8")
        target = Path(self.tmp) / "made_here"

        moved = import_legacy_exports(str(self.legacy), str(target))

        self.assertEqual(moved, ["a.md"])
        self.assertTrue((target / "a.md").exists())

    def test_falsy_arguments_are_a_noop(self):
        self.assertEqual(import_legacy_exports("", str(self.target)), [])
        self.assertEqual(import_legacy_exports(str(self.legacy), None), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transcripts_migration.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.transcripts_migration'`

- [ ] **Step 3: Write minimal implementation**

Create `app/utils/transcripts_migration.py`:

```python
"""One-time relocation of Markdown transcript exports.

Exports written before the app data dir moved to Documents (c49d8c6,
d8e86fc) were saved under the repo-relative default transcripts folder.
The app now reads and writes the configured folder, so those files are
invisible to it — present on disk, absent from the recordings list. This
moves them across once. Nothing is deleted and nothing is overwritten.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def import_legacy_exports(legacy_dir, transcripts_dir):
    """Move *.md from legacy_dir into transcripts_dir, returning the names moved.

    Skips any filename already present in the target — that copy is the newer
    one — and leaves the legacy file in place rather than destroying it. A
    missing legacy dir, a falsy path, or both paths resolving to the same
    directory are all no-ops.
    """
    if not legacy_dir or not transcripts_dir:
        return []

    legacy = Path(legacy_dir)
    target = Path(transcripts_dir)
    if not legacy.is_dir():
        return []
    try:
        if legacy.resolve() == target.resolve():
            return []
    except OSError:
        return []

    moved = []
    for path in sorted(legacy.glob("*.md")):
        destination = target / path.name
        if destination.exists():
            logger.info("Legacy export %s already present in target — left in place", path.name)
            continue
        try:
            os.makedirs(target, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(path.name)
        except OSError:
            logger.exception("Failed to move legacy export %s", path)

    if moved:
        logger.info("Imported %d legacy transcript export(s) from %s", len(moved), legacy)
    return moved
```

In `app/utils/config.py`, extend the `transcripts` block:

```python
    "transcripts": {
        "directory": str(Path(__file__).parent.parent.parent / "transcripts"),
        # One-shot flag for transcripts_migration.import_legacy_exports; set
        # once at startup so later launches do no filesystem work.
        "legacy_import_done": False,
    },
```

In `app/main_window.py`, immediately after `self.config = Config()` (line 62), add:

```python
        self._import_legacy_transcript_exports()
```

and add the method to `MainWindow` (place it next to the other startup helpers):

```python
    def _import_legacy_transcript_exports(self):
        """Move exports stranded by the Documents data-dir move, once.

        Before c49d8c6/d8e86fc the transcripts folder defaulted to a
        repo-relative path; exports written then are invisible to the app now
        that it reads the configured folder. The config flag makes every later
        launch free, and is set even when nothing moved.
        """
        if self.config.get("transcripts", "legacy_import_done"):
            return
        from app.utils.config import DEFAULT_CONFIG
        from app.utils.transcripts_migration import import_legacy_exports
        moved = import_legacy_exports(
            DEFAULT_CONFIG["transcripts"]["directory"],
            self.config.get("transcripts", "directory"),
        )
        # Config.set() persists on its own — no separate save() call.
        self.config.set("transcripts", "legacy_import_done", True)
        if moved:
            logger.info("Imported %d legacy transcript export(s)", len(moved))
```

`main_window.py` already defines a module-level `logger`; confirm that before using it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_transcripts_migration.py tests/test_config.py -q`
Expected: PASS

Then smoke-test the wiring:

Run: `.venv\Scripts\python.exe -c "import os; os.environ.setdefault('QT_QPA_PLATFORM','offscreen'); from app.main_window import MainWindow; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add app/utils/transcripts_migration.py tests/test_transcripts_migration.py app/utils/config.py app/main_window.py
git commit -m "feat: one-time import of transcript exports stranded by the data-dir move (#73)"
```

---

### Task 5: Full-suite verification and docs

**Files:**
- Modify: `CLAUDE.md` (Data Files Per Recording / Current Features wording for delete scope)

- [ ] **Step 1: Run the full suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS, no failures. Baseline before this plan was 664 passed, 1 skipped; expect a higher passed count and the same single skip.

- [ ] **Step 2: Launch smoke test**

Run: `start_debug.bat`, then confirm `TalkTrack UI ready` appears in `%USERPROFILE%\Documents\TalkTrack\talktrack.log` (the app data dir — not `~/.talktrack`). In the app: right-click a recording, confirm the three reworded delete options render, cancel the dialog. Confirm the "Transcribed" pill now appears only on recordings with a `.md` in the configured transcripts folder, and that the legacy import moved the stranded files there on this first launch.

- [ ] **Step 3: Update CLAUDE.md**

In the "Current Features" list, replace the multi-select bulk delete bullet's neighbours as needed so the delete scopes are described accurately:

```markdown
- **Delete scopes:** deleting a recording offers three scopes — the recording folder (removes the whole session directory, keeps the Markdown export), transcriptions only (removes transcript/summary/action items plus the Markdown export, keeps audio), or everything
- **Transcribed indicator:** the "Transcribed" pill in the recordings list reflects the Markdown export in the transcripts folder, not `transcript.json` inside the recording; the right-click Transcribe/Export actions still key off `transcript.json`
```

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: describe delete scopes and the export-backed Transcribed pill (#73)"
```

- [ ] **Step 5: Push and close the issue**

```bash
git push origin master
```

```bash
gh issue close 73 --repo martinasencio-gm/TalkTrack --reason completed
```
