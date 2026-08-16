"""One-time import of Markdown transcript exports into their recording's
own session folder.

Before this change, `export_transcript()` wrote a Markdown copy into a
separate, configurable transcripts/ folder, keyed by
`<sanitized-directory-name>_<timestamp>.md`. That folder — and everything
that managed it — has been removed; the export now lives at
`<session_dir>/transcript.md`. This module runs once at startup to relocate
any export still sitting in the old folder(s) into the matching session
folder. Exports with no matching session folder (e.g. the recording was
already deleted under the old "recordings only" scope, leaving the export
as the only surviving copy) are left in place untouched — nothing here ever
deletes data.
"""
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


def import_exports_into_sessions(source_dirs, recordings_dir):
    """Move *.md files from source_dirs into their matching session folder
    under recordings_dir, as transcript.md. Returns the destination paths
    (as strings) actually moved.

    A file matches a session folder when its name starts with
    "<folder_name>_" — the old filename format's stable prefix. A
    destination that already has a transcript.md is left alone (and the
    source file untouched) rather than overwritten. Duplicate/unresolvable
    source dirs are skipped; a missing recordings_dir is a no-op.
    """
    recordings_dir = Path(recordings_dir)
    if not recordings_dir.is_dir():
        return []

    session_names = sorted(
        (p.name for p in recordings_dir.iterdir() if p.is_dir()),
        key=len, reverse=True,  # longest name first avoids a short name's
    )                            # prefix falsely matching a longer sibling's export

    moved = []
    seen_sources = set()
    for source_dir in source_dirs:
        if not source_dir:
            continue
        source = Path(source_dir)
        if not source.is_dir():
            continue
        try:
            resolved = source.resolve()
        except OSError:
            continue
        if resolved in seen_sources:
            continue
        seen_sources.add(resolved)

        for md_path in sorted(source.glob("*.md")):
            match = next(
                (name for name in session_names if md_path.name.startswith(name + "_")),
                None,
            )
            if match is None:
                logger.info("No matching session for stranded export %s — left in place", md_path.name)
                continue

            destination = recordings_dir / match / "transcript.md"
            if destination.exists():
                logger.info("%s already has a transcript.md — %s left in place", match, md_path.name)
                continue
            try:
                shutil.move(str(md_path), str(destination))
                moved.append(str(destination))
            except OSError:
                logger.exception("Failed to import stranded export %s", md_path)

    if moved:
        logger.info("Imported %d transcript export(s) into their session folders", len(moved))
    return moved
