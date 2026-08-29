"""Design tokens: colour, type scale, and spacing constants.

Six widgets — the compact/pill floating chrome, recording controls, activity
indicator, tag dialog, and the two batch dialogs — each hand-typed their own
copy of what is actually one consistent dark palette (the same near-black
surface shows up as five slightly different hex strings, `#e9e9ed` text is
retyped dozens of times, etc.). None of that changes here: every constant
below is the exact value already in use at its call sites, just named once
instead of re-invented per file. This is deduplication, not a redesign — see
`.claude/rules/ui-patterns.md` for the separate, already-documented in-app
Catppuccin Mocha palette (`resources/style.qss`), which this does not touch.

Usage: build QSS as an f-string and reference these by name, e.g.
    from app.ui import tokens
    widget.setStyleSheet(f"QLabel {{ color: {tokens.TEXT}; }}")
"""

# --- Colour: floating/overlay chrome palette -------------------------------
# (compact strip, pill, meeting toast, activity indicator, tag dialog, batch
# dialogs — the widgets that draw their own frame rather than living inside
# the main window's styled panels)

# Text
TEXT = "#e9e9ed"          # primary text on dark chrome
TEXT_SECONDARY = "#cfd3e5"  # secondary/label text, one step down from TEXT
TEXT_MUTED = "#9397ab"    # de-emphasized text (timestamps, hints)
TEXT_MUTED_DIM = "#75798c"  # further-de-emphasized text (disabled-ish, captions)
TEXT_SUBTLE = "#a6adc8"   # subtlest text variant (activity_indicator footnotes)

# Surfaces (near-black backgrounds; several were independently hand-picked
# for the same "floating chrome background" role — kept distinct here only
# because collapsing them would be a visual change, however small)
SURFACE_1 = "#292b31"
SURFACE_2 = "#23252f"
SURFACE_3 = "#1b1d2c"
SURFACE_4 = "#14161f"
SURFACE_5 = "#12141f"
SURFACE_6 = "#161826"

# Borders / mid-tones
BORDER = "#3f424d"
BORDER_MUTED = "#4d5063"
BORDER_SUBTLE = "#3b3e4d"

# Accent (lavender — record button, active/primary emphasis)
ACCENT = "#9184d9"
ACCENT_STRONG = "#423a6a"
ACCENT_LIGHT = "#d2cefd"

# Status colours (shared meaning across every one of these widgets)
RED = "#f38ba8"       # error / mute / stop / danger
YELLOW = "#f9e2af"    # warning / hot / paused
GREEN = "#a6e3a1"     # healthy / success
PEACH = "#fab387"     # queued / waiting
MAUVE = "#cba6f7"     # tag accent

# Danger-button state family (delete/remove confirmations —
# batch_process_info_dialog's cancel/remove buttons)
DANGER_BG = "#7d2a2a"
DANGER_BORDER = "#b34d4d"
DANGER_HOVER = "#9e3535"
DANGER_PRESSED_BG = "#5c2020"
DANGER_PRESSED_BORDER = "#8c3838"
DANGER_HOVER_ALT = "#8c3838"
ON_DANGER = "#ffffff"

# --- Type scale --------------------------------------------------------
# 5 steps, replacing 7 distinct px values that had drifted in by half-pixel
# increments. The two outliers (14px, 16px) snap to their nearest neighbour
# below — a 1px difference, not a visual redesign.
TYPE_XS = "10px"
TYPE_SM = "11px"
TYPE_MD = "12px"
TYPE_BASE = "13px"   # also covers the former 14px outlier
TYPE_LG = "15px"     # also covers the former 16px outlier

# --- Spacing -------------------------------------------------------------
SPACE_1 = 4
SPACE_2 = 8
SPACE_3 = 12
SPACE_4 = 16
