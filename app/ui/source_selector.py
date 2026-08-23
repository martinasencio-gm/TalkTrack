"""Audio source selection widget with per-app capture support."""
import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QRadioButton, QButtonGroup, QCheckBox, QFrame
)
from PyQt6.QtCore import pyqtSignal, QTimer, Qt

from app.utils.audio_devices import (
    get_input_devices, get_system_audio_devices,
    get_default_mic, get_default_output,
    device_names_match, find_matching_device_index
)
from app.utils.device_mismatch import compute_device_mismatches
from app.utils.platform_info import is_windows_11
from app.utils.icons import colored_pixmap
from app.ui.collapsible_section import CollapsibleSection
from app.ui.preflight import PreflightWidget
from app.utils import preflight_status

logger = logging.getLogger(__name__)


# Apps that set AUDCLNT_STREAMFLAGS_EXCLUDE_FROM_PROCESS_LOOPBACK_CAPTURE
# on their call audio stream, making per-app loopback return silence
# during calls. Process-loopback captures non-call audio from these apps
# (chat chimes, notifications) but not the actual conversation.
# See docs/per-app-audio-capture.md for background.
CONFERENCING_APPS = {
    "Microsoft Teams",
    "Zoom",
    "Webex",
    "GoToMeeting",
    "Google Meet",
    "Discord",
}


# Every row label in this section shares one width so the dropdowns beside
# them line up into a single column.
_LABEL_WIDTH = 70


