# Batch Summarization & Transcript-Header Summarize — Design

**Status:** approved 2026-08-27
**Branch:** `feature/ui-redesign`

Three related changes, bundled because they share the AI-summary and batch-queue
surfaces:

1. A **"Summarize" checkbox** in the transcript header, next to "Identify
   speakers", gating whether summarization auto-runs after the next
   transcription.
2. Rename the single-recording context-menu action **"Delete Recording" →
   "Delete"** (the delete dialog now offers scopes beyond the recording).
3. **Batch Summarization** — the headless batch CLI and the in-app batch runner
   gain speaker-recognition and summarization stages, selectable per recording
   from a redesigned context sub-menu.

---

## Global Constraints

- **Never log or print `ai.api_key`, `ai.provider_settings`, or
  `diarization.hf_token`.** The batch runner logs the settings *file path*
  only, never its contents. The AI API key MAY be *used* in a batch run
  (passed to `create_provider`); it must never reach a log line, `--dry-run`
  output, or an exception message the runner prints.
- Tests use `unittest` + the `pytest` runner. Run with the venv interpreter:
  `.venv\Scripts\python.exe -m pytest tests/ -q`. Never bare `uv run`.
- Non-UI logic is TDD (failing test first). PyQt code is smoke-tested
  (`python -c "from app.x import Y"`) plus pure-helper unit tests; no Qt
  widget tests beyond pure helpers.
- Durable file writes go through `app/utils/atomic_io.py`
  (`atomic_write_json` / `atomic_write_text`) — never bare `open(w)`.
- `app/utils/batch_queue.py`, `app/batch/worklist.py`, `app/batch/pipeline.py`,
  and `app/utils/session_io.py` are **Qt-free** and must stay that way — the
  GUI and the headless CLI both import them.
- Conventional commit prefixes: `feat(ui):`, `feat:`, `fix:`, `docs:`. No
  `Co-Authored-By` lines. New commits only, never `--amend`.
- The `app/batch/pipeline.py` stage order must continue to mirror
  `MainWindow._on_transcription_finished` (per-track labels → pyannote →
  SimpleDiarizer fallback). Change one, change the other.
- Diarization or summarization failing must still leave a successfully
  produced transcript on disk — same invariant as the existing #14 fix.
- `graphify update .` after code changes.

---

## Feature 1 — Transcript-header "Summarize" checkbox

### Behaviour

A `QCheckBox("Summarize")` sits in the transcript header row
(`app/ui/transcript_viewer.py`, the same `header` layout that holds
`diarize_cb` and `diarize_btn`), immediately after "Identify speakers". It is
the **live source of truth** for whether the *next* transcription that
finishes auto-runs summarization — exactly the role `diarize_cb` plays for
diarization.

- **Checked + enabled** → after the next transcription completes for the
  displayed recording, `_maybe_auto_summarize()` runs.
- **Unchecked** → transcription completes with no summary; the user can still
  press the Summary panel's Generate button.
- **Disabled** when no AI provider is configured
  (`MainWindow._ai_provider_configured()` is False — provider `"none"`, or
  `"local"` with no usable model file). Tooltip when disabled: *"Choose an AI
  provider in Settings ▸ AI Assistant to enable summaries."* Mirrors how
  `diarize_cb` is disabled without an HF token.
- Toggling it writes `config["ai"]["auto_summarize"]`, so the checkbox and
  the Settings ▸ AI Assistant "Auto-summarize" checkbox are one setting with
  two surfaces (the Settings dialog already persists that key at
  `settings_dialog.py:765`).
- **No per-session override.** Unlike diarization's 1:1-call auto-uncheck
  (`_apply_diarization_default_for_session`), the Summarize checkbox is a
  pure config mirror — nothing about the recording changes its state.

### `TranscriptViewer` additions

Mirror the diarization trio verbatim:

| New member | Mirrors |
|---|---|
| `summarize_toggled = pyqtSignal(bool)` | `diarize_toggled` |
| `self.summarize_cb = QCheckBox("Summarize")` | `self.diarize_cb` |
| `set_summarize_available(available: bool)` | `set_diarization_available` — enables/disables the box, swaps the tooltip |
| `set_summarize_enabled(enabled: bool)` | `set_diarization_enabled` — sets checked state with `blockSignals` |
| `summarize_enabled() -> bool` | `diarization_enabled` — returns `self.summarize_cb.isChecked() and self.summarize_cb.isEnabled()` |

