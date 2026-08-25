"""Utilities for multi-screen geometry, monitor resolution, and widget placement."""
from PyQt6.QtCore import QPoint, QRect, QSize
from PyQt6.QtGui import QCursor, QGuiApplication
from PyQt6.QtWidgets import QApplication, QWidget


def get_active_screen(reference_widget=None):
    """Resolve the active QScreen based on parent window, cursor, or primary display.

    Hierarchy:
    1. If a visible, non-minimized reference_widget exists, use its screen.
    2. Else, use the screen containing the current mouse cursor position.
    3. Fallback to QApplication.primaryScreen().
    """
    if reference_widget is not None and isinstance(reference_widget, QWidget):
        if reference_widget.isVisible() and not reference_widget.isMinimized():
            # In Qt 6, QWidget has a .screen() method returning its QScreen
            try:
                screen = reference_widget.screen()
                if screen is not None:
                    return screen
            except Exception:
                pass
            # Alternatively check the center of the widget
            try:
                center = reference_widget.geometry().center()
                screen = QGuiApplication.screenAt(center)
                if screen is not None:
                    return screen
            except Exception:
                pass

    # Try screen at current mouse cursor
    try:
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is not None:
            return screen
    except Exception:
        pass

    return QGuiApplication.primaryScreen() or (QApplication.screens()[0] if QApplication.screens() else None)


def center_on_active_screen(widget, parent=None):
    """Center a widget/dialog within the available geometry of the active screen.

    If a visible parent is provided, centers over the parent while keeping
    within the active screen's available geometry.
    """
    screen = get_active_screen(parent or widget)
    if screen is None:
        return

    geo = screen.availableGeometry()
    w_size = widget.sizeHint() if widget.size().isEmpty() else widget.size()
    w_width = max(w_size.width(), widget.width(), 100)
    w_height = max(w_size.height(), widget.height(), 100)

    if parent is not None and isinstance(parent, QWidget) and parent.isVisible() and not parent.isMinimized():
        p_geo = parent.geometry()
        x = p_geo.x() + (p_geo.width() - w_width) // 2
        y = p_geo.y() + (p_geo.height() - w_height) // 2
    else:
        x = geo.x() + (geo.width() - w_width) // 2
        y = geo.y() + (geo.height() - w_height) // 2

    # Clamp to screen available geometry
    x = max(geo.left(), min(x, geo.right() - w_width))
    y = max(geo.top(), min(y, geo.bottom() - w_height))
    widget.move(x, y)


def position_corner_on_active_screen(widget, corner="bottom-right", margin=20, reference_widget=None):
    """Position a widget in a corner of the active screen's available geometry.

    Args:
        widget: The QWidget to position.
        corner: "bottom-right", "bottom-left", "top-right", or "top-left".
        margin: Pixel margin from the screen boundary.
        reference_widget: Optional reference QWidget to resolve the screen.
    """
    screen = get_active_screen(reference_widget or widget.parent())
    if screen is None:
        return

    geo = screen.availableGeometry()
    w_width = widget.width() if widget.width() > 0 else widget.sizeHint().width()
    w_height = widget.height() if widget.height() > 0 else widget.sizeHint().height()

    if corner == "bottom-right":
        x = geo.right() - w_width - margin
        y = geo.bottom() - w_height - margin
    elif corner == "bottom-left":
        x = geo.left() + margin
        y = geo.bottom() - w_height - margin
    elif corner == "top-right":
        x = geo.right() - w_width - margin
        y = geo.top() + margin
    else:  # top-left
        x = geo.left() + margin
        y = geo.top() + margin

    # Ensure coordinates are within screen
    x = max(geo.left(), min(x, geo.right() - w_width))
    y = max(geo.top(), min(y, geo.bottom() - w_height))
    widget.move(x, y)


def ensure_within_screens(x, y, width=100, height=40, reference_widget=None):
    """Ensure coordinates (x, y) fall within one of the connected screens.

    If (x, y) is on a disconnected display, shifts it to the active screen.
    Returns: (clamped_x, clamped_y)
    """
    screens = QApplication.screens()
    if not screens:
        return x, y

    target_point = QPoint(int(x), int(y))
    # Check if point falls on any connected screen
    for s in screens:
        if s.availableGeometry().contains(target_point):
            geo = s.availableGeometry()
            clamped_x = max(geo.left(), min(int(x), geo.right() - width))
            clamped_y = max(geo.top(), min(int(y), geo.bottom() - height))
            return clamped_x, clamped_y

    # If point is outside all screens (e.g. unplugged monitor), recover to active screen
    active_screen = get_active_screen(reference_widget)
    if active_screen:
        geo = active_screen.availableGeometry()
        return geo.center().x() - (width // 2), geo.top() + 24

    return x, y
