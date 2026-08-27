# Batch transcription

Transcription is slow on CPU, and speaker diarization is slower still —
often longer than the recording itself. `batch_transcribe.py` moves both to
an unattended run, so recordings can pile up during the day and be worked
through overnight. A run can also generate the AI summary and action items.

## Operations

Each queued recording carries an ordered set of operations, a subset of:

1. **Transcription** — produce `transcript.json` / `transcript.txt` / `transcript.md`.
2. **Speaker Recognition** — run pyannote diarization (needs a HuggingFace
   token). Internally `diarization`.
3. **Summarization** — generate `summary.md` + `action_items.json` via the
   configured AI provider. `summary_meta.json` is stamped
   `generated_by: "talktrack-batch"`.

They always run in that order, and a stage is skipped when its output is
already on disk (`summary.md` present → summarization skipped; a transcript
with more than one speaker → speaker recognition skipped).

**Dependency rule:** Speaker Recognition and Summarization both need a
transcript. If a recording has none *and* Transcription is not also queued,
that operation is dropped at worklist time (logged, not counted as a
failure). A summarization-only job needs `transcript.json` but no audio
file.

## Queueing recordings

Right-click one or more recordings in the Recordings list and open the
**Batch Transcription/Summarization** sub-menu. It has a checkable item per
operation — Transcription, Speaker Recognition, Summarization. Speaker
Recognition and Summarization are selectable once a transcript exists (or
Transcription is checked) and the relevant credential is configured. A
peach **Queued** pill appears on each row; its tooltip lists the queued
operations. Unchecking all three removes the recording from the queue.

Recordings the app declines to transcribe itself — auto-transcribe is off,
or the recording is shorter than `transcription.min_duration` — are queued
automatically for transcription. Turn that off with *Settings → General →
"Queue skipped recordings for batch transcription"*.

The tag lives in the recording's own `metadata.json`: `batch_pending`
(bool) plus `batch_ops` (the operation list, canonical order). A recording
with `batch_pending: true` and no `batch_ops` — the pre-`batch_ops` layout
— reads as transcription only. The tag travels with the folder.

## Running it from the app

You can run batch transcription on demand directly inside TalkTrack:

- Click **File > Run Batch Processing...** in the menu bar.
- Or click the **Run Batch (N)** button above the recordings list when queued items exist.
- Or right-click in the recordings list and choose **Process Batch Queue Now...**.

The launch dialog offers two execution modes:
1. **Process inside app (recommended):** Runs as a background task in TalkTrack with live status bar progress and model caching in memory.
2. **Run as independent background process:** Spawns a detached `pythonw.exe` process that continues running even if TalkTrack is closed.

It also has **Diarization** and **Summarization** checkboxes: ticking either
adds that operation to *every* queued recording for this run (the same as
`--diarize` / `--summarize` on the command line). Leaving one unticked does
not remove it — a recording queued with that operation still gets it.
Summarization is disabled when no AI provider is configured.

## Running it from the command line

```bash
.venv\Scripts\python.exe batch_transcribe.py --until 07:00
```

| Option | Meaning |
| --- | --- |
| `--until TIME` | **Required.** The latest time a *new* recording may be started. `HH:MM` means the next occurrence of that time, so a run launched at 23:00 with `--until 07:00` gets the whole night. `YYYY-MM-DDTHH:MM` is an absolute instant. A recording already in progress is allowed to finish. |
| `--diarize` / `--no-diarize` | Add / remove Speaker Recognition on every queued recording for this run. Defaults to whatever each recording carries, and `--diarize` is ignored when no HuggingFace token is configured. |
| `--summarize` / `--no-summarize` | Add / remove Summarization on every queued recording for this run. `--summarize` is ignored when no AI provider is configured. |
| `--limit N` | Process at most N recordings. |
| `--dry-run` | Print the worklist and exit without transcribing. |
| `--verbose` | Log at DEBUG level. |

Exit codes: `0` everything done or cleanly deferred at the cutoff, `1`
fatal (bad arguments, unreadable settings), `2` the run completed but one
or more recordings failed. Task Scheduler shows this as the last run
result.

Recordings are processed oldest first, so a run that hits the cutoff has
cleared the longest-waiting backlog. Anything left over stays queued for
the next run.

A recording that fails three times is skipped until it is queued again —
one corrupt file can't consume every future run. Re-queueing it from the
app resets the counter.

## Scheduling it

```bash
schtasks /Create /TN TalkTrackBatch /SC DAILY /ST 23:00 /TR "\"C:\src\talktrack\.venv\Scripts\pythonw.exe\" \"C:\src\talktrack\batch_transcribe.py\" --until 07:00"
```

- Point the task at the **venv** interpreter. A global Python has neither
  the dependencies nor a working torch.
- `pythonw.exe` keeps a console window from flashing up. All output goes
  to the run log instead.
- Under *Conditions*, consider clearing "Start the task only if the
  computer is on AC power" if you want it to run on battery, and setting
  "Wake the computer to run this task".

## The run log

One file per run under `Documents\TalkTrack\batch Log\`, named
`batch_<timestamp>.log`. The 30 most recent are kept.

It records the settings **path** but never its contents — the HuggingFace
token and AI API key must never reach a log file. The per-recording line
shows the queued operations (`- Weekly sync  [transcription, summarization]`)
but no AI configuration.

## What a run does, and doesn't

Each recording goes through the same pipeline the app uses, writing
`transcript.json`, `transcript.txt` and `transcript.md` into the
recording's own folder, plus `summary.md` / `action_items.json` when
Summarization is queued.

Summarization makes real network calls and spends against your AI API key,
so it only runs for recordings explicitly queued with it (or a run passed
`--summarize`). A summary that fails — provider error, timeout — is logged
as a warning and leaves the transcript intact; the recording still counts
as processed.

## Running alongside the app

The batch run does not coordinate with a running TalkTrack window. Both
can run at once, and each loads its own Whisper model, so expect roughly
double the RAM and CPU. If both transcribe the *same* recording
simultaneously, whichever finishes last wins — unlikely in practice, since
the app only transcribes what you ask it to, but worth knowing.
