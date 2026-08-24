"""The double-click shrink chain (Qt-free, so it can be tested directly).

The app has one ordered chain of presentation states:

    full -> compact_bar -> pill -> full

Double-clicking the capture bar (or the strip) walks one step along it and
wraps. `ui.double_click_target` picks where a double-click *from the full
window* lands, so it selects an entry point into an otherwise fixed chain:
choosing "pill" yields full -> pill -> full, leaving the compact bar
reachable from the pill's own expand button but out of the cycle.

The minimize button is deliberately not part of this — it always performs an
ordinary Windows minimize.
"""

FULL = "full"
COMPACT_BAR = "compact_bar"
PILL = "pill"

TARGETS = (COMPACT_BAR, PILL)

_CHAIN = {
    COMPACT_BAR: PILL,
    PILL: FULL,
}


def next_presentation(current, double_click_target):
    """The state a double-click moves to from `current`.

    Both arguments are tolerated being junk: a hand-edited settings.json or
    an unexpected state must never leave the app with no way back to the
    full window.
    """
    if current == FULL:
        return double_click_target if double_click_target in TARGETS else COMPACT_BAR
    return _CHAIN.get(current, FULL)
