# Transcription Pipeline: worker lifecycle, session binding, queueing, model caches

Invariants from issues #19–#21, #27–#28. Violating the first two reproduces cross-recording data corruption or crash-on-exit.

## Session binding — never read `_current_session` in completion handlers

Every pipeline worker (`TranscriptionWorker`, `DiarizationWorker`, `SimpleDiarizeWorker`) carries a `.session` attribute bound at creation. Completion/error handlers read the worker's session, not `self._current_session` — the user may have selected a different recording while the worker ran. Results whose session is no longer displayed are persisted via `_write_transcript_for_session` (own directory, own speaker names) without touching the UI, and auto-summarize is skipped for them (the summarize path reads notes/instruction from the *displayed* panels).

## Serial job queue

- `_start_transcription(audio_path, session=None)` queues `(audio_path, session)` in `_pending_transcriptions` when busy; never silently drops.
- `_transcription_busy()` covers ALL THREE workers (transcription, pyannote diarization, simple diarization) — one job (transcribe → diarize → display/save) runs at a time.
- Every terminal path (display, transcription error, cancel, diarization error) must call `_process_pending_transcriptions()` or the queue stalls.

## Shutdown

- `closeEvent` sets `self._closing = True` BEFORE `recorder.stop_recording()` — the synchronous `recording_finished` signal would otherwise spawn a fresh worker mid-exit. `_start_transcription` early-returns when closing.
- `_shutdown_workers()` handles all QThread workers (plus chat + search workers via `active_worker()` accessors): cooperative cancel where supported, `wait(5000)`, `terminate()` last resort. New background workers must be added to its list.

## Diarization error paths show the transcript

Diarization (full or simple) failing must still render/persist the successful transcript (`worker.transcript_result`). A silent drop here was the original #14 bug.

## CPU thread caps (both stages)

Both heavy stages take a `full_cpu` flag, set by MainWindow to `recorder.state == RecordingState.IDLE`:

