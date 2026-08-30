"""A two-pane QSplitter whose handle can collapse the second pane to zero width.

Collapse state lives on the splitter, not the handle - the handle is just a
button that calls back into it. That keeps the state machine in one place even
though Qt asks the splitter to create a fresh handle object internally.
"""
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QSplitter, QSplitterHandle, QToolButton, QVBoxLayout


class CollapsibleSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()

        self._button = QToolButton(self)
        self._button.setAutoRaise(True)
        self._button.setFixedSize(12, 28)
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setStyleSheet(
            "QToolButton { background-color: rgba(255, 255, 255, 30); "
            "border: none; border-radius: 3px; color: #e9e9ed; }"
            "QToolButton:hover { background-color: rgba(255, 255, 255, 60); }"
        )
        self._button.clicked.connect(self.splitter().toggle_collapse)
        layout.addWidget(self._button)

        layout.addStretch()
        self._update_arrow(self.splitter().is_collapsed())
        self.splitter().collapse_changed.connect(self._update_arrow)

    def _update_arrow(self, collapsed):
        self._button.setText("▸" if collapsed else "◂")
        self._button.setToolTip("Expand panel" if collapsed else "Collapse panel")

    def _draggable(self):
        # The spec's own words: "the arrow button is the only collapse
        # path" (docs/superpowers/specs/2026-08-29-collapsible-panels-design.md).
        # setCollapsible(1, False) on the owning splitter blocks dragging
        # TO zero but Qt has no matching flag for dragging FROM zero, so
        # while collapsed this handle sits pixel-adjacent to the next
        # splitter's own handle (nothing occupies the collapsed pane's
        # width) with no visual seam between them. A user reaching for
        # that neighboring handle to resize an unrelated pane can just as
        # easily grab this one instead, and dragging THIS one can only
        # ever do one thing: pull the collapsed pane back open. Refusing
        # the drag here (button clicks still land on the button, which is
        # a child widget handled separately) makes the button the only
        # way to reopen it, matching the spec, and leaves the neighboring
        # handle as the sole way to resize past a collapsed pane.
        return not self.splitter().is_collapsed()

    def mousePressEvent(self, event):
        if not self._draggable():
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self._draggable():
            event.ignore()
            return
        super().mouseMoveEvent(event)


class CollapsibleSplitter(QSplitter):
    """Two-pane splitter; index 1 (the right/second pane) is what collapses."""

    # Fired first, before any size is touched, with the state being toggled
    # to. A listener that needs to resize the containing window (so there's
    # no leftover width for Qt to hand back to the right pane, or enough
    # width to actually restore into) must do it here, synchronously - by
    # the time collapse_changed fires the resize is too late to matter.
    about_to_toggle = pyqtSignal(bool)
    collapse_changed = pyqtSignal(bool)

    def __init__(self, orientation=Qt.Orientation.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self._collapsed = False
        self._expanded_size = None
        # One-shot: True only for the toggle_collapse() call immediately
        # following set_expanded_size() — see both methods below.
        self._expanded_size_seeded = False
        self.splitterMoved.connect(self._reconcile_collapsed_state)

    def createHandle(self):
        return CollapsibleSplitterHandle(self.orientation(), self)

    def is_collapsed(self):
        return self._collapsed

    def toggle_collapse(self):
        target = not self._collapsed
        self.about_to_toggle.emit(target)

        sizes = self.sizes()
        if len(sizes) < 2:
            return
        total = sum(sizes)
        # The owning window calls setCollapsible(1, False) so a stray drag
        # can never leave pane 1 at size 0 without _collapsed knowing about
        # it. But once pane 1 holds a widget with a non-trivial
        # minimumSizeHint(), that same False also blocks OUR OWN
        # programmatic setSizes([..., 0]) below - Qt clamps to the minimum
        # size hint for a non-collapsible pane regardless of who calls
        # setSizes. Flip collapsible on for just this resize, then put back
        # whatever the owner configured, so drag-to-zero stays blocked while
        # the button can still reach exactly 0.
        was_collapsible = self.isCollapsible(1)
        self.setCollapsible(1, True)
        try:
            if target:
                if self._expanded_size_seeded:
                    # A caller (MainWindow's startup restore) just supplied
                    # a known-good width via set_expanded_size() because
                    # sizes() right now would only report Qt's clamped
                    # pre-show geometry -- honor the seed once instead of
                    # immediately clobbering it with that same bad reading.
                    self._expanded_size_seeded = False
                else:
                    self._expanded_size = sizes[1]
                self.setSizes([total, 0])
            else:
                restore = self._expanded_size or max(total // 3, 1)
                restore = min(restore, total)
                self.setSizes([total - restore, restore])
        finally:
            self.setCollapsible(1, was_collapsible)
        self._collapsed = target
        self.collapse_changed.emit(self._collapsed)

    def set_collapsed(self, collapsed):
        """Apply a restored state (e.g. on startup) without a double toggle."""
        if collapsed == self._collapsed:
            return
        self.toggle_collapse()

    def _reconcile_collapsed_state(self, pos, index):
        """MainWindow blocks interactive drag-to-zero via
        setCollapsible(1, False), but that only blocks the *destination*
        size, not the direction: a drag that starts from an already-zero
        pane and moves it back open bypasses toggle_collapse() entirely,
        leaving _collapsed stuck True over a now-visible pane. Since
        drag-to-zero is blocked, a drag can only ever produce the
        collapsed-to-expanded transition here, never the reverse, so only
        that one direction needs reconciling."""
        if self._collapsed and self.sizes()[1] > 0:
            self._collapsed = False
            self.collapse_changed.emit(False)

    def set_expanded_size(self, pixels):
        """Seed the size toggle_collapse() will restore to on next expand.
        Used by MainWindow's startup restore, where sizes() read right after
        the pre-show setSizes() call reports Qt's clamped interim geometry,
        not the value that will actually apply once show() runs a real
        resize — so the snapshot toggle_collapse() would otherwise take is
        wrong for a column being restored collapsed.

        Sets the one-shot seeded flag too: if the very next toggle_collapse()
        call is the initial collapse (MainWindow's _restore_panel_collapse_state
        calling set_collapsed(True) on this still-expanded, freshly
        constructed splitter), that call must not immediately overwrite this
        seed with the same bad sizes() reading it exists to correct."""
        if pixels and pixels > 0:
            self._expanded_size = pixels
            self._expanded_size_seeded = True

    def clear_expanded_size_seed(self):
        """Cancel a pending set_expanded_size() seed without consuming it.

        MainWindow calls this right after _restore_panel_collapse_state()
        finishes, for every splitter, regardless of whether that splitter
        was actually restored collapsed. If it WAS restored collapsed,
        toggle_collapse() already consumed the seed (flag is False by now,
        so this is a no-op). If it was NOT restored collapsed -- the common
        case, since transcript_collapsed/inspector_collapsed both default
        to False -- the seed would otherwise sit pending on a fully
        expanded splitter until the user's first real collapse click of
        the session, and toggle_collapse() would wrongly treat that first
        real collapse as the startup restore, consuming the stale
        startup-seeded width instead of capturing the live (possibly
        since-resized) pane size. Clearing it here scopes the seed to
        exactly the startup-restore call it exists for."""
        self._expanded_size_seeded = False
