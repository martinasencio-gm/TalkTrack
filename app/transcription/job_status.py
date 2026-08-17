"""Which recordings currently have transcription work outstanding."""


def transcribing_directories(workers, pending):
    """Session directories with a job running or queued.

    Workers bind their session at creation rather than reading the
    displayed one, so this stays correct while the user browses other
    recordings mid-job. Queued entries count too — they will run without
    any further input, and a row that looks untouched until its turn comes
    is the confusing case this indicator exists to remove.
    """
    directories = set()

    for worker in workers:
        if worker is None or not worker.isRunning():
            continue
        session = getattr(worker, "session", None)
        directory = (session or {}).get("directory")
        if directory:
            directories.add(directory)

    for _, session in pending:
        directory = (session or {}).get("directory")
        if directory:
            directories.add(directory)

    return directories