`summarize_cb.toggled.connect(self.summarize_toggled)`.

### `MainWindow` wiring

- In `_setup_ui` connections (near `main_window.py:563`):
  `self.transcript_viewer.summarize_toggled.connect(self._on_summarize_toggled)`.
- `_on_summarize_toggled(enabled)`: `self.config.set("ai", "auto_summarize", enabled)`.
- New `_sync_summarize_control()`, called wherever `_sync_diarization_controls()`
  is (startup at `main_window.py:565`, and after the Settings dialog closes,
  ~`main_window.py:2564`):
  - `set_summarize_available(self._ai_provider_configured())`
  - `set_summarize_enabled(bool(self.config.get("ai", "auto_summarize")))`
- `_maybe_auto_summarize()` (`main_window.py:3006`): replace the
  `if not self.config.get("ai", "auto_summarize"): return` line with
  `if not self.transcript_viewer.summarize_enabled(): return`. Keep the
  `general.auto_transcribe`, `_ai_provider_configured()`, and `_transcript`
  guards unchanged. (Background jobs for a non-displayed session already skip
  auto-summarize — that path is untouched.)

### Tests

- `tests/test_transcript_viewer_summarize.py` (new, pure-helper style like the
  diarize checkbox coverage): `summarize_enabled()` truth table — checked +
  enabled → True; checked + disabled → False; unchecked → False.
  `set_summarize_enabled` does not emit `summarize_toggled`.
- Smoke: `python -c "from app.ui.transcript_viewer import TranscriptViewer"`.
- Extend `tests/test_main_window_*` only if an existing MainWindow test file
  already builds a window cheaply enough; otherwise a targeted new test that
  patches `_run_summarize` and asserts `_maybe_auto_summarize` consults the
  checkbox.

---

## Feature 2 — "Delete Recording" → "Delete"

`app/ui/recordings_list.py`:

- Single-select (`recordings_list.py:875`): `QAction("Delete Recording", self)`
  → `QAction("Delete", self)`.
- Multi-select (`recordings_list.py:847`): `f"Delete {count} Recordings"` →
  `f"Delete {count}"`.

Rationale: the delete flow offers three scopes (audio only / transcriptions
only / everything), so "Delete Recording" understates what the action does.

### Tests

Covered by a smoke import; if `tests/test_recordings_list_*` has a menu-label
assertion it is updated to the new strings. No new test file.

---

## Feature 3 — Batch Transcription/Summarization

### 3.1 Overview

Today a queued recording is transcribed (and diarized per a global CLI
flag / saved setting). After this change, each queued recording carries an
**ordered set of operations** — `transcription`, `diarization`,
`summarization` — and the batch run performs exactly those, in that order,
skipping any whose output already exists on disk.

`diarization` is surfaced to the user as **"Speaker Recognition"**.

### 3.2 Queue tag — `app/utils/batch_queue.py`

Add a third optional key beside `batch_pending` / `batch_attempts`:

- **`batch_ops`** — a list, a subset of `["transcription", "diarization",
  "summarization"]`, always stored in that canonical order, deduped.

Back-compat: a recording with `batch_pending: true` and **no** `batch_ops`
reads as `["transcription"]`. Absence of `batch_pending` still means "not
queued".

New / changed functions (all Qt-free, all through `_update` + `atomic_write_json`):

```
OPS_ORDER = ("transcription", "diarization", "summarization")

def queued_ops(metadata) -> list[str]:
    """Canonical-ordered ops for this recording.
    [] when not queued. Legacy batch_pending:true with no batch_ops -> ["transcription"].
    Unknown / mis-ordered entries in a hand-edited file are filtered and re-sorted."""

def set_ops(directory, ops) -> bool:
    """Write batch_ops (canonical order, deduped, filtered to OPS_ORDER) and
    batch_pending = bool(ops). Empty ops clears both batch_ops and batch_pending.
    Always pops batch_attempts (re-queue == 'try again'), same as set_queued(..., True)."""
```

