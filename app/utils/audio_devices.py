import sounddevice as sd


def _is_hidden(name, hidden_devices):
    """Check if a device name matches any hidden device pattern (case-insensitive)."""
    if not hidden_devices:
        return False
    name_lower = name.lower()
    for pattern in hidden_devices:
        if pattern.lower() in name_lower:
            return True
    return False


def get_input_devices(hidden_devices=None):
    """Return list of audio input (microphone) devices."""
    devices = sd.query_devices()
    inputs = []
    for i, dev in enumerate(devices):
        if dev["max_input_channels"] > 0:
            if _is_hidden(dev["name"], hidden_devices):
                continue
            inputs.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_input_channels"],
                "sample_rate": dev["default_samplerate"],
                "hostapi": sd.query_hostapis(dev["hostapi"])["name"],
            })
    return inputs


def get_loopback_devices():
    """Return list of WASAPI loopback devices for system audio capture."""
    devices = sd.query_devices()
    loopbacks = []
    for i, dev in enumerate(devices):
        hostapi = sd.query_hostapis(dev["hostapi"])
        if hostapi["name"] == "Windows WASAPI" and dev["max_input_channels"] > 0:
            if "loopback" in dev["name"].lower() or dev["max_output_channels"] > 0:
                loopbacks.append({
                    "index": i,
                    "name": dev["name"],
                    "channels": dev["max_input_channels"],
                    "sample_rate": dev["default_samplerate"],
                    "hostapi": "WASAPI",
                })
    return loopbacks


def get_system_audio_devices(hidden_devices=None):
    """Return WASAPI output devices for system audio loopback capture.

    These are speakers/headphone output devices. PyAudioWPatch automatically
    finds the corresponding loopback input device by name matching.
    """
    devices = sd.query_devices()
    outputs = []
    for i, dev in enumerate(devices):
        hostapi = sd.query_hostapis(dev["hostapi"])
        if hostapi["name"] == "Windows WASAPI" and dev["max_output_channels"] > 0:
            if _is_hidden(dev["name"], hidden_devices):
                continue
            outputs.append({
                "index": i,
                "name": dev["name"],
                "channels": dev["max_output_channels"],
                "sample_rate": dev["default_samplerate"],
                "hostapi": "WASAPI",
            })
    return outputs


# Keep old name as alias
get_wasapi_output_devices = get_system_audio_devices


def get_default_mic():
    """Return the default microphone device index."""
    try:
        return sd.default.device[0]
    except Exception:
        inputs = get_input_devices()
        return inputs[0]["index"] if inputs else None


MME_NAME_LIMIT = 31


def match_device_name(default_name, outputs):
    """Find the WASAPI output whose name is default_name, tolerating the
    MME 31-character cap.

    sd.default.device[1] is an MME/DirectSound index, and MME clips device
    names to 31 characters. Comparing those clipped names for equality
    never matched anything on a machine with long device names ("DELL
    S2725QS (2- HD Audio Driver for Display Audio)"), so the caller fell
    through to "first device in the list" and captured an endpoint that
    rendered nothing — an empty system_audio.wav with no error anywhere.

    A clipped name shared by two endpoints carries nothing to tell them
    apart, so ambiguity returns None rather than guessing.
    """
    for dev in outputs:
        if dev["name"] == default_name:
            return dev["index"]
    if len(default_name) < MME_NAME_LIMIT:
        return None
    prefixed = [d for d in outputs if d["name"].startswith(default_name)]
    return prefixed[0]["index"] if len(prefixed) == 1 else None


def get_default_output():
    """Return the default output device index (for loopback).

    sd.default.device[1] returns a DirectSound/MME index which doesn't match
    WASAPI device indices. We match by name instead to find the corresponding
    WASAPI output device.
    """
    outputs = get_system_audio_devices()
    try:
        default_idx = sd.default.device[1]
        if default_idx is not None and default_idx >= 0:
            default_info = sd.query_devices(default_idx)
            matched = match_device_name(default_info["name"], outputs)
            if matched is not None:
                return matched
    except Exception:
        pass
    return outputs[0]["index"] if outputs else None


def _clean_device_tokens(name: str) -> set[str]:
    """Extract normalized alphanumeric tokens from a device name for fuzzy matching."""
    import re
    if not name:
        return set()
    # Strip common wrappers/APIs
    cleaned = re.sub(r"\((wasapi loopback|windows wasapi|mme|directsound|wdm-ks|wasapi)\)", "", name, flags=re.IGNORECASE)
    # Remove punctuation
    tokens = re.findall(r"[a-z0-9]+", cleaned.lower())
    # Generic audio terms that shouldn't alone determine identity
    generic = {"audio", "device", "endpoint", "driver", "for", "high", "definition",
               "microphone", "mic", "headset", "headphones", "speakers", "array", "default"}
    meaningful = {t for t in tokens if t not in generic and len(t) > 1}
    return meaningful if meaningful else set(tokens)


def device_names_match(name1: str, name2: str) -> bool:
    """Return True if two device names refer to the same audio hardware endpoint."""
    if not name1 or not name2:
        return False

    n1 = name1.strip().lower()
    n2 = name2.strip().lower()

    if n1 == n2:
        return True

    # Check prefix / substring match (handles MME 31-char truncation)
    if n1.startswith(n2) or n2.startswith(n1):
        return True

    tokens1 = _clean_device_tokens(n1)
    tokens2 = _clean_device_tokens(n2)

    if not tokens1 or not tokens2:
        return False

    # If the core tokens of one are a subset of another, they match
    if tokens1.issubset(tokens2) or tokens2.issubset(tokens1):
        return True

    # If token overlap ratio is high (e.g. 75%+)
    overlap = tokens1.intersection(tokens2)
    if overlap:
        ratio = len(overlap) / min(len(tokens1), len(tokens2))
        if ratio >= 0.75:
            return True

    return False


def find_matching_device_index(target_endpoint_name: str, device_list: list[dict]) -> int | None:
    """Find the device index in device_list matching target_endpoint_name."""
    if not target_endpoint_name or not device_list:
        return None
    for dev in device_list:
        if device_names_match(dev.get("name", ""), target_endpoint_name):
            return dev.get("index")
    return None

