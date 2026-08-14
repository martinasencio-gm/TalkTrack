# Phase 2: Multi-Select Transcript Creation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user multi-select recordings in the recordings list and, from the context menu, batch-transcribe the untranscribed ones and/or batch-export the already-transcribed ones to Markdown.

**Architecture:** `RecordingsList` gains two new signals fired from its existing multi-select context menu, carrying pre-filtered metadata lists (untranscribed-only for transcribe, transcribed-only for export). `MainWindow` gains two thin handlers that loop the selection and call its existing single-recording methods (`_start_transcription`, `_export_transcript`) unchanged — no new worker or persistence code.

**Tech Stack:** PyQt6, Python, pytest (offscreen Qt platform for headless test runs).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-phase2-multi-select-transcript-creation-design.md`
- Tests run via `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q` — never bare `uv run`.
- Commits go directly to `master`, conventional prefixes (`ui:`, `main:`), never `Co-Authored-By`, never `--amend`.
- No new background-thread/queue/persistence code — both operations must reuse `_start_transcription` and `_export_transcript` exactly as they already behave for single recordings.
- Out of scope: any visible batch-progress panel; changes to `_export_transcript`/`_start_transcription`/worker classes themselves; Story 1.3 (already complete).

---

### Task 1: RecordingsList multi-select transcribe/export actions

**Files:**
- Modify: `app/ui/recordings_list.py` (signals near line 205-211; `_show_context_menu` multi-select branch, line ~397-417)
- Test: `tests/test_recordings_list_batch_transcript.py` (new)

**Interfaces:**
- Produces: `RecordingsList.transcribe_selected_requested = pyqtSignal(list)` — emits `list[dict]` of metadata for selected recordings whose directory has no `transcript.json`.
- Produces: `RecordingsList.export_selected_requested = pyqtSignal(list)` — emits `list[dict]` of metadata for selected recordings whose directory has `transcript.json`.
- Consumes: existing `RecordingsList.recordings_dir`, `self.list_widget.selectedItems()`, metadata dicts already carrying a `"directory"` key (see `refresh()`, `app/ui/recordings_list.py:262-292`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recordings_list_batch_transcript.py`:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.ui.recordings_list import RecordingsList

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication([])
    return _app


