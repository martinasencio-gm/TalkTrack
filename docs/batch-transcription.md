# Batch transcription

Transcription is slow on CPU, and speaker diarization is slower still —
often longer than the recording itself. `batch_transcribe.py` moves both to
an unattended run, so recordings can pile up during the day and be worked
through overnight.

## Queueing recordings

Right-click one or more recordings in the Recordings list and choose
**Queue for Batch Transcription**. A peach **Queued** pill appears on each
row. The same menu offers **Remove from Batch Queue**.

Recordings the app declines to transcribe itself — auto-transcribe is off,
or the recording is shorter than `transcription.min_duration` — are queued
automatically. Turn that off with *Settings → General → "Queue skipped
recordings for batch transcription"*.

The tag lives in the recording's own `metadata.json` (`batch_pending`), so
it travels with the folder.

## Running it from the app

You can run batch transcription on demand directly inside TalkTrack:

- Click **File > Run Batch Transcription...** in the menu bar.
- Or click the **Run Batch (N)** button above the recordings list when queued items exist.
- Or right-click in the recordings list and choose **Process Batch Queue Now...**.

The launch dialog offers two execution modes:
1. **Process inside app (recommended):** Runs as a background task in TalkTrack with live status bar progress and model caching in memory.
2. **Run as independent background process:** Spawns a detached `pythonw.exe` process that continues running even if TalkTrack is closed.

## Running it from the command line

```bash
.venv\Scripts\python.exe batch_transcribe.py --until 07:00
```

| Option | Meaning |
| --- | --- |
| `--until TIME` | **Required.** The latest time a *new* recording may be started. `HH:MM` means the next occurrence of that time, so a run launched at 23:00 with `--until 07:00` gets the whole night. `YYYY-MM-DDTHH:MM` is an absolute instant. A recording already in progress is allowed to finish. |
| `--diarize` / `--no-diarize` | Override the app's saved diarization setting for this run. Defaults to whatever the app has, and is forced off when no HuggingFace token is configured. |
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
token and AI API key must never reach a log file.

## What a run does, and doesn't

Each recording goes through the same pipeline the app uses, writing
`transcript.json`, `transcript.txt` and `transcript.md` into the
recording's own folder.

It does **not** generate AI summaries or action items: that would mean
network calls and API spend in an unattended run. Generate those from the
app afterwards.

## Running alongside the app

The batch run does not coordinate with a running TalkTrack window. Both
can run at once, and each loads its own Whisper model, so expect roughly
double the RAM and CPU. If both transcribe the *same* recording
simultaneously, whichever finishes last wins — unlikely in practice, since
the app only transcribes what you ask it to, but worth knowing.
