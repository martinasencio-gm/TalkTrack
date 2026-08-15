"""Shared app-data directory, resolved once at import time.

Migrates existing users to `<real Documents folder>/TalkTrack` on first
launch after the update. Both config.py and main.py import APP_DATA_DIR from
here instead of each computing their own path literal.
"""
import os
import shutil
import winreg
from pathlib import Path


def _known_documents_dir():
    """The real Windows Documents folder, honoring redirection.

    Path.home() / "Documents" is wrong whenever Documents has been redirected
    elsewhere - e.g. to a OneDrive-managed location, which is the default on
    many managed/corporate machines. The registry's "Personal" shell folder
    value is the same thing Explorer itself uses, so it reflects redirection
    correctly. Falls back to the naive path if the registry is unreadable
    (e.g. running under Wine, or a locked-down machine).
    """
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        )
        with key:
            value, _ = winreg.QueryValueEx(key, "Personal")
        return Path(os.path.expandvars(value))
    except OSError:
        return Path.home() / "Documents"


def _merge_legacy_into(legacy_dir, target_dir):
    """Move legacy_dir's contents into target_dir, one file at a time.

    A whole-directory shutil.move is wrong here: target_dir can already exist
    with unrelated content (recordings/transcripts default under the same
    Documents/TalkTrack folder), and moving a directory on top of an existing
    one nests it instead of merging. Anything already present at the
    destination is left alone rather than overwritten.

    Returns True if legacy_dir ended up empty and was removed. False on any
    failure - the caller then falls back to using legacy_dir directly, so
    settings and the log stay together in one place instead of split across
    two folders.
    """
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for entry in legacy_dir.iterdir():
            dest = target_dir / entry.name
            if dest.exists():
                continue
            shutil.move(str(entry), str(dest))
        legacy_dir.rmdir()
        return True
    except OSError:
        return False


# Locations earlier builds may have used, checked in order: the very first
# ~/.talktrack, and the briefly-shipped Path.home()/"Documents" (which
# ignored redirection and put data in the wrong Documents folder on any
# machine where Documents points elsewhere).
_LEGACY_CANDIDATES = (
    Path.home() / ".talktrack",
    Path.home() / "Documents" / "TalkTrack",
)

APP_DATA_DIR = _known_documents_dir() / "TalkTrack"

# settings.json, not directory existence, is the real "already migrated"
# signal - the target directory can legitimately exist already (e.g. with
# recordings/transcripts subfolders from unrelated settings) without config
# data ever having been migrated into it.
if not (APP_DATA_DIR / "settings.json").exists():
    for _legacy in _LEGACY_CANDIDATES:
        if _legacy == APP_DATA_DIR or not _legacy.exists():
            continue
        if not _merge_legacy_into(_legacy, APP_DATA_DIR):
            APP_DATA_DIR = _legacy
        break
    # else: none of the candidates exist - brand-new install. APP_DATA_DIR
    # stays at the resolved Documents target; callers create it on first
    # write via mkdir(exist_ok=True).
