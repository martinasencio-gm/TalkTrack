"""Headless batch transcription — the Task Scheduler companion to the app.

Nothing in this package may import PyQt6 widgets. `runner` creates a
QCoreApplication so the existing QThread workers can be reused verbatim,
but there is no GUI and no display in a scheduled run.
"""
