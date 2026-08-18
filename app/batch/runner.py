"""The batch run itself: parse arguments, pick the work, do it, report.

Launched by Windows Task Scheduler through batch_transcribe.py. See that
file for the import-order constraints that have to hold before anything
here runs.
"""
import argparse
import logging
import time
from datetime import datetime

from app.batch.cutoff import CutoffError, may_start_another, parse_cutoff
from app.batch.logging_setup import setup_logging
from app.batch.pipeline import BatchSettings, run_job
from app.batch.worklist import build_worklist
from app.utils import batch_queue

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_SOME_FAILED = 2

DESCRIPTION = """\
Transcribe (and optionally diarize) the TalkTrack recordings that have been
queued for batch processing.

Queue recordings from the app: right-click one in the Recordings list and
choose "Queue for batch transcription".

Scheduled nightly run:

  schtasks /Create /TN TalkTrackBatch /SC DAILY /ST 23:00 /TR ^
    "\\"<repo>\\.venv\\Scripts\\pythonw.exe\\" \\"<repo>\\batch_transcribe.py\\" --until 07:00"

Point the task at the venv interpreter - a global Python has neither the
dependencies nor a working torch. Output goes to the run log under
Documents\\TalkTrack\\batch Log.
"""


def build_parser():
    parser = argparse.ArgumentParser(
        prog="batch_transcribe",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--until", metavar="TIME", required=True,
        help="latest time a new recording may be STARTED, as HH:MM (the next "
             "occurrence of that time) or YYYY-MM-DDTHH:MM. A recording "
             "already in progress is allowed to finish.",
    )
    parser.add_argument(
        "--diarize", dest="diarize", action="store_true", default=None,
        help="identify individual speakers with pyannote (slow; needs a "
             "HuggingFace token). Defaults to the app's saved setting.",
    )
    parser.add_argument(
        "--no-diarize", dest="diarize", action="store_false",
        help="skip speaker identification even if the app has it enabled.",
    )
    parser.add_argument(
        "--limit", type=int, metavar="N",
        help="process at most N recordings this run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="list what would be processed and exit without transcribing.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="log at DEBUG level.",
    )
    return parser


def _describe(outcome):
    parts = [f"{outcome.segments} segments"]
    if outcome.per_track:
        parts.append("per-track labels")
    elif outcome.diarized:
        parts.append("speakers identified")
    parts.append(f"{outcome.elapsed:.0f}s")
    return ", ".join(parts)


def run(argv=None):
    args = build_parser().parse_args(argv)
    log_path = setup_logging(verbose=args.verbose)

    try:
        cutoff = parse_cutoff(args.until)
    except CutoffError as e:
        logger.error("Bad --until value: %s", e)
        return EXIT_FATAL

    # Imported here, not at module scope: loading Config resolves (and can
    # migrate) the app data directory, which should happen after logging
    # is up so any trouble it hits is recorded.
    from app.utils.config import Config, CONFIG_FILE

    try:
        config = Config()
    except Exception:
        logger.exception("Could not read the TalkTrack settings")
        return EXIT_FATAL

    recordings_dir = config.get("output", "directory")
    settings = BatchSettings.from_config(config, diarize=args.diarize)

    logger.info("TalkTrack batch run starting")
    logger.info("Log file:    %s", log_path)
    logger.info("Settings:    %s", CONFIG_FILE)   # path only — never contents
    logger.info("Recordings:  %s", recordings_dir)
    logger.info("Model:       %s on %s", settings.model_size, settings.device)
    logger.info("Diarization: %s", "on" if settings.diarize else "off")
    logger.info("Cutoff:      %s (no new recording started after this)", cutoff)

    jobs = build_worklist(recordings_dir, limit=args.limit)
    if not jobs:
        logger.info("Nothing queued for batch processing — nothing to do.")
        return EXIT_OK

    logger.info("Queued (%d):", len(jobs))
    for job in jobs:
        logger.info("  - %s", job.label)

    if args.dry_run:
        logger.info("Dry run — stopping before any transcription.")
        return EXIT_OK

    return _process(jobs, settings, cutoff)


def _process(jobs, settings, cutoff):
    processed, failed, deferred = [], [], []
    run_started = time.monotonic()

    for job in jobs:
        if not may_start_another(cutoff, now=datetime.now()):
            # Checked between recordings only: a job already running is
            # allowed to finish rather than throwing away the CPU time it
            # has already spent.
            deferred.append(job.label)
            continue

        logger.info("--- %s", job.label)
        try:
            outcome = run_job(job, settings, on_progress=logger.info)
        except Exception as e:
            logger.exception("Unhandled error processing %s", job.label)
            outcome = None
            message = f"{type(e).__name__}: {e}"
        else:
            message = outcome.message

        if outcome is not None and outcome.ok:
            for warning in outcome.warnings:
                logger.warning("    %s", warning)
            logger.info("    done — %s", _describe(outcome))
            processed.append(job.label)
            batch_queue.clear(job.directory)
        else:
            logger.error("    failed — %s", message)
            failed.append((job.label, message))
            attempts = batch_queue.record_failure(job.directory)
            if attempts >= batch_queue.MAX_ATTEMPTS:
                logger.warning(
                    "    %s has now failed %d times and will be skipped until "
                    "it is queued again", job.label, attempts,
                )

    _report(processed, failed, deferred, time.monotonic() - run_started)
    return EXIT_SOME_FAILED if failed else EXIT_OK


def _report(processed, failed, deferred, elapsed):
    logger.info("=== Batch run finished in %.0f min ===", elapsed / 60)
    logger.info("Processed (%d):", len(processed))
    for label in processed:
        logger.info("  - %s", label)
    if failed:
        logger.info("Failed (%d):", len(failed))
        for label, reason in failed:
            logger.info("  - %s — %s", label, reason)
    if deferred:
        logger.info("Deferred past the cutoff (%d), still queued:", len(deferred))
        for label in deferred:
            logger.info("  - %s", label)


def main(argv=None):
    """Entry point. Returns a process exit code."""
    # A QCoreApplication (not QApplication — no widgets, no display) so the
    # app's QThread workers behave exactly as they do in the GUI. The
    # runner drives them by calling run() inline, so no event loop is
    # started and nothing here needs one.
    from PyQt6.QtCore import QCoreApplication

    app = QCoreApplication([])
    try:
        return run(argv)
    finally:
        del app
