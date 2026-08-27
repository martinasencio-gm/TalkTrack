"""Downloadable-model catalog UI for the Local Model provider.

Shown inside Settings ▸ AI Assistant when the provider is "local". One row
per app/ai/model_catalog.CATALOG entry: name + status pill, a detail line,
and a stacked control that is Download → (progress + Cancel) → Select /
Remove. Exactly one download runs at a time; while it does, the parent
dialog disables Save and the provider combo (see download_active_changed).
"""
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.ai.model_catalog import CATALOG, CatalogModel
from app.ai import model_store
from app.ai.model_downloader import DownloadCancelled, DownloadError, download

_PILL_SELECTED = "#89b4fa"
_PILL_DOWNLOADED = "#a6e3a1"
_MUTED = "#9397ab"


def human_size(nbytes: int) -> str:
    mb = nbytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.1f} MB"


def row_detail_line(model: CatalogModel) -> str:
    ctx = f"{model.context_tokens // 1000}k" if model.context_tokens >= 1000 else str(model.context_tokens)
    ram = f"{model.ram_hint_gb:g}"
    return f"{human_size(model.size_bytes)} · {ctx} context · needs ~{ram} GB RAM · {model.license}"


def disk_warning(free_bytes: int, model: CatalogModel) -> str | None:
    need = int(1.5 * model.size_bytes)
    if free_bytes >= need:
        return None
    return (
        f"Only {human_size(free_bytes)} free on disk. "
        f"{model.display_name} needs about {human_size(model.size_bytes)} "
        f"(plus headroom). Download anyway?"
    )


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, model: CatalogModel, token: str = ""):
        super().__init__()
        self._model = model
        self._token = token
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _on_progress(self, done: int, total: int):
        if total:
            self.progress.emit(int(done * 100 / total))

    def run(self):
        try:
            download(
                self._model,
                token=self._token,
                progress_cb=self._on_progress,
                cancel_check=lambda: self._cancelled,
            )
        except DownloadCancelled:
            self.cancelled.emit()
        except DownloadError as e:
            self.failed.emit(str(e))
        except Exception as e:  # defensive: never let the worker crash silently
            self.failed.emit(str(e))
        else:
            self.finished_ok.emit(self._model.key)


class _ModelRow(QWidget):
    select_requested = pyqtSignal(str)
    download_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, model: CatalogModel, parent=None):
        super().__init__(parent)
        self.model = model
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 4, 6)

        top = QHBoxLayout()
        self._name = QLabel(f"<b>{model.display_name}</b>")
        self._pill = QLabel("")
        top.addWidget(self._name)
        top.addWidget(self._pill)
        top.addStretch()

        self._download_btn = QPushButton("Download")
        self._download_btn.clicked.connect(lambda: self.download_requested.emit(model.key))
        self._select_btn = QPushButton("Select")
        self._select_btn.clicked.connect(lambda: self.select_requested.emit(model.key))
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(model.key))
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(model.key))
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        for w in (self._download_btn, self._select_btn, self._remove_btn,
                  self._cancel_btn, self._bar):
            top.addWidget(w)
        outer.addLayout(top)

        detail = QLabel(row_detail_line(model))
        detail.setStyleSheet(f"color: {_MUTED};")
        outer.addWidget(detail)

        self._note = QLabel("")
        self._note.setStyleSheet(f"color: {_MUTED};")
        self._note.setVisible(False)
        outer.addWidget(self._note)

    def set_state(self, *, downloaded: bool, selected: bool, overridden: bool):
        self._pill.setVisible(selected or downloaded)
        if selected:
            self._pill.setText(f'<span style="color:{_PILL_SELECTED};">● Selected</span>')
        elif downloaded:
            self._pill.setText(f'<span style="color:{_PILL_DOWNLOADED};">● Downloaded</span>')
        self._download_btn.setVisible(not downloaded)
        self._select_btn.setVisible(downloaded and not selected)
        self._remove_btn.setVisible(downloaded)
        self._cancel_btn.setVisible(False)
        self._bar.setVisible(False)
        self._note.setVisible(overridden)
        if overridden:
            self._note.setText("Overridden by the custom GGUF path below.")

    def set_downloading(self, percent: int):
        for w in (self._download_btn, self._select_btn, self._remove_btn):
            w.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._bar.setVisible(True)
        self._bar.setValue(percent)

    def set_buttons_enabled(self, enabled: bool):
        for w in (self._download_btn, self._select_btn, self._remove_btn):
            w.setEnabled(enabled)


