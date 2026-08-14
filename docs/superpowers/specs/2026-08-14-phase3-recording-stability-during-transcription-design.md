# Phase 3: Recording Stability During Transcription — Design

Covers Story 2.2 (Recording During Transcript Processing) and Story 4.1 (Background Task
Optimization), combined per the original backlog's own phasing ("Phase 3 (High Impact)") since
both describe the same underlying requirement: recording must stay reliable while transcription
and diarization run concurrently.

## Background

Reading the existing architecture before proposing changes:

- Audio capture (`app/recording/audio_capture.py`, `DualAudioCapture`) runs via `sd.InputStream`
  — PortAudio's native callback thread. The Python-level callback (`_audio_callback`) still needs
  the GIL to run, so it competes with any other Python code holding the GIL for CPU time.
- Transcription (`TranscriptionWorker`, `app/transcription/transcriber.py:165`), full diarization
  (`DiarizationWorker`, `app/transcription/diarizer.py:31`), and lightweight channel-based
  diarization (`SimpleDiarizeWorker`, `app/transcription/diarizer.py:205`) already run on
  `QThread` subclasses — never on the GUI thread.
- `MainWindow._pending_transcriptions` (`app/main_window.py:963-1015`) already serializes
  transcription jobs: `_start_transcription` queues instead of starting a second worker whenever
  `_transcription_busy()` is true, and `_process_pending_transcriptions()` drains the queue on
  every terminal path (success, error, cancel). This already satisfies Story 4.1's "job queue for
  transcript generation" requirement — no new queue is needed.
- `MainWindow._start_recording` (`app/main_window.py:422`) does not check transcription state at
  all before starting — nothing currently blocks starting a recording while a transcription job
  is in flight. Story 2.2's "user can start a recording while transcript generation is active" is
  already true today.

**The actual gap:** none of the three worker classes set an explicit thread priority. All run at
Qt's default (`QThread.Priority.InheritPriority`), so under CPU load (e.g. Whisper `base`/`small`
inference, or pyannote diarization) they compete equally with the audio callback for CPU time —
exactly the contention Story 4.1 calls "resource contention" and warns could cause "recording
corruption" (dropped audio frames).

## Fix

**1. Lower priority on the three worker classes.** Each already calls `self.start()` from
`MainWindow` (`_start_transcription`, `_start_diarization`, `_start_simple_diarization`). Change
each call site to `worker.start(QThread.Priority.LowPriority)` — `QThread.start()` takes an
optional priority argument directly; no change needed inside the worker classes themselves.

Call sites, all in `app/main_window.py`:
- `_start_transcription` (`:982-994`): `self._transcription_worker.start(QThread.Priority.LowPriority)`
- `_start_diarization` (`:1083-1094`): `self._diarization_worker.start(QThread.Priority.LowPriority)`
- `_start_simple_diarization` (`:1043-1051`): `self._simple_diarize_worker.start(QThread.Priority.LowPriority)`

`LowPriority` (rather than `LowestPriority`/`IdlePriority`) keeps transcription/diarization
making steady progress — it's a "yield to audio capture when both want the CPU" hint to the OS
scheduler, not a starvation risk for the transcription job itself.

**2. No new job-queue code.** The existing `_pending_transcriptions` list-based queue already
serializes transcript generation exactly as Story 4.1 requires ("Add job queue for transcript
generation"). This design explicitly does not add a second, parallel-capable queue — that would
increase resource contention, not reduce it, and nothing in either story asks for concurrent
transcription jobs.

**3. No process-isolation change.** Moving transcription to a separate OS process (rather than a
thread) was considered and rejected: it would need IPC for progress/result marshaling and
duplicate model loading overhead, for a problem thread-priority tuning already addresses. If
priority tuning proves insufficient during manual verification (see below), that's the fallback
to revisit — not the first move.

## Error handling

No new error paths. `QThread.start(priority)` cannot fail in a way `start()` doesn't already
handle; the existing `error`/`finished`/`cancelled` signal handling on all three workers is
unchanged.

## Testing

- Unit tests asserting each of the three `.start(...)` call sites passes
  `QThread.Priority.LowPriority` — mock the worker class, call the `_start_*` method, assert
  `start.assert_called_once_with(QThread.Priority.LowPriority)`.
- Manual verification (documented, not automated — requires real audio hardware and a real model
  load, which the existing test suite deliberately avoids per its offscreen/headless setup):
  1. Start a recording.
  2. While recording, trigger a transcription job on a different, already-recorded file (e.g.
     right-click a past recording → View/Transcribe, or use the new batch-transcribe action from
     Phase 2).
  3. Let both run concurrently for at least 30 seconds.
  4. Stop the recording. Confirm: no UI freeze during either operation, the recorder's
     `capture_status`/`pid_lost`/`capture_lost` signals show no failures, and the resulting WAV
     plays back with no audible gaps/glitches.

## Out of scope

- Any UI change (no new indicators, no new settings). Priority tuning is invisible to the user
  except through more reliable recordings.
- CPU/memory monitoring or telemetry — Story 4.1's "CPU/memory utilization remains stable" is
  validated by the manual verification step above, not instrumented in-app.
- Process-level isolation (see "No process-isolation change" above) unless manual verification
  shows thread-priority tuning is insufficient.

## Self-review

- No placeholders — every fix is a one-line change at a named call site with the exact API
  (`QThread.start(priority)`), and the existing architecture pieces it relies on are named with
  file:line references.
- Internal consistency: "no new job-queue code" and the Testing section's manual-verification
  step don't contradict — the existing single-item queue is exercised as-is, not replaced.
- Scope: single cohesive fix (thread priority) across three call sites, one plan.
- Ambiguity: `LowPriority` vs `LowestPriority`/`IdlePriority` is pinned explicitly with reasoning.
