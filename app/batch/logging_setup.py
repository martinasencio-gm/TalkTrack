"""The batch run log.

One file per run under ``<app data>/batch Log``, so a scheduled overnight
run leaves a readable record of what it did — a run launched by Task
Scheduler under pythonw.exe has no console and no other output at all.

Nothing here may write a HuggingFace token or an API key. The runner logs
the config *path*, never config contents.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path

from app.utils.app_paths import APP_DATA_DIR

LOG_DIR_NAME = "batch Log"
KEEP_RUNS = 30


def log_dir():
    return Path(APP_DATA_DIR) / LOG_DIR_NAME


def prune_old_logs(directory=None, keep=KEEP_RUNS):
    """Delete all but the newest `keep` run logs. Best-effort."""
    directory = Path(directory) if directory else log_dir()
    try:
        logs = sorted(directory.glob("batch_*.log"))
    except OSError:
        return []
    removed = []
    for path in logs[:-keep] if keep else logs:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def get_latest_log(directory=None):
    """Find the most recent batch run log file, or None if none exist."""
    directory = Path(directory) if directory else log_dir()
    try:
        logs = sorted(directory.glob("batch_*.log"))
        return logs[-1] if logs else None
    except OSError:
        return None


def open_batch_logs_folder(directory=None):
    """Open the batch logs directory in Windows Explorer (creating it if needed)."""
    import os
    directory = Path(directory) if directory else log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(directory))
    return directory


def open_batch_log(log_path=None, directory=None):
    """Open the given or newest batch run log file. Falls back to opening the folder."""
    import os
    if log_path is None:
        log_path = get_latest_log(directory=directory)
    if log_path and Path(log_path).exists():
        if sys.platform == "win32":
            os.startfile(str(log_path))
        return Path(log_path)
    return open_batch_logs_folder(directory=directory)


def setup_logging(directory=None, now=None, verbose=False):
    """Start a new run log. Returns its path.

    Also redirects stderr into it: under pythonw.exe there is no console,
    so an unhandled traceback would otherwise vanish entirely.
    """
    directory = Path(directory) if directory else log_dir()
    directory.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    path = directory / f"batch_{now:%Y%m%d_%H%M%S}.log"

    handlers = [logging.FileHandler(path, encoding="utf-8")]
    # Only when a console is actually attached — under pythonw.exe
    # sys.stdout is None and a StreamHandler on it raises on first use.
    if sys.stdout is not None:
        # The Windows console defaults to cp1252, which turns every em
        # dash in these messages into a stray '?'.
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
        handlers.append(logging.StreamHandler(sys.stdout))

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )
    # Model loading and HTTP chatter from the ML stack would bury the run
    # narrative; the interesting lines all come from app.*.
    for noisy in ("urllib3", "httpx", "filelock", "speechbrain", "pyannote", "torio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _redirect_stderr(path)
    prune_old_logs(directory)
    return path


def _redirect_stderr(path):
    try:
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return
    sys.stderr = stream
    if sys.stdout is None:
        # pythonw: give print() somewhere to go rather than raising.
        sys.stdout = stream
