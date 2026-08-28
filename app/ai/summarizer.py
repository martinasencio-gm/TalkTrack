"""Meeting summary generation.

The summary is a single markdown document that ends with a ``## Action Items``
section — action items are not a separate structured artifact.
"""


_TRUNCATION_MARKER = "\n[... transcript truncated to fit the model's context ...]\n"


def truncate_transcript(text, max_chars):
    """Cap transcript text, keeping the head and tail.

    Endings matter (action items and decisions cluster late in meetings), so
    a head-only cut like the chat panel's would drop exactly the part the
    action-items section needs. 60/40 head/tail split.
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
    """Prompt for one markdown summary that ends with a ``## Action Items`` section.

    The whole response is used verbatim as ``summary.md`` — there is no
    separate action-items payload to parse.
    """
    transcript_text = _format_transcript(segments, speaker_names,
                                         max_chars=max_transcript_chars)
    notes_text = _format_notes(notes)
    instruction_text = _format_instruction(instruction)
    return (
        "Below is a transcript of a meeting. Write a concise summary as markdown "
        "with bullet points, covering: key discussion points, decisions made, "
        "and outcomes.\n\n"
        "End the summary with a section headed exactly:\n"
        "## Action Items\n"
        "List every action item — task, follow-up, or commitment made by a "
        "participant — as a bullet in the form:\n"
        "- **Owner:** the task (deadline, if one was mentioned)\n"
        "Use `_None._` as the only line under that heading if there are no "
        "action items.\n\n"
        "If user notes are included, incorporate relevant context from them into "
        "the summary and include any action items they contain.\n\n"
        "If additional instructions are provided, follow them.\n\n"
        "Return only the summary markdown, no preamble.\n\n"
        f"TRANSCRIPT:\n{transcript_text}{notes_text}{instruction_text}"
    )
