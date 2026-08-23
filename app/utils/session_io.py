"""Reading and writing a recording session's files on disk.

Everything here is disk-driven and Qt-free: give it a session dict (the
contents of that recording's metadata.json, with a "directory" key) and it
reads or writes that folder. Nothing consults the UI.

Split out of MainWindow so the headless batch runner writes byte-identical
output to the app — a second implementation would drift, and transcript.md
in particular is consumed by tooling that shouldn't have to cope with two
dialects.
"""
import json
import logging
from pathlib import Path

from app.utils import transcript_export
from app.utils.atomic_io import atomic_write_json, atomic_write_text

logger = logging.getLogger(__name__)


def _read_json(path):
    """Parse a JSON file, or None if it's missing or unusable.

    Every optional companion file (speaker names, calendar tag, action
    items) is genuinely optional, and a corrupt one must degrade that one
    section rather than fail the whole write.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def load_speaker_names(session, config=None):
    """The recording's speaker-ID → friendly-name map ({} when unset)."""
    directory = session.get("directory") if session else None
    if not directory:
        names = {}
    else:
        raw = _read_json(Path(directory) / "speaker_names.json")
        names = raw if isinstance(raw, dict) else {}

    if config:
        try:
            if config.get("general", "replace_you_with_name") and "You" not in names:
                from app.utils.platform_info import get_current_user_name
                user_name = get_current_user_name(config)
                if user_name and user_name.strip() and user_name.strip().lower() != "you":
                    names["You"] = user_name.strip()
        except Exception:
            pass

    return names


def load_calendar_event(session):
    """Load calendar_event.json for a session, if present.

    Returns (calendar_event: dict|None, attendees: list[str]).
    """
    directory = session.get("directory") if session else None
    if not directory:
        return None, []
    event = _read_json(Path(directory) / "calendar_event.json")
    if not isinstance(event, dict):
        return None, []
    attendees = event.get("attendees", [])
    return event, attendees if isinstance(attendees, list) else []


def write_transcript(session, result, speaker_names=None):
    """Write transcript.json + transcript.txt, then refresh transcript.md.

    Returns True on success. Speaker names default to whatever is already
    saved for the recording, which is what a background writer wants — the
    caller only passes them explicitly when it holds newer ones the user
    has just edited.
    """
    directory = session.get("directory") if session else None
    if not directory:
        return False
    directory = Path(directory)
    names = speaker_names if speaker_names is not None else load_speaker_names(session)
    try:
        atomic_write_json(directory / "transcript.json",
                          result.to_dict(speaker_names=names),
                          indent=2, ensure_ascii=False)
        atomic_write_text(directory / "transcript.txt",
                          result.to_text(speaker_names=names))
        if names:
            atomic_write_json(directory / "speaker_names.json",
                              names, indent=2, ensure_ascii=False)
    except OSError:
        logger.exception("Failed to write transcript for %s", directory)
        return False

    export_session_markdown(session)
    return True


def load_tags(session):
    """The recording's tag list ([] when unset)."""
    if not session:
        return []
    tags = session.get("tags")
    if isinstance(tags, list):
        return [t for t in tags if isinstance(t, str)]
    directory = session.get("directory")
    if directory:
        meta = _read_json(Path(directory) / "metadata.json")
        if isinstance(meta, dict):
            tags = meta.get("tags")
            if isinstance(tags, list):
                return [t for t in tags if isinstance(t, str)]
    return []


def export_session_markdown(session):
    """Best-effort LLM-readable transcript.md for a session.

    Reads everything fresh from disk rather than taking it from the
    caller: this also runs for recordings that are not the one on screen,
    where the widgets hold someone else's data entirely.
    """
    directory = session.get("directory") if session else None
    if not directory:
        return
    directory = Path(directory)

    transcript_data = _read_json(directory / "transcript.json")
    if transcript_data is None:
        return  # nothing transcribed yet — nothing useful to export

    meta = _read_json(directory / "metadata.json") or dict(session)
    if not meta.get("directory"):
        meta["directory"] = str(directory)

    speaker_names = load_speaker_names(session)
    calendar_event, _ = load_calendar_event(session)
    notes = _read_text(directory / "notes.txt") or ""
    summary_markdown = _read_text(directory / "summary.md")
    action_items = _read_json(directory / "action_items.json")

    transcript_export.export_transcript(
        meta, transcript_data, speaker_names, calendar_event,
        notes, summary_markdown, action_items,
    )
