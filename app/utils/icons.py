"""Resolve and recolor vendored Phosphor SVG icons for Qt widgets.

Icons ship as vendored SVGs, but they don't uniformly use `currentColor`
for every shape (e.g. resources/icons/warning.svg's warning-dot circle has
no fill attribute at all, defaulting to plain black), so there's no single
reliable way to retint them via QSS the way a browser would. Rendering the
SVG's alpha shape and compositing a solid color into it
(CompositionMode_SourceIn) works regardless of what color/fill the source
file happens to specify.
"""
from functools import lru_cache
from pathlib import Path

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

_ICONS_DIR = Path(__file__).resolve().parent.parent.parent / "resources" / "icons"


def icon_path(name):
    """Absolute path to resources/icons/<name>.svg, resolved from this
    file's own location so it doesn't depend on the process's cwd."""
    return _ICONS_DIR / f"{name}.svg"


def _device_pixel_ratio():
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen else 1.0


@lru_cache(maxsize=512)
def _colored_pixmap_cached(name, color, size, dpr):
    """Render a vendored SVG icon tinted a single solid color, rasterised at
    `size * dpr` device pixels so it stays crisp on high-DPI displays."""
    renderer = QSvgRenderer(str(icon_path(name)))
    device_size = max(1, round(size * dpr))
    pixmap = QPixmap(QSize(device_size, device_size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def colored_pixmap(name, color, size=20):
    """Render a vendored SVG icon tinted a single solid color.

    Cached per (name, color, size, device pixel ratio) — the same handful of
    icon/color/size combinations get requested repeatedly (every library row,
    every segment row), and re-rendering the SVG each time is wasted work.
    """
    return _colored_pixmap_cached(name, color, size, _device_pixel_ratio())
