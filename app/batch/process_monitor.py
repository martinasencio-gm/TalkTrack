"""Process discovery, status inspection, and termination for batch transcription.

Detects both detached OS background processes running batch_transcribe.py and
in-app QThread batch workers, providing PID, runtime tracking, and termination.
"""
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import psutil


def format_duration(seconds: float) -> str:
    """Format seconds into a readable duration string: HH:MM:SS or MM:SS."""
    total_seconds = max(0, int(seconds))
    m, s = divmod(total_seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class BatchProcessInfo:
    """Snapshot of a running batch transcription process."""
    pid: int
    create_time: float
    is_in_app: bool = False
    cmdline: List[str] = field(default_factory=list)
    name: str = ""

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, time.time() - self.create_time)

    @property
    def formatted_duration(self) -> str:
        return format_duration(self.elapsed_seconds)

    @property
    def formatted_start_time(self) -> str:
        try:
            dt = datetime.fromtimestamp(self.create_time)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return "Unknown"

    @property
    def process_type_label(self) -> str:
        if self.is_in_app:
            return "In-App Worker Thread"
        return "Detached Background Process"

    @property
    def arguments_summary(self) -> str:
        if self.is_in_app:
            return "Running inside TalkTrack"
        # Filter out python executable and script path for clean argument display
        args = []
        skip_next = False
        for arg in self.cmdline[1:]:
            if "batch_transcribe.py" in arg.lower():
                continue
            args.append(arg)
        return " ".join(args) if args else "Default settings"


def find_running_batch_processes(
    in_app_worker=None,
    in_app_start_time: Optional[float] = None,
) -> List[BatchProcessInfo]:
    """Find all running batch processes (both OS processes and in-app worker).

    Returns a list of BatchProcessInfo instances.
    """
    processes: List[BatchProcessInfo] = []
    own_pid = os.getpid()

    # 1. Check in-app worker thread
    if in_app_worker is not None:
        is_running = getattr(in_app_worker, "isRunning", None)
        if callable(is_running) and is_running():
            start_ts = in_app_start_time if in_app_start_time is not None else time.time()
            processes.append(
                BatchProcessInfo(
                    pid=own_pid,
                    create_time=start_ts,
                    is_in_app=True,
                    cmdline=[sys.executable, "batch_worker (in-app)"],
                    name="TalkTrack (In-App)",
                )
            )

    # 2. Inspect OS processes for batch_transcribe.py
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            pid = proc.info["pid"]
            if pid == own_pid:
                continue

            cmdline = proc.info.get("cmdline") or []
            if not cmdline:
                continue

            is_batch_script = any(
                isinstance(arg, str) and "batch_transcribe.py" in arg.lower()
                for arg in cmdline
            )
            if not is_batch_script:
                continue

            create_time = proc.info.get("create_time") or time.time()
            name = proc.info.get("name") or "python"

            processes.append(
                BatchProcessInfo(
                    pid=pid,
                    create_time=create_time,
                    is_in_app=False,
                    cmdline=list(cmdline),
                    name=name,
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return processes


def terminate_batch_process(pid: int, in_app_worker=None) -> bool:
    """Terminate a running batch process by PID.

    If the PID matches the current process and an in-app worker is provided,
    requests cooperative cancellation on the worker. Otherwise terminates
    the external OS process.
    """
    own_pid = os.getpid()
    if pid == own_pid and in_app_worker is not None:
        cancel_fn = getattr(in_app_worker, "cancel", None)
        if callable(cancel_fn):
            cancel_fn()
            return True

    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            proc.kill()
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.AccessDenied:
        try:
            proc = psutil.Process(pid)
            proc.kill()
            return True
        except psutil.Error:
            return False
    except psutil.Error:
        return False
