"""The per-recording "process me in the next batch run" tag.

The tag lives in each recording's own metadata.json rather than in a list
inside settings.json: it travels with the folder, so deleting or moving a
recording outside the app can't leave a dangling entry behind, and there is
no separate list for the GUI to keep in sync.

Three keys, all optional — their absence means "not queued", so every
recording that predates this feature stays valid and untouched:

- ``batch_pending``  (bool) queued for the next run
- ``batch_attempts`` (int)  consecutive failures; the runner gives up on a
  recording at MAX_ATTEMPTS so one unreadable file can't consume every
  future run.
- ``batch_ops``      (list) which operations the run should perform, a
  subset of OPS_ORDER stored in that canonical order. Absent alongside
  ``batch_pending: true`` reads as ``["transcription"]`` (the behaviour
  before this key existed).

Qt-free on purpose: the GUI and the headless batch CLI both call this, and
they must not end up with two implementations that drift.
"""
import json
import logging
from pathlib import Path

from app.utils.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

PENDING_KEY = "batch_pending"
ATTEMPTS_KEY = "batch_attempts"
OPS_KEY = "batch_ops"
MAX_ATTEMPTS = 3

# The operations a batch run can perform, in the only order they may run in:
# a transcript before it can be diarized, a transcript before it can be
# summarized. "diarization" is shown to users as "Speaker Recognition".
OPS_ORDER = ("transcription", "diarization", "summarization")


def _canonical_ops(ops):
    """Filter to known ops, dedupe, and return them in OPS_ORDER.

    metadata.json is exactly the kind of file people hand-edit, so a
    misspelled or mis-ordered entry must degrade to "ignored" rather than
    reach the pipeline.
    """
    if not isinstance(ops, (list, tuple)):
        return []
    present = set(ops)
    return [op for op in OPS_ORDER if op in present]


def is_queued(metadata):
    """Whether this recording is tagged for batch processing."""
    if not isinstance(metadata, dict):
        return False
    # Not bool(...): a hand-edited "yes please" would read as queued, and a
    # metadata.json is exactly the kind of file people edit by hand.
    return metadata.get(PENDING_KEY) is True


def attempts(metadata):
    """How many times the batch runner has already failed on this one."""
    if not isinstance(metadata, dict):
        return 0
    value = metadata.get(ATTEMPTS_KEY, 0)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    return value


def exhausted(metadata):
    """Whether this recording has failed too often to be worth retrying."""
    return attempts(metadata) >= MAX_ATTEMPTS


def read_metadata(directory):
    """Load a recording's metadata.json, or None if it isn't usable.

    Recovered (crash-orphaned) recordings have no metadata.json at all, so
    a missing file is an ordinary outcome here, not an error.
    """
    path = Path(directory) / "metadata.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _update(directory, mutate):
    """Read metadata.json, apply mutate(dict), write it back atomically.

    Returns False rather than raising when the file is missing or corrupt:
    every caller is either a bulk GUI action over a multi-selection or the
    batch runner's own bookkeeping, and neither should abort the rest of
    its work because one folder is unreadable.
    """
    metadata = read_metadata(directory)
    if metadata is None:
        logger.warning("No usable metadata.json in %s — skipping batch tag update", directory)
        return False
    mutate(metadata)
    try:
        atomic_write_json(Path(directory) / "metadata.json", metadata, indent=2)
    except OSError:
        logger.exception("Failed to write metadata.json in %s", directory)
        return False
    return True


def queued_ops(metadata):
    """Canonical-ordered batch operations for this recording.

    ``[]`` when the recording is not queued. A legacy tag
    (``batch_pending: true`` with no ``batch_ops``) reads as
    ``["transcription"]``. Junk or mis-ordered ``batch_ops`` entries in a
    hand-edited file are filtered out and re-sorted.
    """
    if not is_queued(metadata):
        return []
    ops = _canonical_ops(metadata.get(OPS_KEY))
    return ops if ops else ["transcription"]


def set_queued(directory, queued):
    """Tag or untag a recording. Returns True when the write succeeded.

    Queuing this way means "just transcription" — the fine-grained op set
    goes through :func:`set_ops`.
    """
    def mutate(metadata):
        if queued:
            metadata[PENDING_KEY] = True
            metadata[OPS_KEY] = ["transcription"]
            # Re-queuing by hand is the user saying "try this again", so a
            # recording parked at the attempt limit becomes eligible.
            metadata.pop(ATTEMPTS_KEY, None)
        else:
            metadata.pop(PENDING_KEY, None)
            metadata.pop(OPS_KEY, None)

    return _update(directory, mutate)


def set_ops(directory, ops):
    """Set the exact operation set for a recording. Returns True on success.

    Non-empty ops also sets ``batch_pending`` and clears ``batch_attempts``
    (a re-queue is "try again"). Empty ops clears every batch key — that is
    "remove from the queue".
    """
    canonical = _canonical_ops(ops)

    def mutate(metadata):
        metadata.pop(ATTEMPTS_KEY, None)
        if canonical:
            metadata[OPS_KEY] = canonical
            metadata[PENDING_KEY] = True
        else:
            metadata.pop(OPS_KEY, None)
            metadata.pop(PENDING_KEY, None)

    return _update(directory, mutate)


def record_failure(directory):
    """Count one failed batch attempt. Returns the new total (0 if unwritable).

    The recording stays queued — the attempt limit, not the flag, is what
    eventually stops it being retried.
    """
    total = [0]

    def mutate(metadata):
        total[0] = attempts(metadata) + 1
        metadata[ATTEMPTS_KEY] = total[0]

    if not _update(directory, mutate):
        return 0
    return total[0]


def clear(directory):
    """Drop every batch key after a successful run. Returns True on success."""
    def mutate(metadata):
        metadata.pop(PENDING_KEY, None)
        metadata.pop(ATTEMPTS_KEY, None)
        metadata.pop(OPS_KEY, None)

    return _update(directory, mutate)
