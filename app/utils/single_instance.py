"""Single-instance enforcement so TalkTrack can't be launched twice at once.

Two mechanisms working together:

- QLockFile is the actual mutual-exclusion primitive. It stamps the lock
  file with this process's PID and hostname, so a lock left behind by a
  crash (rather than a clean exit) is recognized as stale next launch and
  reclaimed automatically instead of wedging every future launch forever.
- QLocalServer/QLocalSocket is a lightweight local IPC channel so a second
  launch attempt can ask the first instance to bring itself to front,
  instead of just failing with an error the user has to go interpret
  (especially now that an idle instance can be sitting fully hidden in the
  tray with no taskbar entry — see minimize_to_tray).
"""
import logging
import os
from pathlib import Path

import psutil
from PyQt6.QtCore import QObject, pyqtSignal, QLockFile
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

SERVER_NAME = "TalkTrackSingleInstance"
_LOCK_FILE_NAME = "talktrack.lock"


class SingleInstanceGuard(QObject):
    """Acquire the single-instance lock, or notify the instance that has it."""

    show_requested = pyqtSignal()

    def __init__(self, lock_dir, parent=None):
        super().__init__(parent)
        self._lock_file = QLockFile(str(Path(lock_dir) / _LOCK_FILE_NAME))
        # 0 disables QLockFile's own time-based staleness guess in favor of
        # its PID/hostname liveness check alone — the actual owning process
        # either still exists or it doesn't, so a guessed timeout would only
        # ever be wrong in one direction or the other.
        self._lock_file.setStaleLockTime(0)
        self._server = None

    def try_acquire(self):
        """Attempt to become the primary instance.

        Returns True if this process now holds the lock and should proceed
        to start the app; False if another instance already holds it.
        """
        if not self._lock_file.tryLock(100):
            return False
        self._start_server()
        return True

    def notify_running_instance(self):
        """Ask the existing instance to show itself.

        Best-effort: if the lock holder isn't listening yet (e.g. still
        mid-startup, before its server is up), this returns False and the
        caller falls back to telling the user directly.
        """
        # Parented to self: an unparented QLocalSocket can be garbage
        # collected the moment this method returns (nothing else in Python
        # holds a reference to it), which can tear down the pipe before the
        # write actually reaches the other end.
        socket = QLocalSocket(self)
        socket.connectToServer(SERVER_NAME)
        connected = socket.waitForConnected(200)
        if connected:
            socket.write(b"show")
            socket.waitForBytesWritten(200)
            socket.disconnectFromServer()
        socket.disconnected.connect(socket.deleteLater)
        return connected

    def _start_server(self):
        # A prior crash can leave a stale server registration behind on some
        # platforms; removeServer() is documented as a no-op when nothing is
        # actually listening, so calling it unconditionally is safe.
        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._server.listen(SERVER_NAME)

    def _on_new_connection(self):
        socket = self._server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(lambda: self._on_ready_read(socket))

    def _on_ready_read(self, socket):
        socket.readAll()
        self.show_requested.emit()
        socket.disconnectFromServer()


def sweep_orphaned_processes(main_script_path):
    """Terminate any other process still running this same main.py.

    Call only after try_acquire() has confirmed this process holds the
    single-instance lock. QLockFile's PID-liveness check is the source of
    truth for "who's the real instance" — anything else out there running
    the identical script at that point (a launcher stub or previous
    instance's process that outlived its own shutdown) is, by definition,
    not it, and safe to clean up rather than leak for the rest of the day.

    Never touches this process or its own parent (the venv launcher's
    trampoline process shares this exact command line and must stay alive).
    Returns the list of PIDs it terminated, for logging.
    """
    own_pid = os.getpid()
    own_parent_pid = None
    try:
        own_parent_pid = psutil.Process(own_pid).ppid()
    except psutil.Error:
        pass

    target = str(Path(main_script_path).resolve()).lower()
    killed = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        pid = proc.info["pid"]
        if pid == own_pid or pid == own_parent_pid:
            continue
        cmdline = proc.info.get("cmdline") or []
        if not any(arg.lower() == target for arg in cmdline):
            continue
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except psutil.TimeoutExpired:
            try:
                proc.kill()
            except psutil.Error:
                pass
        except psutil.Error:
            continue
        killed.append(pid)

    if killed:
        logger.warning("Cleaned up %d orphaned TalkTrack process(es): %s", len(killed), killed)
    return killed
