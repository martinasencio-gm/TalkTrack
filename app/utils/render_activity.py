"""Track which audio output endpoint is actually rendering sound.

The Windows default output is often not the endpoint a meeting app plays
to — Teams can be pinned to desk speakers while the default is a monitor.
Capturing the loopback of a device that renders nothing yields a silent
system track that ChunkWriter then deletes, with no error anywhere. So
when the user has expressed no preference, pick the endpoint that is
demonstrably producing sound instead of the nominal default.

sample_render_peaks() touches pycaw/comtypes and MUST run inside
com_session_worker's isolated process — see that module's docstring for
why. Everything else here is pure and runs anywhere.
"""
import logging

logger = logging.getLogger(__name__)

# Idle endpoints report exactly 0.0; anything above this is real signal.
# Deliberately low: quiet speech on a low-volume endpoint still counts.
SILENCE_THRESHOLD = 0.0005

# How long an endpoint stays "recently active" after it goes quiet. Long
# enough to survive natural pauses in a conversation, short enough that
# switching output devices mid-session is noticed.
ACTIVITY_WINDOW_SECONDS = 45.0


def sample_render_peaks():
    """Return {friendly_name: peak} for every active render endpoint.

    COM-heavy — worker process only. Never raises: a probe that fails
    contributes nothing and the caller keeps its previous history.
    """
    peaks = {}
    try:
        import comtypes
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioMeterInformation

        from pycaw.pycaw import EDataFlow, DEVICE_STATE

        comtypes.CoInitialize()
        enumerator = AudioUtilities.GetDeviceEnumerator()
        # Render endpoints only. Capture endpoints expose peak meters just
        # the same, and the user's own microphone is loud throughout a
        # meeting — sampling it would put a device that can't be a loopback
        # source at the top of the activity list.
        collection = enumerator.EnumAudioEndpoints(
            EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        for i in range(collection.GetCount()):
            try:
                endpoint = collection.Item(i)
                name = AudioUtilities.CreateDevice(endpoint).FriendlyName
                if not name:
                    continue
                meter = endpoint.Activate(
                    IAudioMeterInformation._iid_, CLSCTX_ALL, None
                ).QueryInterface(IAudioMeterInformation)
                peaks[name] = float(meter.GetPeakValue())
            except Exception:
                continue
    except Exception:
        logger.debug("Render peak sampling failed", exc_info=True)
    return peaks


def update_activity(history, peaks, now, threshold=SILENCE_THRESHOLD,
                    window=ACTIVITY_WINDOW_SECONDS):
    """Fold one peak sample into {name: (last_active_time, peak)}.

    Endpoints below the threshold keep whatever timestamp they already
    had — going quiet between sentences must not erase the fact that this
    device was carrying audio a moment ago.
    """
    updated = {
        name: entry for name, entry in history.items()
        if now - entry[0] <= window
    }
    for name, peak in peaks.items():
        if peak > threshold:
            updated[name] = (now, peak)
    return updated


def active_names(history, now, window=ACTIVITY_WINDOW_SECONDS):
    """Recently-active endpoint names, most recently active first.

    Recency outranks loudness: a notification chime on one device
    shouldn't outrank the device actually carrying the meeting. Peak
    breaks ties, since a batch of samples folded at one instant shares a
    timestamp.
    """
    live = [(t, peak, name) for name, (t, peak) in history.items()
            if now - t <= window]
    return [name for _, _, name in sorted(live, reverse=True)]


def most_active(history, now, window=ACTIVITY_WINDOW_SECONDS):
    """Name of the endpoint most recently rendering audio, or None."""
    names = active_names(history, now, window)
    return names[0] if names else None


def pick_output_index(history, outputs, now, window=ACTIVITY_WINDOW_SECONDS):
    """Device index of the most recently active endpoint, or None.

    None means "no opinion" — the endpoint may be hidden by the user or
    have no capturable loopback — and the caller should fall back rather
    than substitute an arbitrary device.
    """
    from app.utils.audio_devices import match_device_name

    # Walk in recency order rather than taking only the top entry: capture
    # endpoints report peaks too, and the user's own mic is loud during a
    # meeting. Stopping at the first name would report "no opinion"
    # whenever the mic outranked the speakers.
    for name in active_names(history, now, window):
        index = match_device_name(name, outputs)
        if index is not None:
            return index
    return None
