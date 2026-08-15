"""Shared app-data directory, resolved once at import time.

Migrates existing users from the legacy `~/.talktrack` location to
`Documents/TalkTrack` on first launch after the update. Both config.py and
main.py import APP_DATA_DIR from here instead of each computing their own
Path.home() literal.
"""
import shutil
from pathlib import Path

_LEGACY_DIR = Path.home() / ".talktrack"
APP_DATA_DIR = Path.home() / "Documents" / "TalkTrack"

if APP_DATA_DIR.exists():
    pass  # already at the new location - fresh install or already migrated
elif _LEGACY_DIR.exists():
    try:
        APP_DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_LEGACY_DIR), str(APP_DATA_DIR))
    except OSError:
        # Migration failed (permissions, cross-volume, etc). Keep using the
        # legacy location rather than losing settings or splitting the log
        # and config across two folders.
        APP_DATA_DIR = _LEGACY_DIR
# else: neither exists - brand-new install. APP_DATA_DIR stays at the
# Documents target; callers create it on first write via mkdir(exist_ok=True).