`set_queued(directory, True)` stays (used by `general.batch_auto_queue` and
any caller that just means "transcription"): it now also writes
`batch_ops = ["transcription"]`. `set_queued(directory, False)` clears
`batch_ops` too. `is_queued`, `attempts`, `exhausted`, `record_failure`,
`clear` are unchanged (`clear` already pops any unknown keys? — it explicitly
pops `PENDING_KEY` and `ATTEMPTS_KEY`; add `batch_ops` to its `mutate`).

### 3.3 Worklist — `app/batch/worklist.py`

- `Job` gains `ops: list[str]` (defaulted `field(default_factory=list)` so
  older test constructions still work, but `build_worklist` always sets it).
- `build_worklist`:
  - `ops = queued_ops(session)`; skip the recording if `ops` is empty.
  - Compute `has_transcript = (Path(directory) / "transcript.json").exists()`.
  - **Prerequisite guard:** if `"diarization"` or `"summarization"` is in
    `ops` but `"transcription"` is *not* and `not has_transcript`, drop that
    op and log `INFO` (`"<name>: dropping <op> — no transcript and transcription not queued"`).
    If dropping leaves `ops` empty, skip the recording.
  - **Audio requirement is now conditional:** only call `_pick_audio` /
    require a file when `"transcription"` or `"diarization"` is in the
    effective `ops`. A summarization-only job needs `transcript.json`, not a
    WAV — set `Job.audio_path = _pick_audio(session)` (may be `None`) and let
    the pipeline decide. When audio *is* required and missing, skip with the
    existing "no audio file on disk" log.
- CLI global overrides (see 3.5) are applied by the **runner** after
  `build_worklist` returns, mutating each `Job.ops` — `build_worklist` itself
  takes no new parameters.

### 3.4 Pipeline — `app/batch/pipeline.py` + `app/utils/session_io.py`

#### `BatchSettings`

- Add `ai_config: dict = field(default_factory=dict)` — the raw `config["ai"]`
  sub-dict (`provider`, `api_key`, `model`, `local_model_*`). Populated in
  `from_config` via `dict(config.data.get("ai", {}))` (or key-by-key). **This
  dict holds the API key — it must never be logged.** `BatchSettings` has no
  `__repr__` that dumps it, and the runner never logs the object.
- `diarize` stays as a coarse default but `run_job` no longer reads it for
  the decision — `job.ops` is authoritative (see below). `from_config` keeps
  computing `can_diarize = bool(want and hf_token)` and stores it; the runner
  uses it only to *strip* `diarization` from every job's ops when there is no
  token (so a stale `batch_ops` entry can't queue a stage pyannote can't run).

#### `JobOutcome`

- Add `summarized: bool = False`.
- `warnings` continues to collect non-fatal stage failures.

#### `run_job` restructure

`run_job(job, settings, workers=None, on_progress=None)` becomes stage-gated.
`ops = job.ops`.

