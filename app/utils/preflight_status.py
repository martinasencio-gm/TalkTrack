"""Pure computation of the pre-flight verdict shown on the capture bar before
recording starts. No Qt — app/ui/preflight.py renders whatever this returns.

Folds in what used to be three separate widgets: the conferencing-app
opt-out warning, the mic/output device-mismatch banners (both in
source_selector.py), and the implicit "is transcription/diarization even
possible" state nobody surfaced anywhere.
"""

READY = "ready"
WARNING = "warning"
BLOCKED = "blocked"

_SEVERITY = {READY: 0, WARNING: 1, BLOCKED: 2}


def compute_mic_check(has_mic, mic_mismatch):
    """'YOUR VOICE' check -> (status, text)."""
    if not has_mic:
        return BLOCKED, "No microphone selected"
    if mic_mismatch:
        return WARNING, f'{mic_mismatch["app"]} is using a different mic'
    return READY, "Ready"


def compute_call_check(has_source, conferencing_blocked, output_mismatch):
    """'THE CALL' check -> (status, text)."""
    if conferencing_blocked:
        return BLOCKED, "This app blocks per-app capture — switch to all system audio"
    if not has_source:
        return WARNING, "No app or system audio selected"
    if output_mismatch:
        return WARNING, f'{output_mismatch["app"]} is outputting elsewhere'
    return READY, "Ready"


def compute_transcription_check(diarization_enabled, hf_token_present):
    """'TRANSCRIPTION' check -> (status, text)."""
    if diarization_enabled and not hf_token_present:
        return WARNING, "Speaker ID needs a HuggingFace token"
    return READY, "Ready"


def compute_verdict(mic_status, call_status, model_status):
    """Worst-of-three -> (verdict, title, subtitle)."""
    worst = max((mic_status, call_status, model_status), key=lambda s: _SEVERITY[s])
    if worst == BLOCKED:
        return BLOCKED, "Recording will be silent", "Fix the flagged item below before you start"
    if worst == WARNING:
        return WARNING, "Ready with a warning", "Check the flagged item below"
    return READY, "Ready to record", "Microphone and system audio ready"