class ModelCatalogWidget(QWidget):
    selection_changed = pyqtSignal(str)
    download_active_changed = pyqtSignal(bool)

    def __init__(self, token_getter=lambda: "", parent=None):
        super().__init__(parent)
        self._token_getter = token_getter
        self._selected_key = ""
        self._custom_path_active = False
        self._worker: _DownloadWorker | None = None
        self._downloading_key = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._rows: dict[str, _ModelRow] = {}
        for model in CATALOG:
            row = _ModelRow(model)
            row.download_requested.connect(self._start_download)
            row.cancel_requested.connect(self._cancel_download)
            row.select_requested.connect(self._select)
            row.remove_requested.connect(self._remove)
            self._rows[model.key] = row
            layout.addWidget(row)
        self.refresh()

    # ---- public API -------------------------------------------------------

    def selected_key(self) -> str:
        return self._selected_key

    def set_selected_key(self, key: str) -> None:
        self._selected_key = key or ""
        self.refresh()

    def set_custom_path_active(self, active: bool) -> None:
        self._custom_path_active = bool(active)
        self.refresh()

    def is_download_active(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def abort_active_download(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(15000)

    def refresh(self) -> None:
        for key, row in self._rows.items():
            if key == self._downloading_key and self.is_download_active():
                continue
            row.set_state(
                downloaded=model_store.is_downloaded(key),
                selected=(key == self._selected_key and not self._custom_path_active),
                overridden=self._custom_path_active,
            )

    # ---- slots ----------------------------------------------------------

    def _select(self, key: str):
        if not model_store.is_downloaded(key):
            return
        self._selected_key = key
        self.refresh()
        self.selection_changed.emit(key)

    def _remove(self, key: str):
        model = next(m for m in CATALOG if m.key == key)
        if QMessageBox.question(
            self, "Remove model",
            f"Delete {model.display_name} from disk? "
            f"You can download it again later.",
        ) != QMessageBox.StandardButton.Yes:
            return
        model_store.remove(key)
        if self._selected_key == key:
            self._selected_key = ""
            self.selection_changed.emit("")
        self.refresh()

    def _start_download(self, key: str):
        if self.is_download_active():
            return
        model = next(m for m in CATALOG if m.key == key)
        warn = disk_warning(model_store.free_disk_bytes(), model)
        if warn and QMessageBox.question(self, "Low disk space", warn) != \
                QMessageBox.StandardButton.Yes:
            return
        self._downloading_key = key
        self._worker = _DownloadWorker(model, token=self._token_getter() or "")
        self._worker.progress.connect(self._rows[key].set_downloading)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.cancelled.connect(self._on_download_cancelled)
        self._rows[key].set_downloading(0)
        for other, row in self._rows.items():
            if other != key:
                row.set_buttons_enabled(False)
        self._worker.start()
        self.download_active_changed.emit(True)

    def _cancel_download(self, key: str):
        if self._worker is not None:
            self._worker.cancel()

    def _teardown_worker(self):
        # This slot runs off the worker's own finished_ok signal, i.e. while
        # _DownloadWorker.run() has not yet returned. Dropping the last
        # reference here could GC a live QThread ("QThread: Destroyed while
        # thread is still running"). Wait for run() to unwind first.
        worker = self._worker
        if worker is not None:
            worker.wait(5000)
        self._worker = None
        self._downloading_key = ""
        for row in self._rows.values():
            row.set_buttons_enabled(True)
        self.download_active_changed.emit(False)
        self.refresh()

    def _on_download_ok(self, key: str):
        self._teardown_worker()
        if not self._selected_key and not self._custom_path_active:
            self._selected_key = key
            self.selection_changed.emit(key)
            self.refresh()

    def _on_download_failed(self, message: str):
        self._teardown_worker()
        QMessageBox.warning(self, "Download failed", message)

    def _on_download_cancelled(self):
        self._teardown_worker()
