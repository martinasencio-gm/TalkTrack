"""QThread worker for running batch transcription in-process.

Processes queued recordings sequentially, emitting Qt signals for live GUI
feedback (status bar, list pill updates, and activity indicator).
"""
import logging
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from app.batch.cutoff import may_start_another
from app.batch.pipeline import BatchSettings, run_job
from app.batch.worklist import build_worklist
from app.utils import batch_queue
from app.utils.batch_queue import OPS_ORDER

logger = logging.getLogger(__name__)


class BatchRunnerWorker(QThread):
    """Executes queued batch transcription jobs in a background thread."""

    job_started = pyqtSignal(str, int, int)  # (job_label, current_1_based_idx, total_count)
    job_progress = pyqtSignal(str)           # (stage_message)
    job_finished = pyqtSignal(object, object) # (job, outcome)
    batch_finished = pyqtSignal(int, int, int) # (processed_count, failed_count, deferred_count)
    cancelled = pyqtSignal()

    def __init__(self, recordings_dir, settings=None, limit=None, cutoff=None,
                 op_overrides=None, parent=None):
        super().__init__(parent)
        self.recordings_dir = Path(recordings_dir)
        self.settings = settings or BatchSettings()
        self.limit = limit
        self.cutoff = cutoff
        # {"diarization": bool|None, "summarization": bool|None} from the
        # Run Batch dialog — a global add/remove on top of each recording's
        # own batch_ops, symmetric with the CLI's --diarize / --summarize.
        self.op_overrides = op_overrides or {}
        self._is_cancelled = False

    def _apply_op_overrides(self, jobs):
        want_diar = self.op_overrides.get("diarization")
        want_summ = self.op_overrides.get("summarization")
        if want_diar is None and want_summ is None:
            return jobs

        can_summarize = bool(self.settings.ai_config.get("provider")
                             and self.settings.ai_config.get("provider") != "none")
        kept = []
        for job in jobs:
            present = set(job.ops)
            if want_diar is True and self.settings.hf_token:
                present.add("diarization")
            elif want_diar is False:
                present.discard("diarization")
            if want_summ is True and can_summarize:
                present.add("summarization")
            elif want_summ is False:
                present.discard("summarization")
            job.ops = [o for o in OPS_ORDER if o in present]
            if job.ops:
                kept.append(job)
            else:
                logger.info("BatchRunnerWorker dropping %s — no operations left", job.label)
        return kept

    def cancel(self):
        """Request cooperative cancellation between recordings."""
        self._is_cancelled = True

    def is_cancelled(self):
        return self._is_cancelled

    def _on_progress(self, msg):
        self.job_progress.emit(str(msg))

    def run(self):
        self._is_cancelled = False
        jobs = build_worklist(self.recordings_dir, limit=self.limit)
        jobs = self._apply_op_overrides(jobs)
        if not jobs:
            self.batch_finished.emit(0, 0, 0)
            return

        total = len(jobs)
        processed = 0
        failed = 0
        deferred = 0

        for idx, job in enumerate(jobs, start=1):
            if self._is_cancelled:
                logger.info("BatchRunnerWorker cancelled before processing %s", job.label)
                break

            if self.cutoff is not None and not may_start_another(self.cutoff, now=datetime.now()):
                logger.info("BatchRunnerWorker reached cutoff before %s", job.label)
                deferred += 1
                continue

            self.job_started.emit(job.label, idx, total)
            logger.info("BatchRunnerWorker starting [%d/%d]: %s", idx, total, job.label)

            try:
                outcome = run_job(job, self.settings, on_progress=self._on_progress)
            except Exception as e:
                logger.exception("Unhandled error processing %s in batch worker", job.label)
                from app.batch.pipeline import JobOutcome
                outcome = JobOutcome(False, f"{type(e).__name__}: {e}")

            if outcome.ok:
                batch_queue.clear(job.directory)
                processed += 1
                logger.info("BatchRunnerWorker finished %s: ok", job.label)
            else:
                batch_queue.record_failure(job.directory)
                failed += 1
                logger.warning("BatchRunnerWorker finished %s: failed (%s)", job.label, outcome.message)

            self.job_finished.emit(job, outcome)

        if self._is_cancelled:
            self.cancelled.emit()
        else:
            self.batch_finished.emit(processed, failed, deferred)
