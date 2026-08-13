"""Atomic file writes — temp file + os.replace so a crash mid-write can't
leave a truncated file behind."""

import json
import os
import time


def atomic_write_text(path, text, encoding="utf-8", retries=4, initial_delay=0.1):
    """Write text to path via temp file + os.replace.

    os.replace can fail with a transient PermissionError (WinError 5) when
    something else — OneDrive sync, Defender, the search indexer — holds a
    momentary lock on a just-touched file. Same class of problem as
    _rmtree_robust in recordings_list.py; same fix: retry with exponential
    backoff rather than surface a spurious error for a lock that clears in
    milliseconds.
    """
    path = str(path)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding=encoding) as f:
        f.write(text)

    delay = initial_delay
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2


def atomic_write_json(path, obj, **dump_kwargs):
    atomic_write_text(path, json.dumps(obj, **dump_kwargs))
