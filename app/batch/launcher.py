"""Helper for spawning batch_transcribe.py as a detached background process.

Used by MainWindow when the user chooses to run batch transcription as an
independent background process that will continue even if TalkTrack is closed.
"""
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def find_pythonw_executable(repo_root=None):
    """Find the best pythonw/python executable for launching the batch script.

    Prefers the project-local .venv interpreter (.venv\\Scripts\\pythonw.exe)
    to match start.bat and Task Scheduler, falling back to pythonw next to
    sys.executable or sys.executable itself.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    venv_pythonw = repo_root / ".venv" / "Scripts" / "pythonw.exe"
    if venv_pythonw.exists():
        return str(venv_pythonw)

    venv_python = repo_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)

    # Next to sys.executable
    exec_dir = Path(sys.executable).parent
    pythonw_candidate = exec_dir / ("pythonw.exe" if sys.platform == "win32" else "python3")
    if pythonw_candidate.exists():
        return str(pythonw_candidate)

    which_pythonw = shutil.which("pythonw")
    if which_pythonw:
        return which_pythonw

    return sys.executable


def launch_detached_batch(repo_root=None, until=None, diarize=None, summarize=None,
                          limit=None):
    """Launch batch_transcribe.py as a detached background OS process.

    Returns the spawned subprocess.Popen instance.
    """
    if repo_root is None:
        repo_root = Path(__file__).resolve().parent.parent.parent
    else:
        repo_root = Path(repo_root).resolve()

    script_path = repo_root / "batch_transcribe.py"
    if not script_path.exists():
        raise FileNotFoundError(f"batch_transcribe.py not found at {script_path}")

    python_exe = find_pythonw_executable(repo_root)

    # Default to 12 hours from now if no cutoff specified
    if not until:
        cutoff_dt = datetime.now() + timedelta(hours=12)
        until = cutoff_dt.strftime("%Y-%m-%dT%H:%M")

    cmd = [python_exe, str(script_path), "--until", str(until)]

    if diarize is True:
        cmd.append("--diarize")
    elif diarize is False:
        cmd.append("--no-diarize")

    if summarize is True:
        cmd.append("--summarize")
    elif summarize is False:
        cmd.append("--no-summarize")

    if limit and int(limit) > 0:
        cmd.extend(["--limit", str(int(limit))])

    logger.info("Spawning detached batch process: %s (cwd=%s)", cmd, repo_root)

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )

    proc = subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        creationflags=creationflags,
        close_fds=True,
    )
    return proc
