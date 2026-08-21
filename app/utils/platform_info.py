"""Platform detection utilities for TalkTrack."""
import platform


def is_windows():
    """Check if running on Windows."""
    return platform.system() == "Windows"


def is_windows_11():
    """Check if running on Windows 11 (Build 22000+).

    Windows 11 introduced per-process audio loopback capture via
    ActivateAudioInterfaceAsync with AUDIOCLIENT_ACTIVATION_PARAMS.
    """
    if not is_windows():
        return False
    try:
        build = int(platform.version().split(".")[-1])
        return build >= 22000
    except (ValueError, IndexError):
        return False


def get_windows_build():
    """Return the Windows build number, or 0 if not on Windows."""
    if not is_windows():
        return 0
    try:
        return int(platform.version().split(".")[-1])
    except (ValueError, IndexError):
        return 0


def get_current_user_name(config=None) -> str:
    """Return the friendly display name of the current logged-in user.

    Order of precedence:
      1. Config explicit override ('general.user_name' if configured)
      2. Windows Active Directory / local display name (via win32api.GetUserNameEx)
      3. Windows username (os.environ['USERNAME'])
    """
    if config:
        try:
            cfg_name = config.get("general", "user_name")
            if cfg_name and isinstance(cfg_name, str) and cfg_name.strip():
                return cfg_name.strip()
        except Exception:
            pass

    # Try Windows display name (e.g. "Martin Asencio")
    try:
        import win32api
        # 3 is NameDisplay (EXTENDED_NAME_FORMAT)
        name_display = getattr(win32api, "GetUserNameEx", None)
        if name_display:
            full_name = name_display(3)
            if full_name and isinstance(full_name, str) and full_name.strip():
                return full_name.strip()
    except Exception:
        pass

    # Fallback to USERNAME env var
    import os
    username = os.environ.get("USERNAME", "")
    if username and username.strip():
        return username.strip()

    return "You"

