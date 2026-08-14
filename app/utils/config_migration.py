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