```
transcript_exists = (Path(job.session["directory"]) / "transcript.json").exists()
result = None            # in-memory TranscriptResult once we have one
diarized = per_track = False
bleed_dropped = 0

# --- Stage 1: transcription ---
if "transcription" in ops:
    <existing transcription block, verbatim>:
      tracks = dual_track_plan(job.session, "diarization" in ops, settings.hf_token)
      worker = workers.transcription(job.audio_path, ...); result, error = _drive(...)
      if result is None: return JobOutcome(False, f"transcription failed: {error}", ...)
      bleed_dropped = getattr(worker, "bleed_dropped", 0)
      if tracks:
          per_track = True
          if bleed_dropped: warnings.append(<bleed message>)
      elif "diarization" in ops:
          <run DiarizationWorker over `result`>  -> diarized = True on success, else warnings
      else:
          <SimpleDiarizer fallback when metadata names both mic+system>  -> diarized on success
    result.merge_adjacent_same_speaker()
    <speaker_names "You"->user_name logic, verbatim>
    if not session_io.write_transcript(job.session, result, speaker_names=speaker_names):
        return JobOutcome(False, "could not write the transcript to disk", ...)
    transcript_exists = True

# --- Stage 2: speaker recognition on an existing transcript ---
elif "diarization" in ops:
    if not transcript_exists:
        return JobOutcome(False, "speaker recognition needs a transcript", ...)
    if _already_diarized(transcript.json):   # >1 distinct non-empty speaker id
        # nothing to do; treat as satisfied
    else:
        result = session_io.load_transcript(job.session)
        <run DiarizationWorker over `result`, full_cpu=True>
        if diarized_result is None:
            warnings.append(f"diarization failed: {error}")     # non-fatal
        else:
            result.merge_adjacent_same_speaker()
            session_io.write_transcript(job.session, diarized_result)
            diarized = True

# --- Stage 3: summarization ---
# Reached only if stage 1 did not return early, so a transcript exists
# (freshly written, or already on disk). A diarization *warning* from an
# earlier stage does not block this.
summarized = False
if "summarization" in ops:
    summary_path = Path(dir) / "summary.md"
    if summary_path.exists():
        summarized = True                     # already done; skip
    elif not (Path(dir) / "transcript.json").exists():
        warnings.append("summarization skipped — no transcript")
    else:
        summarized = _run_batch_summary(job.session, settings, on_progress)
        if not summarized:
            warnings.append("summarization failed: <reason>")   # non-fatal
```

Notes:

- **Skip-if-exists** is per stage: `transcript.json` present → skip
  transcription unless `"transcription"` was explicitly queued (the user
  asked to re-do it — the menu already warns before queueing that, 3.5);
  `summary.md` present → skip summarization always. Speaker recognition is
  "done" when the transcript already has more than one distinct speaker id.
- The `if "transcription" in ops` / `elif "diarization" in ops` split means
  a job that queues both runs diarization *inside* stage 1 (over the fresh
  in-memory result, no reload), exactly as `run_job` does today. Stage 2 is
  only for the diarization-only-on-existing-transcript case.
- Diarization decision inside stage 1 keys off `"diarization" in ops`, not
  `settings.diarize`.

#### New `session_io` helpers

```
def load_transcript(session) -> TranscriptResult | None:
    """Rebuild a TranscriptResult from transcript.json. None if missing/corrupt."""

def write_summary(session, summary_markdown: str, action_items: list, meta: dict) -> bool:
    """atomic_write_text summary.md, atomic_write_json action_items.json and
    summary_meta.json, then export_session_markdown(session) to refresh
    transcript.md. Mirrors what MainWindow._on_summary_ready +
    _on_actions_ready + _on_action_items_changed do, in one Qt-free call."""
```

`load_transcript` needs a `TranscriptResult.from_dict(d)` classmethod added to
`app/transcription/transcriber.py`, mirroring the existing
`TranscriptSegment.from_dict` (transcriber.py:78) — build
`segments=[TranscriptSegment.from_dict(s) for s in d.get("segments", [])]`,
carry `language`/`duration`/`model_size`/`transcribe_seconds`.

#### `_run_batch_summary` (module-private in `pipeline.py`)

```
from app.ai.provider_factory import create_provider, describe_ai_model
from app.ai.summarizer import build_summary_prompt, build_action_items_prompt, parse_action_items

provider = create_provider(settings.ai_config)     # may raise / return None
if provider is None: return False
segments = session_io.load_transcript(session).segments
speaker_names = session_io.load_speaker_names(session)
notes = _read_text(dir / "notes.txt") or ""        # helper already in session_io
max_chars = provider.max_context_chars
t0 = time.monotonic()
summary = provider.complete(build_summary_prompt(segments, speaker_names, notes,
                                                 max_transcript_chars=max_chars))
actions = parse_action_items(provider.complete(
    build_action_items_prompt(segments, speaker_names, notes, max_transcript_chars=max_chars)))
meta = {
    "generated_by": "talktrack-batch",
    "model": describe_ai_model(settings.ai_config),
    "seconds": round(time.monotonic() - t0, 1),
    "generated_at": datetime.now().isoformat(timespec="seconds"),
}
return session_io.write_summary(session, summary, actions, meta)
```