class TestRecordingsListBatchTranscript(unittest.TestCase):
    def setUp(self):
        _get_app()
        self.tmp = tempfile.mkdtemp()
        self.recordings_dir = Path(self.tmp) / "recordings"
        self.recordings_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_recording(self, name, transcribed):
        directory = self.recordings_dir / name
        directory.mkdir()
        metadata = {
            "id": name,
            "directory": str(directory),
            "name": name,
            "started_at": "2026-08-14T10:00:00",
            "duration": 5.0,
            "audio_files": {"combined": str(directory / "combined_audio.wav")},
        }
        with open(directory / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f)
        if transcribed:
            with open(directory / "transcript.json", "w", encoding="utf-8") as f:
                json.dump({"segments": []}, f)
        return metadata

    def _select_all(self, widget):
        widget.list_widget.selectAll()
        return widget.list_widget.selectedItems()

    def test_selected_untranscribed_returns_only_recordings_without_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_untranscribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_a")

    def test_selected_transcribed_returns_only_recordings_with_transcript(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        result = widget._selected_transcribed(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(Path(result[0]["directory"]).name, "rec_b")

    def test_transcribe_selected_requested_emits_untranscribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.transcribe_selected_requested.connect(seen.append)
        untranscribed = widget._selected_untranscribed(items)
        widget.transcribe_selected_requested.emit(untranscribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_a")

    def test_export_selected_requested_emits_transcribed_subset(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        seen = []
        widget.export_selected_requested.connect(seen.append)
        transcribed = widget._selected_transcribed(items)
        widget.export_selected_requested.emit(transcribed)
        self.assertEqual(len(seen), 1)
        self.assertEqual(len(seen[0]), 1)
        self.assertEqual(Path(seen[0][0]["directory"]).name, "rec_b")

    def test_selected_untranscribed_empty_when_all_transcribed(self):
        self._make_recording("rec_a", transcribed=True)
        self._make_recording("rec_b", transcribed=True)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_untranscribed(items), [])

    def test_selected_transcribed_empty_when_none_transcribed(self):
        self._make_recording("rec_a", transcribed=False)
        self._make_recording("rec_b", transcribed=False)
        widget = RecordingsList(self.recordings_dir)
        items = self._select_all(widget)
        self.assertEqual(widget._selected_transcribed(items), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_recordings_list_batch_transcript.py -v`
Expected: FAIL — `AttributeError: 'RecordingsList' object has no attribute '_selected_untranscribed'` (and the two new signals don't exist yet either).

- [ ] **Step 3: Implement the signals, helpers, and menu actions**

In `app/ui/recordings_list.py`, add the two new signals next to the existing ones (around line 205-211):

```python
    recording_selected = pyqtSignal(dict)  # metadata dict
    about_to_delete = pyqtSignal(str)      # directory path — emitted BEFORE rmtree
                                           # so main_window can release caches and
                                           # stop playback on the target files
    recording_deleted = pyqtSignal(str)    # directory path of deleted recording
    search_result_selected = pyqtSignal(str, float)  # recording_id, timestamp
    import_requested = pyqtSignal(str)  # chosen audio file path
    transcribe_selected_requested = pyqtSignal(list)  # list[dict] metadata, untranscribed only
    export_selected_requested = pyqtSignal(list)      # list[dict] metadata, transcribed only
```

Add two helper methods (near `_open_folder`/`_open_transcripts_folder`, around line 449-457):

```python
    def _selected_untranscribed(self, items):
        result = []
        for item in items:
            metadata = item.data(Qt.ItemDataRole.UserRole)
            if not metadata or "directory" not in metadata:
                continue
            if not (Path(metadata["directory"]) / "transcript.json").exists():
                result.append(metadata)
        return result

    def _selected_transcribed(self, items):
        result = []
        for item in items:
            metadata = item.data(Qt.ItemDataRole.UserRole)
            if not metadata or "directory" not in metadata:
                continue
            if (Path(metadata["directory"]) / "transcript.json").exists():
                result.append(metadata)
        return result
```

Edit the multi-select branch of `_show_context_menu` (currently lines 397-417) to add the two new actions between the folder actions and the separator+delete:

```python
        if len(selected_items) > 1:
            # Multi-select context menu
            open_recordings = QAction("Open Recordings Folder", self)
            open_recordings.triggered.connect(
                lambda: self._open_folder(str(self.recordings_dir))
            )
            menu.addAction(open_recordings)

            open_transcripts = QAction("Open Transcripts Folder", self)
            open_transcripts.triggered.connect(self._open_transcripts_folder)
            open_transcripts.setEnabled(self.config is not None)
            menu.addAction(open_transcripts)

            menu.addSeparator()

            untranscribed = self._selected_untranscribed(selected_items)
            transcribe_action = QAction(f"Transcribe {len(untranscribed)} Recordings", self)
            transcribe_action.setEnabled(len(untranscribed) > 0)
            transcribe_action.triggered.connect(
                lambda: self.transcribe_selected_requested.emit(untranscribed)
            )
            menu.addAction(transcribe_action)

            transcribed = self._selected_transcribed(selected_items)
            export_action = QAction(f"Export {len(transcribed)} Transcripts", self)
            export_action.setEnabled(len(transcribed) > 0)
            export_action.triggered.connect(
                lambda: self.export_selected_requested.emit(transcribed)
            )
            menu.addAction(export_action)

            menu.addSeparator()

            count = len(selected_items)
            delete_action = QAction(f"Delete {count} Recordings", self)
            delete_action.triggered.connect(
                lambda: self._delete_selected_recordings(selected_items)
            )
            menu.addAction(delete_action)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_recordings_list_batch_transcript.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ui/recordings_list.py tests/test_recordings_list_batch_transcript.py
git commit -m "ui: add multi-select transcribe/export actions to recordings list"
```

---

### Task 2: MainWindow batch transcribe/export handlers

**Files:**
- Modify: `app/main_window.py` (wiring near other `self.recordings_list.*.connect(...)` calls in `_setup_ui`; new handlers near `_start_transcription`/`_export_transcript`)
- Test: `tests/test_main_window_batch_transcript.py` (new)

**Interfaces:**
- Consumes: `RecordingsList.transcribe_selected_requested` / `export_selected_requested` (Task 1), `MainWindow._start_transcription(audio_path, session=None)` (`app/main_window.py:963`), `MainWindow._export_transcript(session=None)` (`app/main_window.py:1149`), `self.status_label.setText(str)`.
- Produces: `MainWindow._on_transcribe_selected(self, recordings: list[dict])`, `MainWindow._on_export_selected(self, recordings: list[dict])`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_main_window_batch_transcript.py`. This test instantiates a real `MainWindow` (it's the simplest way to exercise `_setup_ui`'s wiring) but replaces `_start_transcription`/`_export_transcript` with mocks before invoking the handlers directly, so no real transcription/export work runs:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestMainWindowBatchTranscript(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()
        self.addCleanup(window.close)
        return window

    def test_on_transcribe_selected_calls_start_transcription_per_recording_with_audio(self):
        window = self._make_window()
        recordings = [
            {"directory": "/r1", "audio_files": {"combined": "/r1/combined_audio.wav"}},
            {"directory": "/r2", "audio_files": {"mic": "/r2/mic_audio.wav"}},
        ]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        self.assertEqual(mock_start.call_count, 2)
        mock_start.assert_any_call("/r1/combined_audio.wav", session=recordings[0])
        mock_start.assert_any_call("/r2/mic_audio.wav", session=recordings[1])

    def test_on_transcribe_selected_skips_recordings_with_no_audio_files(self):
        window = self._make_window()
        recordings = [{"directory": "/r1", "audio_files": {}}]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        mock_start.assert_not_called()

    def test_on_transcribe_selected_prefers_combined_over_system_over_mic(self):
        window = self._make_window()
        recordings = [{
            "directory": "/r1",
            "audio_files": {
                "mic": "/r1/mic_audio.wav",
                "system": "/r1/system_audio.wav",
                "combined": "/r1/combined_audio.wav",
            },
        }]
        with patch.object(window, "_start_transcription") as mock_start:
            window._on_transcribe_selected(recordings)
        mock_start.assert_called_once_with("/r1/combined_audio.wav", session=recordings[0])

    def test_on_export_selected_calls_export_transcript_per_recording(self):
        window = self._make_window()
        recordings = [{"directory": "/r1"}, {"directory": "/r2"}]
        with patch.object(window, "_export_transcript") as mock_export:
            window._on_export_selected(recordings)
        self.assertEqual(mock_export.call_count, 2)
        mock_export.assert_any_call(recordings[0])
        mock_export.assert_any_call(recordings[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_batch_transcript.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute '_on_transcribe_selected'`

- [ ] **Step 3: Implement the handlers and wiring**

In `app/main_window.py`, find where `self.recordings_list` signals are connected in `_setup_ui` (same area as the `recording_selected`/`recording_deleted`/`import_requested` connections) and add:

```python
        self.recordings_list.transcribe_selected_requested.connect(self._on_transcribe_selected)
        self.recordings_list.export_selected_requested.connect(self._on_export_selected)
```

Add the two handlers near `_start_transcription` (after `_cancel_transcription`/`_process_pending_transcriptions`, around line 1015):

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

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_main_window_batch_transcript.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass (500 total: 490 pre-existing + 6 from Task 1 + 4 from Task 2).

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_main_window_batch_transcript.py
git commit -m "main: wire multi-select batch transcribe and export handlers"
```

---

## Manual Verification (after both tasks)

1. Run the app: `.venv/Scripts/python.exe -m app.main` (or however it's normally launched).
2. In the recordings list, select 2+ recordings where at least one lacks a transcript and at least one already has one (right-click → check counts shown match selection).
3. Click "Transcribe N Recordings" — confirm the status bar shows "Queued N recording(s) for transcription," transcription runs serially, and each recording's "Transcribed" badge appears once done (list auto-refreshes via existing `_display_final_transcript` background path).
4. Click "Export N Transcripts" — confirm the status bar shows "Exported N transcript(s)" and Markdown files appear in the transcripts folder (File → Open Transcripts Folder) for each selected already-transcribed recording.
5. Select recordings that are ALL untranscribed — confirm "Export N Transcripts" is absent/disabled (N=0) and doesn't emit.
6. Select recordings that are ALL already transcribed — confirm "Transcribe N Recordings" is absent/disabled (N=0) and doesn't emit.

## Self-Review

**1. Spec coverage:** Multi-select menu actions with correct enable/counts (Task 1) ✓. Signal payload filtering (untranscribed/transcribed subsets) (Task 1) ✓. MainWindow wiring reusing `_start_transcription`/`_export_transcript` unchanged (Task 2) ✓. Skip-missing-audio behavior (Task 2, test 2) ✓. Audio path preference order combined > system > mic (Task 2, test 3) ✓. Status label messaging (Task 2) ✓. Testing section from spec covered by both tasks' test files ✓. Out-of-scope items (progress panel, pipeline changes, Story 1.3) — untouched by this plan ✓.

**2. Placeholder scan:** No TBD/TODO; every step has runnable code.

**3. Type consistency:** `list[dict]` metadata used consistently across `RecordingsList` signals (Task 1) and `MainWindow` handler signatures (Task 2). Method names (`_selected_untranscribed`, `_selected_transcribed`, `_on_transcribe_selected`, `_on_export_selected`) match between where they're defined and where they're used/tested.
