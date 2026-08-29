"""Pure fraction<->pixel math for saving/restoring splitter widths relative
to the active screen's resolution, not the window's own width or raw pixels.

No Qt here by design — MainWindow resolves the active QScreen
(app/utils/screen_utils.get_active_screen) and calls these with plain
numbers, keeping the math testable without constructing any widgets.
See docs/superpowers/specs/2026-08-29-collapsible-panels-design.md.
"""


def fraction_for_size(pixel_size, screen_width):
    """Convert a pixel size into a fraction of the screen width, for saving.

    Returns None when screen_width is missing or non-positive — a caller
    with no usable screen shouldn't persist a divide-by-zero result.
    """
    if not screen_width or screen_width <= 0:
        return None
    return pixel_size / screen_width


def resolve_pane_size(fraction, screen_width, default_size):
    """Convert a saved fraction back into a pixel size, for restoring.

    Falls back to default_size when the fraction was never saved (None) or
    the current screen width is unusable. Floors at 1px so a tiny fraction
    can never produce a zero or negative setSizes() entry.
    """
    if fraction is None:
        return default_size
    if not screen_width or screen_width <= 0:
        return default_size
    return max(1, round(fraction * screen_width))


def resolve_splitter_sizes(fractions, keys, screen_width, default_sizes):
    """Resolve one splitter's setSizes() list from saved fractions.

    fractions: the ui.panel_fractions dict (key -> fraction or None).
    keys: the panel_fractions key for each pane, in setSizes() order; a
        None entry means "always use the default for this pane" — used for
        splitter1's pane 0, which holds splitter2 itself rather than a
        single fraction-tracked column.
    default_sizes: fallback pixel sizes, same order as keys.
    """
    sizes = []
    for key, default in zip(keys, default_sizes):
        if key is None:
            sizes.append(default)
        else:
            sizes.append(resolve_pane_size(fractions.get(key), screen_width, default))
    return sizes