- `DiarizationWorker` sets `torch.set_num_threads` before calling the pyannote pipeline.
- `TranscriptionWorker` passes `cpu_threads` to `WhisperModel` (CTranslate2's own pool — `torch.set_num_threads` does nothing for it).

Same two values in both: `cpu_count - 1` (min 1) when idle, `cpu_count // 2` (min 1) while a recording is in progress. Half the cores exists to protect the real-time capture callback; with nothing recording there is no callback to protect and the cap roughly doubled run time for free. Never uncap to the literal full core count even when idle — the recorder being idle doesn't mean the UI is: switching recordings parses JSON and rebuilds the transcript widget synchronously, and that stalls visibly if a thread pool has saturated every core.

## Diarization is per-run, not just a setting

- The transcript header's "Identify speakers" checkbox (`TranscriptViewer.diarize_cb`) is the live source of truth for a new job: `_start_transcription` reads `transcript_viewer.diarization_enabled()`, not `config["diarization"]["enabled"]`. Toggling it writes the config key too, so Settings and the checkbox stay one setting with two surfaces (`_sync_diarization_controls` pushes config → checkbox on startup and after Settings closes).
- The choice is bound onto the worker (`worker.diarize`) for the same reason `.session` is — the user can toggle it again while the job runs. `_on_transcription_finished` reads the worker's copy.
- No HF token → the checkbox is disabled and `diarization_enabled()` returns False regardless of its checked state. A saved `enabled=True` from a machine that had a token must not queue a job pyannote cannot run.
- On-demand: `diarize_btn` ("Identify Speakers") appears only with a token + a loaded transcript + audio, and re-runs diarization over the transcript already on screen (`_on_diarize_requested` → `_start_diarization`), so a fast unlabelled pass can be upgraded without transcribing again. It defers rather than stacking when `_transcription_busy()`.

## Model caches — resident, but bounded to one Whisper model

- `transcriber._MODEL_CACHE` — WhisperModel keyed `(model_size, device, compute_type, cpu_threads)`. `cpu_threads` is fixed at construction, so it belongs in the key: without it the first model built during a recording would serve every later idle job at half speed.
- **`_MODEL_CACHE_MAXSIZE` is 1 — do not remove the eviction.** The key spans model_size *and* cpu_threads, so an unbounded dict retained a separate multi-GB model for every size ever selected and for each of the two thread counts. Measured: base+small+medium x2 ≈ 4GB private, against a 581MB working set — the app had been paged almost entirely to disk and every click faulted back off it. Eviction happens *before* constructing the replacement, so peak residency is one model, not two; an in-flight job holds its own reference and is unaffected.
- The trade is deliberate: repeated jobs at the same settings still stay warm (the case worth protecting), while switching model size — or moving between a recording-era job and an idle one — pays one reload.
- `diarizer._PIPELINE_CACHE` — pyannote Pipeline keyed by HF token. Left unbounded: one token in practice, ~32MB.
- `provider.get_sentence_transformer()` — shared embed model cache.
Loading costs seconds-to-tens-of-seconds per recording, so keeping *the model in use* resident between recordings is intentional. Keeping a collection of every model ever loaded is not — that was the bug.

## Long-recording memory and the O(N x M) merge

Both fixed after a 3h job left the app at 4.7GB private / 581MB working set:

- `DiarizationWorker._run_pyannote` decodes the entire recording into one in-memory waveform (pyannote 4.0 wants `{"waveform", "sample_rate"}`, and preloading avoids the torchcodec dep). It must `del` the waveform/audio arrays as soon as speaker turns are extracted — before the merge — or a multi-GB allocation survives the rest of the job. Refcounting is sufficient (arrays and tensors, not cycles). **Do not add `gc.collect()` to either this path or `_get_model`'s eviction:** both run on worker threads, where a global collection can finalize QObjects owned by the GUI thread.
- `SimpleDiarizer.diarize` reads both full tracks with `dtype="float32"`. soundfile's default is float64, which doubled the footprint of both tracks for RMS math that never needed the precision.
- `_merge_diarization_with_transcript` uses a bounded backward search (turns sorted by start, plus a running `reach` maximum of end times), NOT a nested scan over every turn per segment. The nested version was O(segments x turns): 20k x 20k measured at 97.7s versus <1s, i.e. a multi-second stall at the tail of every long meeting. Ties keep the earliest-starting speaker, matching the old input-order behaviour. The prune assumes turns are short relative to the recording, which is what pyannote produces.

## Per-track transcription (the default "simple" path)

- `track_merge.dual_track_plan(session, diarization_enabled, hf_token)` decides: both `mic`/`system` files present on disk AND pyannote not going to run → `[("You", mic), ("Remote", system)]`, passed to `TranscriptionWorker(tracks=...)`. Otherwise None and the mix is transcribed as before.
- Pyannote must keep getting the **mixed** audio — it clusters voices across the whole file. Never split the tracks for it.
- `_on_transcription_finished` checks `worker.tracks` FIRST and goes straight to `_display_final_transcript`: the segments are already labelled, so `SimpleDiarizeWorker` must not run on top of them.
- `merge_tracks` only ever drops segments from the **first** track. Mic goes first because it's the only track that can hold a bleed copy — a loopback of the render stream cannot pick up the user's voice. Reversing the order would delete real remote speech.
- **Bleed is a whole-recording judgement, never per-segment** (`track_merge.bleed_detected`). A single matching utterance is not evidence: two people saying "sounds good to me" over each other is identical to an echo at the segment level, and the old per-segment rule silently deleted the user's own words from headphone recordings that had no bleed at all. Dropping requires `BLEED_MIN_MATCHES` (3) matches *and* `BLEED_MIN_REMOTE_FRACTION` (20%) of the remote track — real bleed copies most of the other side, coincidences happen once or twice. Consequence: a very short recording with genuine bleed keeps its duplicates. That's the intended trade (the module has always held that keeping a duplicate beats deleting real speech), so don't "fix" it by dropping the minimum count.
- A track that fails to transcribe is logged and skipped, not fatal — a recording with only one usable track still produces a transcript.
- `worker.bleed_dropped` counts the removed duplicates; `MainWindow._warn_speaker_bleed` surfaces it once per app session above `BLEED_WARNING_SEGMENTS` and suggests headphones. Tray-hidden suppresses the modal (status label only), same as the silent-capture warning.

## SimpleDiarizer

Now reached only when `dual_track_plan` declines but metadata still names both tracks — in practice a track file that's named but missing from disk. Its error path already renders the transcript, so that lands as an unlabelled transcript rather than a failure.

- Runs off-thread via `SimpleDiarizeWorker` (reads both full WAVs — froze the UI inline).
- Indexes each track with its OWN sample rate (`mic_sr` / `sys_sr`) — mic and system tracks can legitimately differ; a single shared rate made You/Remote labels random (#28).

## Segment metadata

- `TranscriptSegment.confidence` = `exp(segment.avg_logprob)` — populated since #29.
- `word_timestamps` is deliberately NOT requested (unused output, real alignment cost). If a feature needs word timing, re-enable it and actually consume `segment.words`.

## Headless batch runs (`batch_transcribe.py`, `app/batch/`)

The scheduled batch CLI reuses the app's workers **verbatim** rather than
reimplementing the pipeline. Keep it that way — a second implementation
drifts, and `transcript.md` is consumed by tooling that shouldn't have to
cope with two dialects.

- `app/batch/pipeline.py` creates the same `TranscriptionWorker` /
  `DiarizationWorker` / `SimpleDiarizeWorker` and calls **`run()` directly**,
  not `start()`. `run()` is an ordinary method: called inline it executes on
  the calling thread, and signals emitted back to that thread are direct
  connections that fire before `run()` returns. No event loop is started.
  `runner.main()` creates a `QCoreApplication` (never `QApplication` — no
  widgets, no display in a scheduled run) for the workers to live in.
- **`run_job`'s branch structure mirrors `MainWindow._on_transcription_finished`
  exactly** — per-track first, then pyannote, then the SimpleDiarizer
  fallback. Change one and the other has to change with it.
- `full_cpu=True` unconditionally: nothing is capturing audio in a batch
  run, so there is no real-time callback to leave headroom for.
- Writers go through `app/utils/session_io.py`, which is the extracted
  (Qt-free) form of what were `MainWindow._write_transcript_for_session` /
  `_export_transcript` / `_load_calendar_event`. MainWindow now delegates to
  it. Add new session-file I/O there, not back on MainWindow.
- The queue tag is `batch_pending` / `batch_attempts` in the recording's own
  `metadata.json` (`app/utils/batch_queue.py`), not a list in settings.json —
  it travels with the folder. Three failures retires a recording until it is
  queued again.
- **No coordination with a running GUI** (deliberate, confirmed). Both can
  run at once; each loads its own Whisper model.
- Diarization failing must still save the transcript, same invariant as the
  GUI. The batch runner records it as a warning on a successful outcome.
- **Never log `diarization.hf_token` or `ai.api_key`.** The runner logs the
  settings *path* only. `tests/` do not cover this — the rule is the guard.

## Diarization progress percent

- `DiarizationWorker.progress_percent` mirrors `TranscriptionWorker.progress_percent` — same signal shape, wired the same way (`.connect(transcript_viewer.set_progress_percent)` + `.connect(_on_transcription_percent)` in `MainWindow._start_diarization`). Before this it only had text status (`progress`), so a long diarization looked hung.
- Driven by `DiarizationWorker._pipeline_hook`, passed as `hook=` to the pyannote `pipeline(...)` call. pyannote invokes it per major step; the two chunked stages (`"segmentation"`, `"embeddings"`) call it repeatedly with real `total`/`completed` because each scans the whole recording, everything else (`"speaker_counting"`, `"discrete_diarization"`) fires once with neither.
- The 50/50 split between the two chunked stages (`_SEGMENTATION_SPAN`, `_EMBEDDING_SPAN`) is an approximation, not a measured cost ratio — it's the only structure pyannote's hook contract exposes. Don't read it as more precise than that.
- `_pipeline_hook` clamps to 99 and returns early on unknown/marker step names; 100 is emitted explicitly right before `finished`, once the diarization-to-transcript merge is done — matching how `TranscriptionWorker` only reaches 100 on `progress_percent.emit(100)` at actual completion, not from the hook itself.
