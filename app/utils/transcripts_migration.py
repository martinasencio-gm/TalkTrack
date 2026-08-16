"""One-time relocation of Markdown transcript exports.

Exports written before the app data dir moved to Documents (c49d8c6,
d8e86fc) were saved under the repo-relative default transcripts folder.
The app now reads and writes the configured folder, so those files are
invisible to it — present on disk, absent from the recordings list. This
moves them across once. Nothing is deleted and nothing is overwritten.
"""
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def import_legacy_exports(legacy_dir, transcripts_dir):
    """Move *.md from legacy_dir into transcripts_dir, returning the names moved.

    Skips any filename already present in the target — that copy is the newer
    one — and leaves the legacy file in place rather than destroying it. A
    missing legacy dir, a falsy path, or both paths resolving to the same
    directory are all no-ops.
    """
    if not legacy_dir or not transcripts_dir:
        return []

    legacy = Path(legacy_dir)
    target = Path(transcripts_dir)
    if not legacy.is_dir():
        return []
    try:
        if legacy.resolve() == target.resolve():
            return []
    except OSError:
        return []

    moved = []
    for path in sorted(legacy.glob("*.md")):
        destination = target / path.name
        if destination.exists():
            logger.info("Legacy export %s already present in target — left in place", path.name)
            continue
        try:
            os.makedirs(target, exist_ok=True)
            shutil.move(str(path), str(destination))
            moved.append(path.name)
        except OSError:
            logger.exception("Failed to move legacy export %s", path)

    if moved:
        logger.info("Imported %d legacy transcript export(s) from %s", len(moved), legacy)
    return moved
