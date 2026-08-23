"""Pure detection of mismatches between what a conferencing app is actually
using and what TalkTrack has selected. No Qt — feeds app/ui/preflight.py via
SourceSelector.check_device_mismatches.
"""
from app.utils.audio_devices import device_names_match

_CONFERENCING_PROCESS_NAMES = {
    "ms-teams", "teams", "zoom", "webex", "ciscocollabhost",
    "slack", "discord", "gotomeeting",
}


def find_active_conferencing_app(app_devices, conferencing_app_names):
    """Return the conferencing app actually using a mic or output, or None.

    app_devices: {key: {"app": str, "mic": str|None, "output": str|None,
                         "process_name": str, "pids": [int]}}
    conferencing_app_names: display names considered conferencing apps
                             (e.g. source_selector.CONFERENCING_APPS).
    Never falls back to arbitrary background processes (e.g. M365Copilot).
    """
    conferencing_lower = {a.lower() for a in conferencing_app_names}
    for app_info in (app_devices or {}).values():
        proc_base = (app_info.get("process_name") or "").lower()
        if proc_base.endswith(".exe"):
            proc_base = proc_base[:-4]
        app_disp = (app_info.get("app") or "").lower()
        if app_disp in conferencing_lower or proc_base in _CONFERENCING_PROCESS_NAMES:
            if app_info.get("mic") or app_info.get("output"):
                return app_info
    return None


def compute_device_mismatches(
    current_mic_name, current_output_name, output_check_active,
    app_devices, conferencing_app_names,
):
    """Compare the active conferencing app's devices against the current
    selection.

    output_check_active: only meaningful when device-level (legacy) loopback
        is actually capturing system audio — per-app capture taps the app's
        own stream directly, so an output mismatch there is not a problem.

    Returns {"mic": {"app": str, "device": str} | None,
             "output": {"app": str, "device": str} | None}.
    """
    target = find_active_conferencing_app(app_devices, conferencing_app_names)
    if not target:
        return {"mic": None, "output": None}

    app_name = target.get("app", "Meeting app")
    result = {"mic": None, "output": None}

    app_mic = target.get("mic")
    if app_mic and not device_names_match(current_mic_name or "", app_mic):
        result["mic"] = {"app": app_name, "device": app_mic}

    app_output = target.get("output")
    if output_check_active and app_output and not device_names_match(current_output_name or "", app_output):
        result["output"] = {"app": app_name, "device": app_output}

    return result