Any exception here is caught by `run_job`, appended to `warnings` with
`type(e).__name__` + `str(e)` (which for the AI SDKs does **not** contain the
key), and the job still succeeds on the strength of its transcript.

`generated_by: "talktrack-batch"` is distinct from the app's
`"talktrack-app"` and the `talktrack-batch-summarize` skill's
`"talktrack-batch-summarize"` — three independent producers, no collision.

### 3.5 Runner & CLI — `app/batch/runner.py`, `app/batch/worker.py`, `batch_transcribe.py`, `app/ui/batch_run_dialog.py`

#### `runner.py`

- `BatchSettings.from_config(config, diarize=args.diarize)` unchanged call;
  `from_config` now also fills `ai_config`.
- After `build_worklist(...)`, apply CLI op overrides to each `Job.ops`:
  - `--diarize` → ensure `"diarization"` in ops (only if `settings.hf_token`;
    otherwise log once and skip the add).
  - `--no-diarize` → remove `"diarization"` from ops.
  - `--summarize` → ensure `"summarization"` in ops (only if an AI provider is
    configured in `ai_config`; otherwise log once and skip).
  - `--no-summarize` → remove `"summarization"` from ops.
  - Re-canonicalise order after each change. If a job's ops become empty, drop
    the job with a log line.
- New parser args mirroring the diarize pair:
  `--summarize` (`dest="summarize", action="store_true", default=None`),
  `--no-summarize` (`action="store_false"`).
- `_describe(outcome)`: append `"summary written"` when `outcome.summarized`.
- Startup log block: add `logger.info("AI summary: %s", "on" if <any job has summarization> else "off")`
  — **provider name only if you must, never the key**; simplest is just on/off.
- Per-job log: `logger.info("  - %s  [%s]", job.label, ", ".join(job.ops))`
  in the "Queued (%d)" list and in `--dry-run` output, so the operator sees
  what each recording will get. **No AI config in this output.**
- `logger.info("Settings:    %s", CONFIG_FILE)` line stays exactly as is
  (path only).
- Exit codes unchanged (`EXIT_OK` / `EXIT_FATAL` / `EXIT_SOME_FAILED`). A job
  whose transcript succeeded but whose summary failed is still `outcome.ok`
  → counts as processed, warnings logged, tag cleared.

#### `worker.py` (`BatchRunnerWorker`)

- `run_job` already receives the per-job `ops` via `Job`. The only change:
  when constructing `BatchRunnerWorker`'s `settings`, `MainWindow` passes a
  `BatchSettings` whose `ai_config` is populated (it already builds
  `BatchSettings.from_config`). Apply the same CLI-style overrides from the
  dialog (below) to the worklist the worker builds — simplest is for the
  worker to accept an optional `op_overrides` dict and apply it in `run()`
  right after `build_worklist`, symmetric with the runner.

#### `batch_run_dialog.py` (`BatchRunDialog`)

- Retitle to **"Run Batch Processing"** (window title + any header copy).
  `File ▸ Run Batch Transcription…` menu item → **"Run Batch Processing…"**.
- The existing "Diarization" group stays (it is a global override).
- Add a sibling **"Summarization"** group: one checkbox *"Generate AI summary
  and action items"*, disabled with an explanatory label when
  `config["ai"]["provider"] == "none"` (or local w/o model). `summarize_enabled()`
  accessor mirroring `diarize_enabled()`.
- `MainWindow._open_batch_run_dialog` maps the dialog's diarize/summarize
  choices into the `op_overrides` it hands the worker.

#### `batch_transcribe.py`

No change beyond what it already does (it just calls `runner.main`).

#### `docs/batch-transcription.md`

Document `batch_ops`, the three operations, the dependency rule, the strict
order, and the new `--summarize` / `--no-summarize` flags.

### 3.6 Recordings-list context menu & pill — `app/ui/recordings_list.py`

#### Menu

Replace the flat "Queue for Batch Transcription" / "Remove from Batch Queue"
items in `_add_batch_queue_actions` with a **sub-menu** titled
**"Batch Transcription/Summarization"** containing three checkable actions:

