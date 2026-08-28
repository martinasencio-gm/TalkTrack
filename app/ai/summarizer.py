"""Meeting summary and action item extraction."""

import json
import re
from app.transcription.transcriber import TranscriptSegment


_TRUNCATION_MARKER = "\n[... transcript truncated to fit the model's context ...]\n"


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


def build_combined_prompt(segments, speaker_names, notes="", instruction="",
                          max_transcript_chars=None):
    """Build a single prompt asking for both the summary and action items.

    One response instead of two means the transcript is uploaded once and the
    call takes one round-trip instead of two.
    """
    transcript_text = _format_transcript(segments, speaker_names,
                                         max_chars=max_transcript_chars)
    notes_text = _format_notes(notes)
    instruction_text = _format_instruction(instruction)
    return (
        "Below is a transcript of a meeting. Do two things:\n\n"
        "1. Write a concise summary covering key discussion points, decisions "
        "made, and outcomes. Format it as markdown with bullet points. Do not "
        "include an \"Action Items\" section in the summary — that's handled "
        "by part 2.\n\n"
        "2. Extract all action items — tasks, follow-ups, or commitments made "
        "by participants — as a JSON array where each item has:\n"
        '   - "task": description of the action item\n'
        '   - "assignee": who is responsible (speaker name)\n'
        '   - "deadline": mentioned deadline or empty string\n\n'
        "If user notes are included, incorporate relevant context from them "
        "into the summary and extract any action items from them too.\n\n"
        "If additional instructions are provided, follow them for both parts.\n\n"
        "Respond with the markdown summary, followed by the JSON array in a "
        "```json fenced code block, and nothing after it. Use an empty array "
        "if there are no action items.\n\n"
        f"TRANSCRIPT:\n{transcript_text}{notes_text}{instruction_text}"
    )


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


def parse_combined_response(response):
    """Split a build_combined_prompt response into (summary_text, action_items).

    Looks for a ```json fenced array first (what the prompt asks for), then
    falls back to a bare trailing [...] array. Everything before the array is
    the summary. No array found -> the whole response is the summary.
    """
    text = response.strip()
    match = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if match:
        json_text = match.group(1)
        summary = text[:match.start()].rstrip()
    else:
        start = text.rfind("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            json_text = text[start:end + 1]
            summary = text[:start].rstrip()
        else:
            return text, []
    items = parse_action_items(json_text)
    return summary, items
