# Phase 3: Recording Stability During Transcription Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lower the OS thread priority of the transcription/diarization worker threads so audio capture doesn't lose CPU time to them, closing Story 2.2 and Story 4.1 without any new queue or process-isolation code.

**Architecture:** One-line change at each of the three existing `QThread.start()` call sites in `app/main_window.py`, passing `QThread.Priority.LowPriority`. Everything else (serial job queue via `_pending_transcriptions`, non-blocking recording start) already exists and needs no code change — only a manual verification step confirming it holds under concurrent load.

**Tech Stack:** PyQt6 (`QThread.Priority`), pytest (offscreen Qt platform for headless test runs).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-14-phase3-recording-stability-during-transcription-design.md`
- Tests run via `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q` — never bare `uv run`.
- Commits go directly to `master`, conventional prefixes (`main:`), never `Co-Authored-By`, never `--amend`.
- No new job-queue code — `_pending_transcriptions` (`app/main_window.py:963-1015`) already serializes transcription jobs.
- No process-isolation change — thread-priority tuning only, per spec's explicit rejection of that alternative.
- Use `QThread.Priority.LowPriority` (not `LowestPriority`/`IdlePriority`) on all three worker starts.

---

### Task 1: Lower priority on transcription/diarization worker threads

**Files:**
- Modify: `app/main_window.py` — three call sites: `_start_transcription` (`:982-994`), `_start_diarization` (`:1083-1094`), `_start_simple_diarization` (`:1043-1051`)
- Test: `tests/test_worker_thread_priority.py` (new)

**Interfaces:**
- Consumes: `MainWindow._transcription_worker`, `MainWindow._diarization_worker`,
  `MainWindow._simple_diarize_worker` (all already exist — `TranscriptionWorker`,
  `DiarizationWorker`, `SimpleDiarizeWorker` respectively, all `QThread` subclasses).
- Produces: no new public interface — the three worker `.start()` calls now pass
  `QThread.Priority.LowPriority` explicitly instead of relying on the default
  `QThread.Priority.InheritPriority`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_worker_thread_priority.py`. This mocks each worker's `.start` method (so no
real thread/model-loading work runs) and asserts the priority argument passed by each `_start_*`
method:

```python
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QApplication

_app = None


def _get_app():
    global _app
    if _app is None:
        _app = QApplication.instance() or QApplication(sys.argv)
    return _app


class TestWorkerThreadPriority(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _get_app()

    def _make_window(self):
        from app.main_window import MainWindow
        window = MainWindow()

        def _close():
            window._really_quit = True
            window.close()
        self.addCleanup(_close)
        return window

    def test_start_transcription_uses_low_priority(self):
        window = self._make_window()
        with patch("app.main_window.TranscriptionWorker") as MockWorker:
            mock_instance = MockWorker.return_value
            window._start_transcription("/some/audio.wav", session={"directory": "/r1"})
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)

    def test_start_diarization_uses_low_priority(self):
        window = self._make_window()
        session = {"directory": "/r1", "audio_files": {"combined": "/r1/combined_audio.wav"}}
        transcript_result = MagicMock()
        with patch("app.main_window.DiarizationWorker") as MockWorker, \
             patch.object(window, "config") as mock_config:
            mock_config.get.side_effect = lambda *keys: {
                ("diarization", "hf_token"): "fake-token",
                ("diarization", "min_speakers"): None,
                ("diarization", "max_speakers"): None,
            }.get(keys, None)
            mock_instance = MockWorker.return_value
            window._start_diarization(transcript_result, session)
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)

    def test_start_simple_diarization_uses_low_priority(self):
        window = self._make_window()
        transcript_result = MagicMock()
        with patch("app.main_window.SimpleDiarizeWorker") as MockWorker:
            mock_instance = MockWorker.return_value
            window._start_simple_diarization(
                transcript_result, {"directory": "/r1"}, "/r1/mic_audio.wav", "/r1/system_audio.wav"
            )
        mock_instance.start.assert_called_once_with(QThread.Priority.LowPriority)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_worker_thread_priority.py -v`
Expected: FAIL — each assertion fails because the real code currently calls
`mock_instance.start()` with no arguments (`start.assert_called_once_with(QThread.Priority.LowPriority)`
raises `AssertionError: Expected call: start(<PriorityType.LowPriority>) Actual call: start()`).

- [ ] **Step 3: Implement the priority changes**

In `app/main_window.py`, `_start_transcription` (around line 994), change:

```python
        self._transcription_worker.start()
```

to:

```python
        self._transcription_worker.start(QThread.Priority.LowPriority)
```

In `_start_diarization` (around line 1094), change:

```python
        self._diarization_worker.start()
```

to:

```python
        self._diarization_worker.start(QThread.Priority.LowPriority)
```

In `_start_simple_diarization` (around line 1050), change:

```python
        self._simple_diarize_worker.start()
```

to:

```python
        self._simple_diarize_worker.start(QThread.Priority.LowPriority)
```

`QThread` is already imported in `app/main_window.py` (used for type references elsewhere in the
file) — confirm the import exists near the top of the file; if it is only imported as part of a
`from PyQt6.QtCore import ...` line that doesn't include `QThread`, add it to that import line.

- [ ] **Step 4: Run tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/test_worker_thread_priority.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full test suite**

Run: `QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add app/main_window.py tests/test_worker_thread_priority.py
git commit -m "main: lower thread priority on transcription/diarization workers"
```

---

## Manual Verification (after Task 1 — requires real audio hardware, cannot be automated)

1. Launch the app normally (not under `QT_QPA_PLATFORM=offscreen`).
2. Start a recording (any audio source).
3. While recording, trigger a transcription job on a different, already-transcribed-or-not
   past recording — right-click it in the recordings list → "View / Transcribe", or use the
   Phase 2 "Transcribe N Recordings" batch action on a multi-selection.
4. Let both run concurrently for at least 30 seconds. Confirm:
   - No UI freeze during either operation (window remains responsive to clicks/resizing).
   - No `capture_lost` signal fired and no entries in `pid_lost` (check via the app's own
     status/error surfacing, or add a temporary print if needed for this one-time check).
   - Recording controls show live audio levels updating throughout (waveform/level meters).
5. Stop the recording. Confirm the resulting WAV plays back with no audible gaps or glitches.

## Self-Review

**1. Spec coverage:** Priority tuning at all three call sites (Task 1, Steps 3-4) ✓. "No new
job-queue code" — plan makes zero changes to `_pending_transcriptions` ✓. "No process-isolation
change" — plan contains no such code ✓. Testing section's unit-test requirement (assert
`LowPriority` passed at each call site) ✓. Testing section's manual-verification requirement ✓
(Manual Verification section, word-for-word from the spec's steps). Out-of-scope items (no UI
change, no CPU/memory telemetry, no process isolation) — plan doesn't add any ✓.

**2. Placeholder scan:** No TBD/TODO; every step has runnable code including the exact `.get`
side-effect stub needed for `_start_diarization`'s config reads.

**3. Type consistency:** All three test methods use `QThread.Priority.LowPriority` — matches the
spec's pinned choice exactly, and matches the implementation Step 3 changes verbatim. Method
names (`_start_transcription`, `_start_diarization`, `_start_simple_diarization`) match their
existing signatures in `app/main_window.py` as read during design (confirmed via the spec's
file:line references).
