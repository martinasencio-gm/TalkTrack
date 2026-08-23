"""Pure computation of the pre-flight verdict shown on the capture bar before
recording starts. No Qt — app/ui/preflight.py renders whatever this returns.

Folds in what used to be three separate widgets: the conferencing-app
opt-out warning, the mic/output device-mismatch banners (both in
source_selector.py), the "quiet mic" level check (see
app/utils/mic_level_tracker.py), and the implicit "is transcription/
diarization even possible" state nobody surfaced anywhere.
"""

READY = "ready"
WARNING = "warning"
BLOCKED = "blocked"

_SEVERITY = {READY: 0, WARNING: 1, BLOCKED: 2}

# A mic peaking below this over the trailing window is "very quiet" — picked
# to sit clearly below normal speech (typically -20 to -10 dBFS peak) while
# still catching a genuinely wrong/muted/far-away mic before the user finds
# out from an empty transcript.
QUIET_MIC_THRESHOLD_DB = -40.0


def compute_mic_check(has_mic, mic_mismatch, mic_peak_db=None, mic_name="Your mic"):
    """'YOUR VOICE' check -> (status, title, subtitle).

    `mic_peak_db` is the loudest peak seen over the trailing window (see
    MicLevelTracker.peak_db_over_window) — None means not enough audio has
    arrived yet to judge, so it must never itself trigger a warning.
    """
    if not has_mic:
        return BLOCKED, "No microphone selected", "Pick a mic in Sources before you record"
    if mic_mismatch:
        return (
            WARNING,
            f'{mic_mismatch["app"]} is using a different mic',
            "Close it or switch devices before you record",
        )
    if mic_peak_db is not None and mic_peak_db < QUIET_MIC_THRESHOLD_DB:
        return (
            WARNING,
            "Mic is very quiet",
            f"{mic_name} peaking at {mic_peak_db:.0f} dB — check it before you record",
        )
    return READY, "Ready", "Microphone ready"


def compute_call_check(has_source, conferencing_blocked, output_mismatch):
    """'THE CALL' check -> (status, title, subtitle)."""
    if conferencing_blocked:
        return (
            BLOCKED,
            "This app blocks per-app capture",
            "Switch to all system audio or the call will record silent",
        )
    if not has_source:
        return (
            WARNING,
            "No app or system audio selected",
            "Check your capture sources before you record",
        )
    if output_mismatch:
        return (
            WARNING,
            f'{output_mismatch["app"]} is outputting elsewhere',
            "Check your capture sources before you record",
        )
    return READY, "Ready", "System audio ready"


def compute_transcription_check(diarization_enabled, hf_token_present):
    """'TRANSCRIPTION' check -> (status, title, subtitle)."""
    if diarization_enabled and not hf_token_present:
        return (
            WARNING,
            "Speaker ID needs a HuggingFace token",
            "Add a HuggingFace token in Settings, or turn off Identify speakers",
        )
    return READY, "Ready", "Transcription ready"


def compute_verdict(mic_check, call_check, model_check):
    """Worst-of-three -> (verdict, title, subtitle).

    Each argument is a (status, title, subtitle) tuple from one of the
    compute_*_check functions above. The verdict's title is always the
    losing check's own title (e.g. "This app blocks per-app capture"),
    never a generic severity wrapper — the bar should name the actual
    problem, not just how bad it is.
    """
    checks = [("mic", *mic_check), ("call", *call_check), ("model", *model_check)]
    _, status, title, subtitle = max(checks, key=lambda c: _SEVERITY[c[1]])
    if status == READY:
        return READY, "Ready to record", "Microphone and system audio ready"
    return status, title, subtitle
