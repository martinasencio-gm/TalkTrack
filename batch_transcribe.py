"""TalkTrack batch transcription — the Windows Task Scheduler companion.

Transcribes (and optionally diarizes) the recordings queued for batch
processing in the app, then stops. Run with --help for usage.

    batch_transcribe.py --until 07:00

This file is bootstrap only, and the order below matters. Everything else
lives in app/batch/.
"""
import importlib.util
import os
import sys
from pathlib import Path

# Before ANY torch or ctranslate2 import. faster-whisper (ctranslate2) and
# torch each bundle their own copy of Intel's libiomp5md.dll, and loading
# both — which is exactly what transcribe-then-diarize does — deadlocks the
# process without this.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

sys.path.insert(0, str(Path(__file__).parent))


def _add_torch_dll_directory():
    """Put torch's DLLs on the search path before Qt reorders it.

    Same workaround as main.py: importing PyQt6 modifies the Windows DLL
    search order and breaks torch's c10.dll load. find_spec rather than
    `import torch` so startup doesn't pay for a full torch import it may
    not need.
    """
    try:
        spec = importlib.util.find_spec("torch")
    except (ImportError, ValueError):
        return
    if spec is None or not spec.submodule_search_locations:
        return
    lib_dir = Path(list(spec.submodule_search_locations)[0]) / "lib"
    if lib_dir.is_dir():
        try:
            os.add_dll_directory(str(lib_dir))
        except OSError:
            pass


def main():
    _add_torch_dll_directory()
    # Imported after the DLL directory is set up, and deliberately not at
    # module scope.
    from app.batch.runner import main as run_batch
    return run_batch()


if __name__ == "__main__":
    sys.exit(main())
