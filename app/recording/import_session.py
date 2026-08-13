"""Pure logic for constructing an imported recording's session metadata.

The actual file I/O (copy, ffmpeg conversion, duration probing) lives in
main_window.py's import flow, mirroring Recorder._convert_to_mp3's inline
subprocess style rather than introducing a second I/O abstraction.
"""
from pathlib import Path
from datetime import timedelta


def needs_conversion(source_path):
    """True if the source file must be converted to WAV before use."""
    return Path(source_path).suffix.lower() == ".m4a"


def build_import_metadata(source_path, session_dir, started_at, duration, audio_filename):
    """Build the metadata.json dict for a newly-imported recording session.

    Args:
        source_path: original file path the user picked (for source_filename).
        session_dir: the new session directory (str).
        started_at: user-confirmed recording start (datetime).
        duration: probed audio duration in seconds (float).
        audio_filename: filename of the (possibly converted) audio file
            written into session_dir, e.g. "combined_audio.wav".
    """
    stopped_at = started_at + timedelta(seconds=duration)
    session_dir_str = str(session_dir).replace("\\", "/")
    audio_path = f"{session_dir_str}/{audio_filename}"
    return {
        "directory": session_dir_str,
        "started_at": started_at.isoformat(),
        "stopped_at": stopped_at.isoformat(),
        "duration": duration,
        "audio_files": {"combined": audio_path},
        "imported": True,
        "source_filename": Path(source_path).name,
        "capture_mode": "imported",
    }
