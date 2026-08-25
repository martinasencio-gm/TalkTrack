"""Probe Windows for evidence that a meeting is underway.

Reports facts only - every decision lives in meeting_detector. The individual
probes are injectable so the whole module is testable without COM, without
Windows, and without a real meeting.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

# Window titles that indicate a live call. Deliberately focused on active call
# patterns across Teams, Zoom, Webex, and calendar-style windows.
_MEETING_TITLE_MARKERS = (
    "zoom meeting",
    "zoom workplace",
    "zoom",
    "meeting with",
    "| microsoft teams",
    "| teams",
    "- microsoft teams",
    "- teams",
    "microsoft teams, meeting window",
    "microsoft teams call",
    "microsoft teams meeting",
    "microsoft teams",
    "| webex",
    "- webex",
    "cisco webex",
    "google meet",
)

# IAudioSessionControl::GetState -> AudioSessionStateActive
_SESSION_ACTIVE = 1


def parse_meeting_title(title: str) -> str | None:
    """Extract a clean person or meeting name from a conferencing window title.

    Returns None if the title is generic (e.g. "Microsoft Teams", "Zoom Meeting")
    or cannot be meaningfully parsed.
    """
    if not title or not isinstance(title, str):
        return None

    t = title.strip()
    lower = t.lower()

    # Teams patterns:
    # "Jane Doe | Microsoft Teams, meeting window"
    # "Jane Doe | Microsoft Teams call"
    # "Jane Doe | Microsoft Teams"
    # "Chat | Jane Doe | Microsoft Teams"
    # "Sprint Planning | Microsoft Teams"
    # "Sprint Planning - Microsoft Teams"
    if "microsoft teams" in lower or "| teams" in lower or "- teams" in lower:
        clean_t = t
        for suffix in [", meeting window", " call", " meeting"]:
            if clean_t.lower().endswith(suffix):
                clean_t = clean_t[:-len(suffix)].strip()
        # Split on | or -
        parts = []
        for segment in clean_t.replace(" - ", " | ").split("|"):
            if segment.strip():
                parts.append(segment.strip())
        generic_markers = {
            "microsoft teams", "teams", "chat", "meeting", "calls", "call"
        }
        meaningful = [
            p for p in parts
            if p.lower() not in generic_markers and not p.lower().startswith("microsoft teams")
        ]
        if meaningful:
            return meaningful[0]
        return None

    # Zoom patterns:
    # "Sprint Planning - Zoom"
    # "Zoom Meeting - Sprint Planning"
    # "Zoom Workplace - Sprint Planning"
    if "zoom" in lower:
        parts = [p.strip() for p in t.split("-") if p.strip()]
        generic = {"zoom", "zoom meeting", "zoom workplace", "zoom cloud meetings"}
        meaningful = [
            p for p in parts
            if p.lower() not in generic and not p.lower().startswith("zoom")
        ]
        if meaningful:
            return meaningful[0]
        return None

    # Webex patterns:
    # "Sprint Planning | Webex"
    if "webex" in lower:
        parts = [p.strip() for p in t.split("|") if p.strip()]
        generic = {"webex", "cisco webex", "cisco webex meetings"}
        meaningful = [
            p for p in parts
            if p.lower() not in generic and not p.lower().startswith("webex") and not p.lower().startswith("cisco webex")
        ]
        if meaningful:
            return meaningful[0]
        return None

    # "Meeting with Jane Doe" -> "Jane Doe"
    if lower.startswith("meeting with "):
        candidate = t[len("meeting with "):].strip()
        return candidate if candidate else None

    return None


def _base_name(process_name):
    if process_name.lower().endswith(".exe"):
        return process_name[:-4]
    return process_name


def is_meeting_app(process_name, apps):
    """True if process_name is one of the configured meeting apps."""
    def normalize(name):
        return _base_name(name).lower().replace("-", "").replace("_", "")

    proc_norm = normalize(process_name)
    for a in apps:
        app_norm = normalize(a)
        if proc_norm == app_norm:
            return True
        if proc_norm in ("msteams", "teams") and app_norm in ("msteams", "teams", "msteamsprocess"):
            return True
    return False


def get_mic_capture_pids(exclude_pid=None):
    """PIDs holding an ACTIVE capture session on ANY capture device.

    Two details, both established by the spike and both load-bearing:

    * Any-device, not the default device. A process can be ACTIVE on one capture
      endpoint while INACTIVE on another - ms-teams was observed doing exactly
      that - so a default-device check misses live calls.
    * exclude_pid drops our own process. TalkTrack's idle MicMonitor holds a
      capture session of its own, and without this the app detects itself as a
      meeting the moment it starts.
    """
    import comtypes
    from comtypes import CLSCTX_ALL, POINTER, cast
    from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow
    from pycaw.pycaw import (IAudioSessionControl2, IAudioSessionManager2,
                             IMMDeviceEnumerator)

    pids = set()
    enumerator = comtypes.CoCreateInstance(
        CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    collection = enumerator.EnumAudioEndpoints(
        EDataFlow.eCapture.value, DEVICE_STATE.ACTIVE.value)
    for i in range(collection.GetCount()):
        device = manager = sessions = None
        try:
            device = collection.Item(i)
            manager = cast(
                device.Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None),
                POINTER(IAudioSessionManager2))
            sessions = manager.GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                control = None
                try:
                    control = sessions.GetSession(j)
                    if control.GetState() != _SESSION_ACTIVE:
                        continue
                    pid = control.QueryInterface(IAudioSessionControl2).GetProcessId()
                    if pid and pid != exclude_pid:
                        pids.add(pid)
                except Exception:
                    # A session can vanish mid-enumeration (a participant's
                    # audio session tears down while we're reading it). The
                    # COM proxy is then a dangling pointer - dropping our
                    # reference now, instead of letting the GC finalize it
                    # later on an unrelated call stack, avoids releasing a
                    # VTable that is no longer valid.
                    continue
                finally:
                    del control
        except Exception:
            continue  # one bad endpoint must not lose the others
        finally:
            del sessions, manager, device
    return pids


def _default_audio_apps():
    from app.utils.audio_session_monitor import get_active_audio_apps
    return get_active_audio_apps()


def _default_mic_pids():
    return get_mic_capture_pids(exclude_pid=os.getpid())


def _default_pid_names(pids):
    import psutil

    names = {}
    for pid in pids:
        try:
            names[pid] = psutil.Process(pid).name()
        except Exception:
            continue
    return names


def _default_titles():
    import win32gui

    titles = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            text = win32gui.GetWindowText(hwnd)
            if text and any(m in text.lower() for m in _MEETING_TITLE_MARKERS):
                titles.append(text)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(callback, None)
    except Exception:
        pass
    return titles


def _safe(fn, default, label):
    try:
        return fn()
    except Exception as exc:
        logger.debug("meeting signal probe %s failed: %s", label, exc)
        return default


def probe(settings, calendar_event=None, now=None,
          _audio_apps_fn=None, _mic_pids_fn=None, _pid_names_fn=None,
          _titles_fn=None):
    """Return a signal snapshot. Never raises.

    Each probe is wrapped individually: a signal that fails contributes nothing
    and the rest of the snapshot still arrives. A detection feature that goes
    dark because one COM call threw would be worse than one that degrades.
    """
    apps = settings.get("apps", [])
    audio_apps_fn = _audio_apps_fn or _default_audio_apps
    mic_pids_fn = _mic_pids_fn or _default_mic_pids
    pid_names_fn = _pid_names_fn or _default_pid_names
    titles_fn = _titles_fn or _default_titles

    entries = _safe(audio_apps_fn, [], "audio")
    audio_apps = sorted({
        _base_name(e["process_name"]) for e in entries
        if e.get("active") and is_meeting_app(e.get("process_name", ""), apps)
    })

    mic_apps = []
    if settings.get("use_mic_capture"):
        pids = _safe(mic_pids_fn, set(), "mic")
        names = _safe(lambda: pid_names_fn(pids), {}, "pidnames")
        mic_apps = sorted({
            _base_name(n) for n in names.values() if is_meeting_app(n, apps)
        })

    titles = []
    if settings.get("use_window_title"):
        titles = _safe(titles_fn, [], "titles")

    return {
        "timestamp": time.monotonic() if now is None else now,
        "audio_apps": audio_apps,
        "mic_capture_apps": mic_apps,
        "meeting_titles": titles,
        "calendar_event": calendar_event if settings.get("use_calendar") else None,
    }
