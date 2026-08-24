"""One-time config migrations. Pure functions - no file I/O, no global state."""


def apply_meeting_detection_migration(saved, merged):
    """Derive meeting_detection.mode from the legacy general.auto_record flag.

    `saved` is the raw dict read from disk (None when no config file existed),
    `merged` is that dict deep-merged over DEFAULT_CONFIG.

    The migration writes the mode explicitly rather than letting the default
    stand. Config.load() deep-merges new default keys into existing files, so a
    user who deliberately turned auto-record off would otherwise inherit the new
    "suggest" default and start getting prompts they never asked for.
    """
    if not saved:
        return merged                      # brand-new install: defaults are correct
    if "meeting_detection" in saved:
        return merged                      # already migrated; respect their choice
    general = saved.get("general") or {}
    if "auto_record" not in general:
        return merged                      # pre-dates auto_record entirely
    merged["meeting_detection"]["mode"] = "auto" if general["auto_record"] else "off"
    if "auto_record_threshold" in general:
        merged["meeting_detection"]["threshold_seconds"] = general["auto_record_threshold"]
    return merged


def apply_close_to_tray_migration(saved, merged):
    """Carry the legacy minimize-to-tray choice over to general.close_to_tray.

    The minimize button no longer hides to the tray — it always minimizes to
    the taskbar — so the two keys that used to route it there
    (general.minimize_behavior == "tray", and the older general.minimize_to_tray
    boolean) are gone. A user who had that turned on keeps a tray route; they
    just reach it from the close button's "minimize instead" choice now.
    """
    if not saved:
        return merged                      # brand-new install: defaults are correct
    general = saved.get("general") or {}
    if "close_to_tray" in general:
        return merged                      # already migrated; respect their choice
    if general.get("minimize_behavior") == "tray" or general.get("minimize_to_tray") is True:
        merged["general"]["close_to_tray"] = True
    return merged