| Action | Checked when | Enabled when |
|---|---|---|
| Transcription | every selected recording has `"transcription"` in its `batch_ops` | always |
| Speaker Recognition | every selected has `"diarization"` in `batch_ops` | (`transcript.json` exists for **every** selected recording **OR** "Transcription" is checked) **AND** an HF token is set |
| Summarization | every selected has `"summarization"` in `batch_ops` | (same transcript-or-Transcription rule) **AND** an AI provider is configured |

Behaviour:

- Toggling an action rewrites the effective op-set for **every** selected
  recording via `batch_queue.set_ops(dir, ops)` and calls `self.refresh()`.
- Unchecking **Transcription** while no selected recording has a transcript on
  disk also unchecks and disables Speaker Recognition and Summarization (their
  prerequisite is gone). Implement by recomputing enablement after every
  toggle — simplest is to rebuild the sub-menu, or gate via a shared helper
  `_batch_ops_enablement(metadatas, checked_ops) -> {op: (checked, enabled)}`
  (pure, tested).
- When **all three** actions end unchecked for a recording, that is
  "remove from queue" → `set_ops(dir, [])` (clears `batch_pending`).
- The re-transcribe **overwrite warning** (`_set_queued`, currently fired
  whenever a transcribed recording is queued) moves to: fired only when
  **Transcription** is newly checked for a recording that already has
  `transcript.json`. Speaker Recognition / Summarization on an existing
  transcript never warn.
- "Process Batch Queue Now…" (`run_batch_requested`) still appears below the
  sub-menu whenever anything in the selection is queued.
- A recording currently transcribing is still excluded from the "can queue"
  set, as today.

Extract the enablement + label logic into pure functions beside
`partition_by_queue_state` so it is unit-testable without a `QMenu`.

#### Pill

Keep the single peach **"Queued"** pill (`recordingBadgeQueued`, hourglass).
Its tooltip becomes *"Queued for batch: Transcription, Speaker Recognition,
Summarization"* — only the ops actually queued, canonical order. No new pill
colours, no per-op icons (keeps the row uncluttered, consistent with the
lifecycle-track change already on this branch).

#### `batch_btn`

"Run Batch (N)" count logic unchanged — N is "recordings queued" regardless
of which ops. `_update_batch_btn_visibility` untouched.

### 3.7 Tests

TDD (pure logic):

- `tests/test_batch_queue.py`: `queued_ops` — legacy `batch_pending:true` →
  `["transcription"]`; explicit `batch_ops` round-trips in canonical order;
  hand-edited junk / wrong order is filtered + re-sorted; `set_ops([])` clears
  both keys; `set_ops` resets `batch_attempts`; `clear` drops `batch_ops`.
- `tests/test_batch_worklist.py`: `Job.ops` populated; summarization-only job
  with a transcript and no audio is included (audio_path may be None);
  diarization-only / summarization-only with neither transcript nor queued
  transcription is dropped; transcription-in-ops without audio is skipped.
- `tests/test_batch_pipeline.py`: stage order transcription → diarization →
  summarization; each stage skipped when its output already exists;
  summarization-only path (fake provider) writes `summary.md` /
  `action_items.json` / `summary_meta.json` with `generated_by:"talktrack-batch"`
  and refreshes `transcript.md`; a raising provider → `outcome.ok` stays True,
  `summarized` False, a warning is recorded; diarization-only-over-existing
  path; `ai_config` with a fake key never appears in any captured log.
- `tests/test_batch_runner.py`: `--summarize` / `--no-summarize` override
  ops; `--dry-run` prints per-job ops and **not** the api key; `_describe`
  mentions the summary; exit codes unchanged; a summary-only failure still
  exits `EXIT_OK`.
- `tests/test_recordings_list_batch.py` (+ `_badges` if it asserts the pill
  tooltip): the pure enablement/label helper — Speaker Recognition &
  Summarization disabled with no transcript and Transcription unchecked,
  enabled once Transcription is checked; HF-token / AI-provider gating;
  all-unchecked → `set_ops([])`.
- `tests/test_transcript_viewer_summarize.py`: Feature 1 (see above).
- `tests/test_session_io.py`: `load_transcript` round-trips
  `write_transcript`; `write_summary` writes the three files + calls the
  markdown export.

