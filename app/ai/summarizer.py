"""Meeting summary and action item extraction."""

import json
from app.transcription.transcriber import TranscriptSegment


_TRUNCATION_MARKER = "\n[... transcript truncated to fit the model's context ...]\n"

# Separates the markdown summary from the trailing action-items JSON array in a
# single combined response. Its own line, matched after stripping whitespace.
ACTION_ITEMS_DELIMITER = "===ACTION_ITEMS_JSON==="


def truncate_transcript(text, max_chars):
    """Cap transcript text, keeping the head and tail.

    Endings matter (action items and decisions cluster late in meetings), so
    a head-only cut like the chat panel's would drop exactly the part the
    action-item prompt needs. 60/40 head/tail split.
    """
    if max_chars is None or len(text) <= max_chars:
        return text
    budget = max_chars - len(_TRUNCATION_MARKER)
    head = int(budget * 0.6)
    tail = budget - head
    return text[:head] + _TRUNCATION_MARKER + text[-tail:]


def _format_transcript(segments, speaker_names, max_chars=None):
    lines = []
    for seg in segments:
        name = speaker_names.get(seg.speaker, seg.speaker) if seg.speaker else "Unknown"
        timestamp = f"[{seg.start:.1f}s]"
        lines.append(f"{timestamp} {name}: {seg.text}")
    return truncate_transcript("\n".join(lines), max_chars)


def _format_notes(notes):
    if not notes or not notes.strip():
        return ""
    return f"\n\nUSER NOTES (taken during the meeting):\n{notes.strip()}"


def _format_instruction(instruction):
    if not instruction or not instruction.strip():
        return ""
    return f"\n\nADDITIONAL INSTRUCTIONS FROM USER:\n{instruction.strip()}"


def build_summary_prompt(segments, speaker_names, notes="", instruction="",
                         max_transcript_chars=None):
    """One prompt for both the summary and the action items.

    The response is expected as: the markdown summary, then a line containing
    only ``ACTION_ITEMS_DELIMITER``, then a JSON array of action items. Split it
    with :func:`split_summary_response`.
    """
    transcript_text = _format_transcript(segments, speaker_names,
                                         max_chars=max_transcript_chars)
    notes_text = _format_notes(notes)
    instruction_text = _format_instruction(instruction)
    return (
        "Below is a transcript of a meeting. Produce two things in one response.\n\n"
        "1. A concise summary covering: key discussion points, decisions made, "
        "and outcomes. Format it as markdown with bullet points.\n\n"
        "2. All action items — tasks, follow-ups, or commitments made by "
        "participants — as a JSON array where each item has:\n"
        '   - "task": description of the action item\n'
        '   - "assignee": who is responsible (speaker name)\n'
        '   - "deadline": mentioned deadline or empty string\n\n'
        "If user notes are included, incorporate relevant context from them into "
        "the summary and extract any action items they contain.\n\n"
        "If additional instructions are provided, follow them for both parts.\n\n"
        "Output the summary first. Then output a line containing exactly:\n"
        f"{ACTION_ITEMS_DELIMITER}\n"
        "Then output only the JSON array (use [] if there are none). Write "
        "nothing after the array.\n\n"
        f"TRANSCRIPT:\n{transcript_text}{notes_text}{instruction_text}"
    )


def split_summary_response(response):
    """Split a combined response into ``(summary_markdown, action_items)``.

    The summary is everything before the last ``ACTION_ITEMS_DELIMITER`` line;
    the action items are parsed from whatever follows it via
    :func:`parse_action_items`. A missing delimiter yields the whole response as
    the summary and ``[]``; garbage after the delimiter yields the pre-delimiter
    summary and ``[]``. Never raises.
    """
    text = (response or "").strip()
    lines = text.split("\n")
    cut = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == ACTION_ITEMS_DELIMITER:
            cut = i
            break
    if cut is None:
        return text, []
    summary = "\n".join(lines[:cut]).strip()
    tail = "\n".join(lines[cut + 1:])
    return summary, parse_action_items(tail)


def parse_action_items(response):
    text = response.strip()
    # Models wrap the array in fences or prose; extract the outermost [...].
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        text = text[start:end]
    try:
        items = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(items, list):
        return []
    cleaned = []
    for item in items:
        if not isinstance(item, dict) or not item.get("task"):
            continue
        cleaned.append({
            "task": str(item.get("task", "")),
            "assignee": str(item.get("assignee") or ""),
            "deadline": str(item.get("deadline") or ""),
        })
    return cleaned
