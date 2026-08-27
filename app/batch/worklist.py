"""Deciding which recordings a batch run will process.

Pure filesystem inspection — no Qt, no model loading — so the whole
selection can be exercised in tests and printed by --dry-run without
touching Whisper.
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from app.utils.batch_queue import exhausted, is_queued, queued_ops, read_metadata

logger = logging.getLogger(__name__)

# The keys DualAudioCapture writes, in the order MainWindow prefers them
# when picking a single file to transcribe (app/main_window.py:1275).
_AUDIO_PREFERENCE = ("combined", "system", "mic")
_AUDIO_KEYS = ("mic", "mic2", "system", "combined")
_AUDIO_FILENAMES = {
    "combined": "combined_audio.wav",
    "system": "system_audio.wav",
    "mic": "mic_audio.wav",
}


@dataclass
class Job:
    """One recording to process, with everything the runner needs."""

    directory: str
    session: dict
    label: str
    audio_path: str          # None for a summarization-only job
    ops: list = field(default_factory=list)


def _rebase(path, directory):
    """Point a recorded absolute path back at the folder it now lives in.

    metadata.json stores absolute paths, and recordings live under
    Documents — a folder users move and OneDrive re-homes. The files sit
    right next to the metadata that names them, so the folder on disk is a
    better authority than the path baked in at record time.
    """
    if not path:
        return path
    return str(Path(directory) / Path(str(path)).name)


def _load_session(directory):
    """Read a recording's metadata and correct its paths, or None."""
    metadata = read_metadata(directory)
    if metadata is None:
        return None
    metadata["directory"] = str(directory)
    audio_files = metadata.get("audio_files")
    if isinstance(audio_files, dict):
        metadata["audio_files"] = {
            key: _rebase(value, directory) if key in _AUDIO_KEYS else value
            for key, value in audio_files.items()
        }
    return metadata


def _pick_audio(session):
    """The single file to transcribe, or None when nothing usable exists."""
    directory = session.get("directory", "")
    audio_files = session.get("audio_files") or {}
    for key in _AUDIO_PREFERENCE:
        path = audio_files.get(key)
        if path and os.path.exists(path):
            return path
    # Fall back to the conventional filenames. A metadata.json that never
    # got an audio_files block (an older layout, a hand-assembled folder,
    # a partially salvaged one) still has the tracks sitting right there
    # under the names DualAudioCapture always writes.
    for key in _AUDIO_PREFERENCE:
        candidate = Path(directory) / _AUDIO_FILENAMES[key]
        if candidate.exists():
            return str(candidate)
    return None


def build_worklist(recordings_dir, limit=None):
    """Every queued, still-processable recording, oldest first.

    Oldest first so a run that runs out of time has cleared the
    longest-waiting backlog rather than whatever arrived most recently.
    """
    root = Path(recordings_dir)
    if not root.is_dir():
        logger.warning("Recordings directory does not exist: %s", root)
        return []

    jobs = []
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        session = _load_session(directory)
        if session is None:
            continue
        if not is_queued(session):
            continue
        if exhausted(session):
            logger.info("Skipping %s — too many failed attempts", directory.name)
            continue

        ops = queued_ops(session)
        if not ops:
            continue

        label = session.get("name") or directory.name
        has_transcript = (directory / "transcript.json").exists()

        # A downstream op needs a transcript. If none exists and no
        # transcription is queued to produce one, that op can't run.
        if not has_transcript and "transcription" not in ops:
            kept = [op for op in ops if op == "transcription"]
            for dropped in ops:
                if dropped not in kept:
                    logger.info(
                        "%s: dropping %s — no transcript and transcription not queued",
                        label, dropped,
                    )
            ops = kept
            if not ops:
                continue

        # Audio is only the input for transcription and pyannote. A
        # summarization-only job runs off transcript.json alone.
        audio_path = _pick_audio(session)
        if ("transcription" in ops or "diarization" in ops) and not audio_path:
            logger.info("Skipping %s — no audio file on disk", directory.name)
            continue

        jobs.append(Job(
            directory=str(directory),
            session=session,
            label=label,
            audio_path=audio_path,
            ops=ops,
        ))

    # Folder names are recording_<timestamp>, so the sorted() above is
    # already chronological; slicing after the filter keeps --limit
    # meaning "this many processed", not "this many considered".
    return jobs[:limit] if limit else jobs