Smoke: `transcript_viewer`, `recordings_list`, `batch_run_dialog` importable
after the changes.

Full suite green: `.venv\Scripts\python.exe -m pytest tests/ -q`.

---

## Data flow (Feature 3, one queued recording)

```
recordings list menu  --set_ops(dir, ["transcription","diarization","summarization"])-->
  metadata.json: {batch_pending:true, batch_ops:[...canonical...]}

batch run (CLI runner OR in-app BatchRunnerWorker)
  build_worklist(recordings_dir)
    -> per dir: queued_ops(session), prereq guard, audio check
    -> [Job(directory, session, label, audio_path, ops)]
  apply CLI/dialog op-overrides to each Job.ops
  for job in jobs (oldest first, cutoff-checked between jobs):
    run_job(job, settings)
      stage 1 transcription  (if queued & no transcript, or explicitly re-queued)
        -> TranscriptionWorker.run() inline
        -> diarization folded in here if "diarization" in ops (pyannote / SimpleDiarizer)
        -> session_io.write_transcript()  -> refreshes transcript.md
      stage 2 speaker recognition  (only if "diarization" in ops and stage 1 didn't run)
        -> session_io.load_transcript() -> DiarizationWorker.run() -> write_transcript()
      stage 3 summarization  (if "summarization" in ops and no summary.md)
        -> create_provider(settings.ai_config)
        -> build_summary_prompt / build_action_items_prompt -> provider.complete()
        -> session_io.write_summary()  -> summary.md + action_items.json
                                        + summary_meta.json(generated_by:"talktrack-batch")
                                        -> refreshes transcript.md
      -> JobOutcome(ok, ..., diarized, per_track, summarized, warnings)
    ok  -> batch_queue.clear(dir);  not ok -> batch_queue.record_failure(dir)
```

---

## Error handling

- **Missing prerequisite at worklist time** → op dropped + logged; recording
  skipped if that empties its ops. Not a failure, not an attempt.
- **Transcription stage fails** → `JobOutcome(ok=False)`, `record_failure`,
  3 strikes retires the recording (unchanged).
- **Speaker recognition fails** (stage 1 or 2) → warning on an otherwise
  successful outcome; the transcript is still written. Matches today's
  behaviour and the #14 invariant.
- **Summarization fails** (provider error, timeout, parse) → warning on a
  successful outcome; `summarized=False`; transcript untouched. The exception
  text is logged with `type(e).__name__` — the AI SDKs do not put the key in
  their messages, and `BatchSettings` is never logged.
- **No AI provider but `summarization` queued** → runner strips the op at
  override time with a single log line; if a stale tag still carries it into
  `run_job`, stage 3 records a "no provider" warning.
- **Corrupt `transcript.json` for a summary-only job** →
  `session_io.load_transcript` returns None → warning, job still `ok` (nothing
  to fail).

---

## Delivery

Four commits on `feature/ui-redesign`:

1. `feat(ui): add a Summarize checkbox to the transcript header` — Feature 1.
2. `feat(ui): shorten the single-recording delete menu action to "Delete"` —
   Feature 2.
3. `feat: add speaker-recognition and summarization stages to batch runs` —
   Feature 3 backend: `batch_queue`, `worklist`, `pipeline`, `session_io`,
   `transcriber.TranscriptResult.from_dict`, `runner`, `worker`,
   `batch_transcribe`, `docs/batch-transcription.md`, tests.
4. `feat(ui): choose batch operations per recording` — Feature 3 UI: context
   sub-menu, pill tooltip, `BatchRunDialog` summarization group + retitle,
   `MainWindow` dialog wiring, tests.

Each commit ships with its tests green and the full suite green.

---

## Out of scope

- No coordination between a running GUI and a running batch process (they
  already run independently; each loads its own models).
- No new pill colours or per-op row iconography.
- No change to the `talktrack-batch-summarize` Claude-session skill — it keeps
  its own `generated_by` stamp and its no-API-key contract.
- No GPU llama-cpp wheels, no new AI providers.
- Word-level timestamps stay disabled.