def _group_heading(text, icon_name=None):
    """Small caps-style divider naming what the rows below it capture.

    An optional leading icon reinforces which half of the dialog ("your
    voice" vs "the call") the rows underneath belong to.
    """
    if icon_name is None:
        label = QLabel(text)
        label.setObjectName("groupHeading")
        return label

    row_widget = QWidget()
    row = QHBoxLayout(row_widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    icon_label = QLabel()
    icon_label.setPixmap(colored_pixmap(icon_name, "#9184d9", 13))
    row.addWidget(icon_label)
    text_label = QLabel(text)
    text_label.setObjectName("groupHeading")
    row.addWidget(text_label)
    row.addStretch(1)
    return row_widget


class _ModeCard(QFrame):
    """Clickable card pairing a capture-mode radio with its consequence
    text. The whole card (not just the radio hit-target) selects the mode,
    per the Sources-dialog redesign spec."""

    def __init__(self, radio, description, recommended=False, parent=None):
        super().__init__(parent)
        self.setObjectName("sourceModeCard")
        self._radio = radio
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(11, 8, 11, 8)
        layout.setSpacing(4)

        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.addWidget(radio)
        top_row.addStretch(1)
        if recommended:
            tag = QLabel("Recommended")
            tag.setObjectName("modeCardRecommended")
            top_row.addWidget(tag)
        layout.addLayout(top_row)

        desc_label = QLabel(description)
        desc_label.setObjectName("modeCardDescription")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._radio.setChecked(True)
        super().mousePressEvent(event)


def _fullest_device_name(name, all_devices):
    """Find the longest device name sharing this one's prefix.

    Windows' MME host API truncates device names to ~31 characters, so the
    same physical microphone often shows up twice in this list: once via
    MME with a chopped-off name, once via WASAPI/DirectSound with the full
    one. Used only to populate tooltips — the (possibly truncated) name
    stays the value everything else (selection, persistence) keys off of.
    """
    prefix = name[:20]
    candidates = [d["name"] for d in all_devices if d["name"].startswith(prefix)]
    return max(candidates, key=len) if candidates else name


def format_per_app_suffix(names):
    """Build the collapsed-title suffix for per-app capture mode.

    [] -> "(No apps selected)"
    [A] -> "(A)"
    [A, B] -> "(A, B)"
    [A, B, ...] -> "(A, B +N more)" where N = len - 2.
    """
    if not names:
        return "(No apps selected)"
    if len(names) <= 2:
        return f"({', '.join(names)})"
    return f"({names[0]}, {names[1]} +{len(names) - 2} more)"


def format_legacy_suffix(combo_text):
    """Build the collapsed-title suffix for legacy loopback mode.

    Strips the trailing " (WASAPI Loopback)" marker from the combo label.
    """
    wasapi_suffix = " (WASAPI Loopback)"
    text = combo_text
    if text.endswith(wasapi_suffix):
        text = text[: -len(wasapi_suffix)]
    return f"({text})"


from PyQt6.QtWidgets import QDialog

class SourceSelector(QDialog):
    """Audio source selection widget with per-app and system-audio options.

    Signals:
        devices_changed: Emitted when device selection or app checkboxes change.
        mic_changed: Emitted with the selected mic device index (or None).
    """

    devices_changed = pyqtSignal()
    mic_changed = pyqtSignal(object)
    # Emitted when all checked apps go inactive during recording
    apps_went_inactive = pyqtSignal()
    # Emitted when a checked app becomes active (for auto-record)
    apps_became_active = pyqtSignal()
    # Emitted whenever check_device_mismatches recomputes mic_mismatch /
    # output_mismatch, so the preflight verdict can be refreshed.
    mismatch_changed = pyqtSignal()

    _BASE_TITLE = "Audio Sources"

    def __init__(self, config=None, parent=None, com_poller=None):
        super().__init__(parent)
        self._config = config
        self._com_poller = com_poller
        self._mic_devices = []
        self._loopback_devices = []
        self._last_app_devices = {}
        self.mic_mismatch = None
        self.output_mismatch = None
        self._win11 = is_windows_11()
        self._auto_refresh_timer = None
        self._had_active_apps = False
        self._setup_ui()
        self.refresh_devices()
        self._restore_capture_mode()
        self._update_section_title()

        if self._win11:
            self._start_auto_refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Audio sources dialog
        content = layout
        self.setWindowTitle(self._BASE_TITLE)

        # Header: icon + title + one-line explanation of what this dialog
        # controls, so it reads as a destination rather than a bare list of
        # dropdowns (Screen 3 of the Sources-dialog redesign).
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 11)
        header_layout.setSpacing(11)
        header_icon = QLabel()
        header_icon.setPixmap(colored_pixmap("waveform", "#9184d9", 20))
        header_layout.addWidget(header_icon)
        header_text = QVBoxLayout()
        header_text.setSpacing(2)
        header_title = QLabel(self._BASE_TITLE)
        header_title.setObjectName("sourcesHeaderTitle")
        header_subtitle = QLabel("What TalkTrack records when you press Record")
        header_subtitle.setObjectName("sourcesHeaderSubtitle")
        header_text.addWidget(header_title)
        header_text.addWidget(header_subtitle)
        header_layout.addLayout(header_text, 1)
        content.addWidget(header)

        # Shown instead of enabling all the controls below while a
        # recording is in progress (set_enabled(False)) so the dialog
        # explains itself rather than just going gray.
        self._recording_lock_notice = QLabel(
            "Sources are locked while recording — stop or pause to change them."
        )
        self._recording_lock_notice.setObjectName("sourcesLockNotice")
        self._recording_lock_notice.setWordWrap(True)
        self._recording_lock_notice.setVisible(False)
        content.addWidget(self._recording_lock_notice)

        content.addWidget(_group_heading("YOUR VOICE", "microphone"))

        # Microphone selector
        mic_row = QHBoxLayout()
        mic_label = QLabel("Microphone:")
        mic_label.setFixedWidth(_LABEL_WIDTH)
        mic_row.addWidget(mic_label)

        self.mic_combo = QComboBox()
        self.mic_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mic_combo.currentIndexChanged.connect(self._save_mic_selection)
        self.mic_combo.currentIndexChanged.connect(
            lambda: self._sync_combo_tooltip(self.mic_combo)
        )
        self.mic_combo.currentIndexChanged.connect(
            lambda: self.check_device_mismatches()
        )
        mic_row.addWidget(self.mic_combo, 1)
        content.addLayout(mic_row)



        # Second microphone selector (hidden by default)
        self._mic2_row_widget = QWidget()
        mic2_row = QHBoxLayout(self._mic2_row_widget)
        mic2_row.setContentsMargins(0, 0, 0, 0)
        mic2_label = QLabel("Second mic:")
        mic2_label.setFixedWidth(_LABEL_WIDTH)
        mic2_row.addWidget(mic2_label)

        self.mic2_combo = QComboBox()
        self.mic2_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.mic2_combo.currentIndexChanged.connect(self._save_mic_selection)
        self.mic2_combo.currentIndexChanged.connect(
            lambda: self._sync_combo_tooltip(self.mic2_combo)
        )
        mic2_row.addWidget(self.mic2_combo, 1)
        content.addWidget(self._mic2_row_widget)

        mic_count = self._config.get("audio", "mic_count") if self._config else 1
        self._mic2_row_widget.setVisible(mic_count >= 2)

        content.addWidget(_group_heading("THE CALL", "phone-incoming"))

        # System audio section
        if self._win11:
            self._setup_per_app_ui(content)
        else:
            self._setup_legacy_ui(content)



        # Warning label for per-app capture failures
        self._capture_warning = QLabel("")
        self._capture_warning.setObjectName("captureWarning")
        self._capture_warning.setVisible(False)
        content.addWidget(self._capture_warning)

        # Footer verdict: reuses the same PreflightWidget + pure truth-table
        # functions that drive the main capture bar, so "ready to record"
        # never disagrees between the two surfaces.
        footer = QFrame()
        footer.setObjectName("sourcesFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(11, 11, 11, 11)
        self._verdict = PreflightWidget()
        footer_layout.addWidget(self._verdict)
        content.addWidget(footer)

        # Keep the title and footer verdict in sync with selection state.
        self.loopback_combo.currentIndexChanged.connect(self._update_section_title)
        self.loopback_combo.currentIndexChanged.connect(self._save_loopback_selection)
        self.loopback_combo.currentIndexChanged.connect(lambda: self.check_device_mismatches())
        self.mic_combo.currentIndexChanged.connect(self._update_verdict)
        self.mismatch_changed.connect(self._update_verdict)
        if self.app_list is not None:
            self.app_list.itemChanged.connect(self._update_section_title)
            # Checking/unchecking an app can flip is_conferencing_blocked();
            # mismatch_changed is the general "preflight-relevant state
            # changed" signal MainWindow listens to.
            self.app_list.itemChanged.connect(self.mismatch_changed)



    def _setup_legacy_ui(self, parent_layout):
        """Original system audio dropdown (Win10 or fallback)."""
        sys_row = QHBoxLayout()
        sys_label = QLabel("Output:")
        sys_label.setFixedWidth(_LABEL_WIDTH)
        sys_row.addWidget(sys_label)

        self.loopback_combo = QComboBox()
        sys_row.addWidget(self.loopback_combo, 1)
        parent_layout.addLayout(sys_row)

        self.app_list = None
        self.mode_group = None

    def _setup_per_app_ui(self, parent_layout):
        """Per-app audio picker (Win11)."""
        self.mode_group = QButtonGroup(self)
        self.radio_per_app = QRadioButton("Selected apps")
        self.radio_per_app.setObjectName("captureMode")
        self.radio_per_app.setToolTip("Capture audio only from the apps checked below.")
        self.radio_legacy = QRadioButton("All system audio")
        self.radio_legacy.setObjectName("captureMode")
        self.radio_legacy.setToolTip("Capture all system audio output (WASAPI loopback).")
        self.mode_group.addButton(self.radio_per_app, 0)
        self.mode_group.addButton(self.radio_legacy, 1)
        self.radio_per_app.setChecked(True)
        self.mode_group.idToggled.connect(self._on_mode_changed)

        # Mode cards replace bare radios: each names the consequence of
        # picking it, so the choice isn't a coin flip between two labels
        # (Screen 3 of the Sources-dialog redesign).
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        per_app_card = _ModeCard(
            self.radio_per_app,
            "One or more apps only. Conferencing apps block this and "
            "record silent.",
        )
        legacy_card = _ModeCard(
            self.radio_legacy,
            "Everything your speakers play. Works with Teams, Zoom and "
            "WebEx.",
            recommended=True,
        )
        mode_row.addWidget(per_app_card, 1)
        mode_row.addWidget(legacy_card, 1)
        parent_layout.addLayout(mode_row)

        # App list (checkable). Scrollbar shows automatically when the list
        # has more items than its visible area can fit.
        self.app_list = QListWidget()
        self.app_list.setObjectName("appAudioList")
        self.app_list.setMinimumHeight(120)
        self.app_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.app_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        parent_layout.addWidget(self.app_list, 1)

        # Conferencing apps (Teams/Zoom/WebEx/etc.) set AUDCLNT_STREAMFLAGS_
        # EXCLUDE_FROM_PROCESS_LOOPBACK_CAPTURE on their call stream, so
        # per-app loopback gets silence. Rather than a banner that only
        # appears after the user has already checked one, each such row in
        # the list above is rendered disabled with the reason appended to
        # its own label (see _refresh_app_list) — legacy mode (device-level
        # WASAPI tap) bypasses the opt-out and is unaffected.

        # Output picker, shown instead of the app list in "All system audio"
        # mode. Carries its own label row so it doesn't appear as a bare
        # dropdown with nothing saying what it selects.
        self._loopback_row_widget = QWidget()
        loopback_row = QHBoxLayout(self._loopback_row_widget)
        loopback_row.setContentsMargins(0, 0, 0, 0)
        loopback_label = QLabel("Output:")
        loopback_label.setFixedWidth(_LABEL_WIDTH)
        loopback_row.addWidget(loopback_label)
        self.loopback_combo = QComboBox()
        loopback_row.addWidget(self.loopback_combo, 1)
        self._loopback_row_widget.setVisible(False)
        parent_layout.addWidget(self._loopback_row_widget)

    def _on_mode_changed(self, button_id, checked):
        if not checked:
            return
        if self.app_list is not None:
            is_per_app = button_id == 0
            self.app_list.setVisible(is_per_app)
            self._loopback_row_widget.setVisible(not is_per_app)
        self._update_section_title()
        self.check_device_mismatches()
        self.mismatch_changed.emit()

    def _on_section_toggled(self, expanded):
        pass

    def _selected_sources_text(self):
        """Build the ' (...)' suffix shown when the section is collapsed."""
        if self._win11 and self.is_per_app_mode():
            names = []
            if self.app_list is not None:
                for i in range(self.app_list.count()):
                    item = self.app_list.item(i)
                    if item.checkState() == Qt.CheckState.Checked:
                        names.append(item.text().split("  (")[0])
            return format_per_app_suffix(names)

        # Legacy loopback mode (Win10 or Win11 with "All system audio" radio)
        if self.loopback_combo.currentData() is None:
            return "(No system audio)"
        return format_legacy_suffix(self.loopback_combo.currentText())

    def _update_section_title(self):
        """Update the header text to summarize current capture mode / sources."""
        self.setWindowTitle(f"{self._BASE_TITLE} {self._selected_sources_text()}")
        self._update_verdict()

    def is_conferencing_blocked(self):
        """True if a checked app in per-app mode opts out of process-loopback
        capture (see CONFERENCING_APPS) — legacy mode is unaffected."""
        if self.app_list is None:
            return False
        if self.get_capture_mode() != "per_app":
            return False
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            # Strip the trailing "  (N processes)" / "  (not in call)" /
            # blocked-reason suffix added at list-render time so the match
            # keys on name only.
            label = item.text().split("  ")[0]
            if label in CONFERENCING_APPS:
                return True
        return False

    def _update_verdict(self):
        """Recompute the footer verdict from the same truth-table functions
        that drive the main capture bar's pre-flight badge."""
        if not hasattr(self, "_verdict"):
            return
        has_mic = self.get_selected_mic() is not None
        mic_check = preflight_status.compute_mic_check(has_mic, self.mic_mismatch)

        if self.is_per_app_mode():
            has_source = bool(self.get_selected_app_pids())
        else:
            has_source = self.get_selected_loopback() is not None
        call_check = preflight_status.compute_call_check(
            has_source, self.is_conferencing_blocked(), self.output_mismatch
        )

        verdict, title, subtitle = preflight_status.compute_verdict(
            mic_check, call_check, (preflight_status.READY, "", "")
        )
        self._verdict.set_verdict(verdict, title, subtitle)

    def _start_auto_refresh(self):
        if self._auto_refresh_timer is None:
            self._auto_refresh_timer = QTimer(self)
            self._auto_refresh_timer.timeout.connect(self._refresh_app_list)
        self._auto_refresh_timer.start(5000)

    def _stop_auto_refresh(self):
        if self._auto_refresh_timer:
            self._auto_refresh_timer.stop()

    def set_recording_active(self, active):
        """Switch to faster polling (1s) during recording for quicker call-end detection."""
        if self._com_poller is not None:
            self._com_poller.set_interval(1.0 if active else 2.0)
        if self._auto_refresh_timer and self._auto_refresh_timer.isActive():
            self._auto_refresh_timer.start(2000 if active else 5000)

    def _refresh_app_list(self):
        """Update the app list with currently active audio apps."""
        if self.app_list is None:
            return
        if self._com_poller is None:
            return

        snapshot = self._com_poller.get_snapshot()
        apps = snapshot.get("audio_apps", [])
        self.check_device_mismatches(snapshot.get("app_devices", {}))

        # Filter out hidden apps
        hidden = []
        if self._config:
            try:
                hidden = self._config.get("audio", "hidden_devices") or []
            except (KeyError, TypeError):
                pass
        if hidden:
            hidden_lower = [h.lower() for h in hidden]
            apps = [a for a in apps if not any(
                h in a["name"].lower() for h in hidden_lower
            )]

        # Remember which app names were checked (stable across PID changes)
        checked_names = set()
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                checked_names.add(item.text().split("  (")[0])

        # On first load (empty list), seed from saved config
        if not checked_names and self._config:
            try:
                saved_apps = self._config.get("audio", "selected_apps")
                if saved_apps:
                    checked_names = set(saved_apps)
            except (KeyError, TypeError):
                pass

        self.app_list.clear()

        # Track whether any checked apps are still active
        any_checked_active = False

        for app in apps:
            blocked = app["name"] in CONFERENCING_APPS
            if app.get("active", False):
                label = f"{app['name']}  ({len(app['pids'])} process{'es' if len(app['pids']) > 1 else ''})"
            else:
                label = f"{app['name']}  (not in call)"
            if blocked:
                label += "  · blocks per-app capture"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, app["pids"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if blocked:
                # Disabled rather than merely warned-about: prevents new
                # silent-recording selections outright. A row already
                # checked from a saved config stays checked (still grayed)
                # so is_conferencing_blocked() keeps surfacing it via the
                # footer verdict until the user picks "All system audio".
                item.setToolTip(
                    f"{app['name']} blocks per-app capture during calls. "
                    "Switch to \"All system audio\" to record it."
                )
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            if app["name"] in checked_names:
                item.setCheckState(Qt.CheckState.Checked)
                if app.get("active", False):
                    any_checked_active = True
            else:
                item.setCheckState(Qt.CheckState.Unchecked)
            self.app_list.addItem(item)

        if self.app_list.count() == 0:
            item = QListWidgetItem("No audio apps detected")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.app_list.addItem(item)

        # Detect transition: checked apps were active, now all inactive
        if checked_names and self._had_active_apps and not any_checked_active:
            self.apps_went_inactive.emit()
        # Detect transition: no checked apps were active, now at least one is
        if checked_names and not self._had_active_apps and any_checked_active:
            self.apps_became_active.emit()
        self._had_active_apps = any_checked_active
        self._update_section_title()

    def has_active_checked_apps(self):
        """Whether any checked app is currently reported as active.

        Reflects the most recent snapshot from the COM session poller. Used
        by the auto-record threshold to confirm sustained activity before
        starting a recording.
        """
        return bool(self._had_active_apps)

    def refresh_devices(self):
        # Block signals while rebuilding combos so clear/addItem don't
        # trigger _save_mic_selection with stale values
        self.mic_combo.blockSignals(True)
        self.mic2_combo.blockSignals(True)

        self.mic_combo.clear()

        # Get hidden device patterns from config
        hidden = []
        if self._config:
            try:
                hidden = self._config.get("audio", "hidden_devices") or []
            except (KeyError, TypeError):
                pass

        # Microphone devices
        self._mic_devices = get_input_devices(hidden_devices=hidden)
        self.mic_combo.addItem("(None - don't record microphone)", None)
        default_mic = get_default_mic()
        default_mic_idx = 0

        for i, dev in enumerate(self._mic_devices):
            label = f"{dev['name']} ({dev['hostapi']})"
            self.mic_combo.addItem(label, dev["index"])
            self.mic_combo.setItemData(
                i + 1,
                _fullest_device_name(dev["name"], self._mic_devices),
                Qt.ItemDataRole.ToolTipRole,
            )
            if dev["index"] == default_mic:
                default_mic_idx = i + 1

        # Restore saved mic or fall back to system default
        last_mic = ""
        if self._config:
            try:
                last_mic = self._config.get("audio", "last_mic") or ""
            except (KeyError, TypeError):
                pass

        if last_mic:
            idx = self.mic_combo.findText(last_mic)
            if idx >= 0:
                self.mic_combo.setCurrentIndex(idx)
            elif default_mic_idx > 0:
                self.mic_combo.setCurrentIndex(default_mic_idx)
        elif default_mic_idx > 0:
            self.mic_combo.setCurrentIndex(default_mic_idx)

        # Second microphone (same device list)
        self.mic2_combo.clear()
        self.mic2_combo.addItem("(None - don't record second mic)", None)
        for i, dev in enumerate(self._mic_devices):
            label = f"{dev['name']} ({dev['hostapi']})"
            self.mic2_combo.addItem(label, dev["index"])
            self.mic2_combo.setItemData(
                i + 1,
                _fullest_device_name(dev["name"], self._mic_devices),
                Qt.ItemDataRole.ToolTipRole,
            )

        # Restore saved mic 2
        last_mic2 = ""
        if self._config:
            try:
                last_mic2 = self._config.get("audio", "last_mic2") or ""
            except (KeyError, TypeError):
                pass
        if last_mic2:
            idx = self.mic2_combo.findText(last_mic2)
            if idx >= 0:
                self.mic2_combo.setCurrentIndex(idx)

        self.mic_combo.blockSignals(False)
        self.mic2_combo.blockSignals(False)
        # blockSignals() suppressed currentIndexChanged above, so the
        # tooltip sync that piggybacks on it needs an explicit call here.
        self._sync_combo_tooltip(self.mic_combo)
        self._sync_combo_tooltip(self.mic2_combo)

        # System audio dropdown - always populated. Signals are blocked while
        # rebuilding so the intermediate selections clear()/addItem() produce
        # can't overwrite the saved choice through _save_loopback_selection.
        self.loopback_combo.blockSignals(True)
        self.loopback_combo.clear()
        self._loopback_devices = get_system_audio_devices(hidden_devices=hidden)
        self.loopback_combo.addItem("(None - don't record system audio)", None)
        default_output = get_default_output()
        default_lb_idx = 0

        for i, dev in enumerate(self._loopback_devices):
            label = f"{dev['name']} (WASAPI Loopback)"
            self.loopback_combo.addItem(label, dev["index"])
            if dev["index"] == default_output:
                default_lb_idx = i + 1

        # Saved choice wins over the system default: the Windows default
        # output is often not the endpoint the meeting app renders to, and
        # capturing the wrong one yields a silent (then deleted) track.
        last_loopback = ""
        if self._config:
            try:
                last_loopback = self._config.get("audio", "last_loopback") or ""
            except (KeyError, TypeError):
                pass

        saved_idx = self.loopback_combo.findText(last_loopback) if last_loopback else -1
        active_lb_idx = self._active_output_row()
        if saved_idx >= 0:
            self.loopback_combo.setCurrentIndex(saved_idx)
        elif active_lb_idx > 0:
            # Nothing saved: an endpoint that is demonstrably producing
            # sound beats the nominal Windows default, which is regularly
            # not where the meeting app is playing.
            self.loopback_combo.setCurrentIndex(active_lb_idx)
        elif default_lb_idx > 0:
            self.loopback_combo.setCurrentIndex(default_lb_idx)
        elif self._loopback_devices:
            # Default device didn't match — pick the first one
            self.loopback_combo.setCurrentIndex(1)

        self.loopback_combo.blockSignals(False)
        self._update_section_title()

        # Refresh app list too
        if self._win11 and self.app_list is not None:
            self._refresh_app_list()

        self.devices_changed.emit()

    def get_selected_mic(self):
        return self.mic_combo.currentData()

    def get_selected_mic2(self):
        """Return second mic device index, or None if not enabled/selected."""
        if not self._mic2_row_widget.isVisible():
            return None
        return self.mic2_combo.currentData()

    def update_mic_count(self, count):
        """Show or hide the second microphone dropdown."""
        self._mic2_row_widget.setVisible(count >= 2)

    def get_selected_loopback(self):
        """Return loopback device index for system audio capture."""
        return self.loopback_combo.currentData()

    def get_selected_app_pids(self):
        """Return list of checked app PIDs (per-app mode only).

        Each app entry may have multiple PIDs (e.g., Zoom runs several
        processes). All PIDs for checked apps are returned.
        """
        if self.app_list is None:
            return []
        pids = []
        for i in range(self.app_list.count()):
            item = self.app_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                pid_data = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(pid_data, list):
                    pids.extend(pid_data)
                elif pid_data is not None:
                    pids.append(pid_data)
        return pids

    def get_capture_mode(self):
        """Return 'per_app' or 'legacy'."""
        if self.is_per_app_mode():
            return "per_app"
        return "legacy"

    def is_per_app_mode(self):
        """Check if per-app capture mode is selected."""
        if self.mode_group and self.radio_per_app.isChecked():
            return True
        return False

    def _restore_capture_mode(self):
        """Restore capture mode and selected apps from config."""
        if not self._config:
            return
        try:
            mode = self._config.get("audio", "capture_mode")
        except (KeyError, TypeError):
            return

        if self._win11 and self.mode_group:
            if mode == "legacy":
                self.radio_legacy.setChecked(True)
                # Explicitly set visibility in case signal didn't fire
                if self.app_list is not None:
                    self.app_list.setVisible(False)
                    self._loopback_row_widget.setVisible(True)
            else:
                self.radio_per_app.setChecked(True)

    def _sync_combo_tooltip(self, combo):
        """Mirror the current item's ToolTipRole data onto the combo box
        itself, so hovering the closed dropdown (not just the open popup
        list) surfaces the untruncated device name too."""
        tip = combo.itemData(combo.currentIndex(), Qt.ItemDataRole.ToolTipRole)
        combo.setToolTip(tip or "")

    def _save_mic_selection(self):
        """Persist mic choices immediately when the user changes a dropdown."""
        if self._config:
            mic1_text = self.mic_combo.currentText() if self.mic_combo.currentData() is not None else ""
            self._config.set("audio", "last_mic", mic1_text)
            mic2_text = self.mic2_combo.currentText() if self.mic2_combo.currentData() is not None else ""
            self._config.set("audio", "last_mic2", mic2_text)
        self.mic_changed.emit(self.mic_combo.currentData())

    def _active_output_row(self):
        """Combo row for the endpoint currently rendering audio, or 0.

        Returns a row index (not a device index) so the caller can treat 0
        as "no opinion" the same way it treats the default lookup.
        """
        if self._com_poller is None:
            return 0
        try:
            index = self._com_poller.active_output_index(self._loopback_devices)
        except Exception:
            logger.exception("Failed to read active output endpoint")
            return 0
        if index is None:
            return 0
        for row in range(1, self.loopback_combo.count()):
            if self.loopback_combo.itemData(row) == index:
                return row
        return 0

    def _save_loopback_selection(self):
        """Persist the system-audio choice immediately when it changes."""
        if not self._config:
            return
        self._config.set("audio", "last_loopback", self._loopback_text())

    def _loopback_text(self):
        """The combo label to save, or "" for the (None) entry."""
        if self.loopback_combo.currentData() is None:
            return ""
        return self.loopback_combo.currentText()

    def save_capture_settings(self):
        """Save current capture mode, selected app names, and mic choices to config."""
        if not self._config:
            return
        self._config.set("audio", "capture_mode", self.get_capture_mode())

        # Save checked app names (not PIDs, since those change)
        selected_names = []
        if self.app_list is not None:
            for i in range(self.app_list.count()):
                item = self.app_list.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected_names.append(item.text().split("  (")[0])
        self._config.set("audio", "selected_apps", selected_names)

        # Save mic selections by name (device indices change across sessions)
        mic1_text = self.mic_combo.currentText() if self.mic_combo.currentData() is not None else ""
        self._config.set("audio", "last_mic", mic1_text)
        mic2_text = self.mic2_combo.currentText() if self.mic2_combo.currentData() is not None else ""
        self._config.set("audio", "last_mic2", mic2_text)
        self._config.set("audio", "last_loopback", self._loopback_text())

    def set_enabled(self, enabled):
        self.mic_combo.setEnabled(enabled)
        self.mic2_combo.setEnabled(enabled)
        self.loopback_combo.setEnabled(enabled)
        if self.app_list is not None:
            self.app_list.setEnabled(enabled)
        if self.mode_group:
            self.radio_per_app.setEnabled(enabled)
            self.radio_legacy.setEnabled(enabled)
        if hasattr(self, "_recording_lock_notice"):
            self._recording_lock_notice.setVisible(not enabled)

    def mark_capture_failures(self, failures):
        """Show/hide the ⚠ indicator when per-app activation fails for some PIDs.

        Args:
            failures: {pid: hresult_name_str} mapping. Empty dict clears the indicator.
        """
        if not failures:
            self._capture_warning.setVisible(False)
            self._capture_warning.setText("")
            self._capture_warning.setToolTip("")
            return

        # Resolve PID -> display name via the current app list entries.
        pid_to_name = {}
        if self.app_list is not None:
            for i in range(self.app_list.count()):
                item = self.app_list.item(i)
                pid_data = item.data(Qt.ItemDataRole.UserRole)
                name = item.text()
                if isinstance(pid_data, list):
                    for pid in pid_data:
                        pid_to_name[pid] = name
                elif pid_data is not None:
                    pid_to_name[pid_data] = name

        lines = []
        names_shown = set()
        for pid, err in failures.items():
            name = pid_to_name.get(pid, f"PID {pid}")
            if name in names_shown:
                continue
            names_shown.add(name)
            lines.append(f"{name}: {err}")

        self._capture_warning.setText(
            f"\u26a0 {len(names_shown)} app(s) could not be captured"
        )
        self._capture_warning.setToolTip("\n".join(lines))
        self._capture_warning.setVisible(True)

    def get_selected_mic_name(self):
        """Display name of the selected mic, for the capture bar's
        "CAPTURING" sources block. Public wrapper around the mismatch
        check's own lookup."""
        return self._selected_mic_name()

    def get_selected_source_name(self):
        """Display name for the call/system-audio side of the "CAPTURING"
        sources block: checked app names in per-app mode (a capture-mode
        label rather than the raw endpoint name in legacy mode — the mode
        is what matters there, not which device happens to be selected)."""
        if self.is_per_app_mode():
            if self.app_list is None:
                return None
            names = [
                self.app_list.item(i).text()
                for i in range(self.app_list.count())
                if self.app_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            return " + ".join(names) if names else None
        return "All system audio" if self._selected_output_name() else None

    def _selected_mic_name(self):
        idx = self.mic_combo.currentData()
        if idx is None:
            return None
        for dev in self._mic_devices:
            if dev.get("index") == idx:
                return dev.get("name")
        return None

    def _selected_output_name(self):
        idx = self.loopback_combo.currentData()
        if idx is None:
            return None
        for dev in self._loopback_devices:
            if dev.get("index") == idx:
                return dev.get("name")
        return None

    def check_device_mismatches(self, app_devices=None):
        """Recompute mic_mismatch / output_mismatch against the active
        conferencing app's own devices. Folds into the preflight verdict —
        see app/utils/device_mismatch.py and app/utils/preflight_status.py.
        """
        if app_devices is not None:
            self._last_app_devices = app_devices

        # Device-level (legacy) loopback taps the endpoint post-mix, so an
        # output mismatch there is real. Per-app capture taps the target
        # process's own stream directly and doesn't care what endpoint it
        # renders to.
        output_check_active = (not self._win11) or (self.get_capture_mode() == "legacy")

        result = compute_device_mismatches(
            current_mic_name=self._selected_mic_name(),
            current_output_name=self._selected_output_name(),
            output_check_active=output_check_active,
            app_devices=self._last_app_devices,
            conferencing_app_names=CONFERENCING_APPS,
        )

        changed = (result["mic"] != self.mic_mismatch) or (result["output"] != self.output_mismatch)
        self.mic_mismatch = result["mic"]
        self.output_mismatch = result["output"]
        if changed:
            self.mismatch_changed.emit()

