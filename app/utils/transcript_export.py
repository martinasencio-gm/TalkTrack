"""Pure builder for the human/LLM-readable Markdown transcript export.

No Qt dependency — every input is a plain dict/string/list so this module
stays unit-testable without a QApplication. app/main_window.py is
responsible for reading the source JSON/text files from a recording's
directory and calling export_transcript() with the results.
"""
import os
from pathlib import Path

from app.utils.atomic_io import atomic_write_text

_MAX_TITLE_LEN = 60
_INVALID_CHARS = '\\/:*?"<>|'


def _format_time(seconds):
    """HH:MM:SS from a float seconds offset. Local copy — transcriber.py
    imports PyQt6, and this module must stay Qt-free."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def sanitize_filename_component(text):
    """Strip characters invalid in Windows filenames, collapse whitespace
    to single underscores, cap length. Empty/whitespace-only input becomes
    'Untitled' rather than an empty filename component."""
    text = text.strip()
    if not text:
        return "Untitled"
    cleaned = "".join(" " if c in _INVALID_CHARS else c for c in text)
    collapsed = "_".join(cleaned.split())
    return collapsed[:_MAX_TITLE_LEN] if collapsed else "Untitled"


def export_path_for(title, timestamp_iso, transcripts_dir):
    """<transcripts_dir>/<sanitized-title>_<YYYYMMDD>_<HHMM>.md

    Timestamp comes from the recording's started_at, not wall-clock export
    time, so re-exporting the same recording overwrites the same file
    instead of accumulating duplicates. A missing/unparseable timestamp
    falls back to an all-zero stamp rather than raising.
    """
    stamp = "00000000_0000"
    if timestamp_iso:
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(timestamp_iso)
            stamp = dt.strftime("%Y%m%d_%H%M")
        except ValueError:
            pass
    filename = f"{sanitize_filename_component(title)}_{stamp}.md"
    return Path(transcripts_dir) / filename


def _yaml_str(value):
    """Quote a string for a YAML scalar. Values here are display text, not
    attacker-controlled YAML syntax, so simple double-quoting (escaping only
    embedded double quotes/backslashes) is sufficient."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_export_markdown(metadata, transcript_data, speaker_names,
                           calendar_event, notes, summary_markdown, action_items):
    """Render the full Markdown+YAML-frontmatter export document."""
    directory_name = Path(metadata.get("directory", "")).name
    title = (calendar_event or {}).get("subject") or metadata.get("name") or directory_name

    lines = ["---"]
    lines.append(f"title: {_yaml_str(title)}")
    started_at = metadata.get("started_at", "")
    if started_at:
        lines.append(f"recording_date: {_yaml_str(started_at)}")
    duration = metadata.get("duration") or transcript_data.get("duration") or 0
    lines.append(f"duration_seconds: {int(duration)}")
    lines.append(f"source_directory: {_yaml_str(directory_name)}")

    if calendar_event:
        lines.append("calendar:")
        lines.append(f"  subject: {_yaml_str(calendar_event.get('subject', ''))}")
        organizer = calendar_event.get("organizer", "")
        if organizer:
            lines.append(f"  organizer: {_yaml_str(organizer)}")
        attendees = calendar_event.get("attendees", [])
        if attendees:
            lines.append("  attendees:")
            for name in attendees:
                lines.append(f"    - {_yaml_str(name)}")

    if speaker_names:
        lines.append("speakers:")
        for speaker_id, name in speaker_names.items():
            lines.append(f"  {speaker_id}: {_yaml_str(name)}")

    lines.append("---")
    lines.append("")

    if summary_markdown:
        lines.append("# Summary")
        lines.append("")
        lines.append(summary_markdown.strip())
        lines.append("")

    if action_items:
        lines.append("# Action Items")
        lines.append("")
        for item in action_items:
            task = (item.get("task") or "").strip()
            if not task:
                continue
            assignee = (item.get("assignee") or "").strip()
            due = (item.get("due") or "").strip()
            entry = f"{assignee}: {task}" if assignee else task
            if due:
                entry += f" (due {due})"
            lines.append(f"- [ ] {entry}")
        lines.append("")

    if notes and notes.strip():
        lines.append("# Notes")
        lines.append("")
        lines.append(notes.strip())
        lines.append("")

    lines.append("# Transcript")
    lines.append("")
    for seg in transcript_data.get("segments", []):
        speaker_id = seg.get("speaker", "")
        display = speaker_names.get(speaker_id, speaker_id) if speaker_id else ""
        timestamp = _format_time(seg.get("start", 0))
        prefix = f"**[{timestamp}] {display}:**" if display else f"**[{timestamp}]**"
        lines.append(f"{prefix} {seg.get('text', '').strip()}")

    return "\n".join(lines) + "\n"


def export_transcript(metadata, transcript_data, speaker_names, calendar_event,
                       notes, summary_markdown, action_items, transcripts_dir):
    """Build and write the export file. Best-effort: every failure is
    swallowed after logging, never raised into the caller — this is a
    convenience copy, not the app's source of truth for the transcript."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        os.makedirs(transcripts_dir, exist_ok=True)
        directory_name = Path(metadata.get("directory", "")).name
        title = (calendar_event or {}).get("subject") or metadata.get("name") or directory_name
        path = export_path_for(title, metadata.get("started_at", ""), transcripts_dir)
        markdown = build_export_markdown(
            metadata, transcript_data, speaker_names, calendar_event,
            notes, summary_markdown, action_items,
        )
        atomic_write_text(path, markdown)
    except (OSError, TypeError, AttributeError, KeyError):
        logger.exception("Failed to export transcript for %s", metadata.get("directory"))
