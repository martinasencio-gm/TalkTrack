"""Pure builder for the human/LLM-readable Markdown transcript export.

No Qt dependency — every input is a plain dict/string/list so this module
stays unit-testable without a QApplication. app/main_window.py is
responsible for reading the source JSON/text files from a recording's
directory and calling export_transcript() with the results.
"""
from pathlib import Path

from app.utils.atomic_io import atomic_write_text


def _format_time(seconds):
    """HH:MM:SS from a float seconds offset. Local copy — transcriber.py
    imports PyQt6, and this module must stay Qt-free."""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _as_number(value):
    """The value if it is a real number, else None.

    bool is excluded explicitly: it is an int subclass in Python, so a stray
    True would otherwise sail through as 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _format_confidence(value):
    """Two-decimal confidence string, or None when there is nothing usable
    to render. Measured confidences here cluster in 0.65-0.79, so two
    decimals is the resolution that actually distinguishes segments."""
    number = _as_number(value)
    return None if number is None else f"{number:.2f}"


def _segment_timestamp(seg):
    """HH:MM:SS, or an HH:MM:SS–HH:MM:SS range when the segment carries a
    usable end.

    A missing, non-numeric, or non-advancing end falls back to the single
    timestamp: a backwards or zero-length range in the corpus would be worse
    than no range at all. Transcripts produced before end was exported hit
    this path and render exactly as they used to.
    """
    start = _as_number(seg.get("start")) or 0
    end = _as_number(seg.get("end"))
    if end is None or end <= start:
        return _format_time(start)
    return f"{_format_time(start)}–{_format_time(end)}"


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
    # Provenance, not content: model_size is what tells a later reader how
    # much to trust this transcript once the audio it came from is gone.
    # transcribe_seconds is deliberately not exported — it describes the
    # machine that ran the transcription, not the transcript.
    language = transcript_data.get("language")
    if language:
        lines.append(f"language: {_yaml_str(str(language))}")
    model_size = transcript_data.get("model_size")
    if model_size:
        lines.append(f"model_size: {_yaml_str(str(model_size))}")
    lines.append(f"source_directory: {_yaml_str(directory_name)}")

    if calendar_event:
        lines.append("calendar:")
        lines.append(f"  subject: {_yaml_str(calendar_event.get('subject') or '')}")
        organizer = calendar_event.get("organizer") or ""
        if organizer:
            lines.append(f"  organizer: {_yaml_str(organizer)}")
        attendees = calendar_event.get("attendees") or []
        if attendees:
            lines.append("  attendees:")
            for name in attendees:
                lines.append(f"    - {_yaml_str(name or '')}")

    if speaker_names:
        lines.append("speakers:")
        for speaker_id, name in speaker_names.items():
            lines.append(f"  {speaker_id}: {_yaml_str(name or '')}")

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
        parts = [f"[{_segment_timestamp(seg)}]"]
        if display:
            parts.append(display)
        confidence = _format_confidence(seg.get("confidence"))
        if confidence:
            parts.append(f"({confidence})")
        # The colon reads as "<speaker> said:" — it only earns its place
        # when a speaker is actually named.
        suffix = ":**" if display else "**"
        lines.append(f"**{' '.join(parts)}{suffix} {seg.get('text', '').strip()}")

    return "\n".join(lines) + "\n"


def has_exportable_content(transcript_data):
    """Whether this transcript is worth writing to the corpus at all.

    No segments means nothing was heard — the export would be frontmatter
    and an empty '# Transcript' heading. build_export_markdown deliberately
    does NOT consult this: it is a pure builder, and whether a document is
    worth writing is policy that belongs at the write site.
    """
    return bool((transcript_data or {}).get("segments"))


def export_transcript(metadata, transcript_data, speaker_names, calendar_event,
                       notes, summary_markdown, action_items):
    """Build and write transcript.md into the recording's own session
    directory, alongside transcript.json. Best-effort: every failure is
    swallowed after logging, never raised into the caller."""
    import logging
    logger = logging.getLogger(__name__)
    try:
        # Inside the try on purpose: a malformed transcript_data reaches
        # .get() here, and the existing handler is what absorbs it.
        if not has_exportable_content(transcript_data):
            logger.info(
                "Skipping transcript export for %s — no segments",
                metadata.get("directory"),
            )
            return
        directory = metadata.get("directory", "")
        if not directory:
            # No real session folder to write into — a relative "" would
            # resolve to transcript.md in the process's cwd, which is never
            # what's wanted.
            logger.info("Skipping transcript export — metadata has no directory")
            return
        path = Path(directory) / "transcript.md"
        markdown = build_export_markdown(
            metadata, transcript_data, speaker_names, calendar_event,
            notes, summary_markdown, action_items,
        )
        atomic_write_text(path, markdown)
    except (OSError, TypeError, AttributeError, KeyError):
        logger.exception("Failed to export transcript for %s", metadata.get("directory"))
