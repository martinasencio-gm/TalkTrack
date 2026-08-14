# Meeting Detection & Recording Suggestion — Design

**Issue:** [#65](https://github.com/ObscureAintSecure/TalkTrack/issues/65)
**Date:** 2026-08-14
**Status:** Approved

## Goal

Detect that a meeting is underway in Teams, Zoom or another conferencing app and
suggest starting a recording, instead of requiring the user to have pre-configured
per-app capture and pre-ticked the right app.

## Background: what already exists

TalkTrack already auto-records, in `main_window.py:599` (`_on_apps_became_active`):

- fires only when `capture_mode` is per-app (`source_selector.is_per_app_mode()`)
- fires only for apps the user already ticked (`has_active_checked_apps()`)
- starts recording silently — no suggestion, no consent
- `general.auto_record` defaults to `false`

It already solves one hard sub-problem worth preserving: a bare pycaw active-session
edge is not enough, because a Teams message chime flips the edge briefly. The existing
`auto_record_threshold` delays the start and re-checks activity before firing, so short
blips miss the cutoff.

Other reusable pieces:

| Piece | Location | Relevance |
|---|---|---|
| `get_active_audio_apps()` | `app/utils/audio_session_monitor.py` | Reports which apps have live audio sessions; already reattributes Teams' WebView2 children to `ms-teams` |
| 3-second poll of that function | `app/ui/source_selector.py:296` | An existing polling cadence to hook into |
| `find_overlapping_events()` | `app/integrations/outlook_calendar.py` | Calendar corroboration and meeting names |
| Tray `showMessage` | `app/ui/tray_icon.py:139` | Notification surface |
| `CalendarSuggestionBanner` | `app/ui/calendar_banner.py` | Precedent to mirror for the in-app banner |

**The gap is not detection from scratch.** It is that detection requires prior
configuration, and that it acts without asking.

## Detection rule

A necessary condition plus corroboration, rather than weighted numeric scoring —
arbitrary weights are hard to tune and harder to debug when a suggestion misfires.

- **Necessary trigger:** a curated meeting app has sustained audio activity past
  `threshold_seconds`. This is the existing, proven chime filter.
- **Confirmation:** at least one of
  - the app is capturing the microphone,
  - a calendar event overlaps now,
  - a meeting window title is present.
- **Shortcut:** microphone capture alone is treated as sufficient. A meeting app
  captures the mic only in an actual call, never for a notification chime.

Signals ranked by reliability: **mic capture > calendar > sustained audio > window
title.** Window title is brittle across app versions and locales, so it may only ever
confirm, never trigger, and ships disabled by default.

Any single signal failing degrades detection gracefully rather than breaking it.

## Components

Four units with clear boundaries. The decision logic is deliberately isolated from
both Qt and Windows so it can be tested as pure functions.

| Unit | Responsibility | Depends on |
|---|---|---|
| `app/utils/meeting_signals.py` | Probe raw signals: meeting apps with audio, mic-capture holders, matching window titles. Reports facts, makes no decisions. | pycaw, psutil |
| `app/integrations/meeting_detector.py` | Pure state machine. Given a signal snapshot, config and an injected clock, decide what should happen. No Qt, no Windows, no I/O. | — |
| `app/ui/meeting_banner.py` | In-app banner: meeting name, elapsed time, Record / Not now. Mirrors `calendar_banner.py`. | Qt |
| `MainWindow` wiring | Poll signals, feed the detector, route its decisions to tray and banner. | all above |

### Signal snapshot

`meeting_signals.probe()` returns a plain dict, which is the seam that makes the
detector testable without Windows:

```python
{
    "timestamp": float,          # monotonic seconds
    "audio_apps": [str, ...],    # curated meeting apps with an ACTIVE audio session
    "mic_capture_apps": [str, ...],  # apps holding an active capture session
    "meeting_titles": [str, ...],    # window titles matching meeting patterns
    "calendar_event": dict | None,   # overlapping event, or None
}
```

## State machine

```
IDLE       --meeting-app audio starts-->            CANDIDATE
CANDIDATE  --sustained N s AND >=1 confirming-->    SUGGESTED
CANDIDATE  --signals stop before N s-->             IDLE        (chime filtered)
SUGGESTED  --user accepts-->                        RECORDING
SUGGESTED  --user dismisses-->                      DISMISSED   (silent this session)
any        --signals absent for M s-->              IDLE        (session ends)
```

A **session** is a continuous run of activity from one app. It ends only after signals
stay absent for `session_end_seconds` (default 60). This matters: without it a brief
audio dropout would split one meeting into two sessions and re-prompt mid-call.

Dismissal is keyed to the session, so "Not now" means no for this meeting while
leaving a genuinely separate later meeting free to prompt.

In `auto` mode the machine is identical except `SUGGESTED` is skipped — it goes
straight to `RECORDING`, preserving today's behavior exactly.

## Settings

`general.auto_record` becomes a three-way mode under a new `meeting_detection`
section:

```python
"meeting_detection": {
    "mode": "suggest",          # "off" | "suggest" | "auto"
    "threshold_seconds": 5,     # sustained activity before acting
    "session_end_seconds": 60,  # absence before a session is considered over
    "use_mic_capture": True,    # strongest signal; opt-out for privacy/perf
    "use_calendar": True,       # reuses the existing Outlook integration
    "use_window_title": False,  # brittle across versions/locales
    "apps": ["ms-teams", "Teams", "Zoom", "Webex"],
}
```

**Default for new users:** `"suggest"` — that is the `DEFAULT_CONFIG` value above, so
a fresh install gets suggestions.

**Migration for existing users:** migration runs whenever a config already contains
`general.auto_record`, and it explicitly overwrites the default rather than inheriting
it — `auto_record: false` becomes `mode: "off"`, `auto_record: true` becomes
`mode: "auto"`. This distinction matters: without an explicit write, an existing user
who had deliberately turned auto-record off would silently inherit the new `"suggest"`
default and start getting prompts they never asked for. `general.auto_record_threshold`
carries into `threshold_seconds`. The legacy keys stay readable for one release so a
downgrade does not lose settings.

Every signal is individually switchable, so a misbehaving mic-capture probe can be
turned off without losing detection entirely. Setting `mode: "off"` reduces the
feature to today's behavior with no polling beyond what already runs.

### Meeting-app list

`KNOWN_AUDIO_APPS` in `audio_session_monitor.py` includes Spotify and Discord because
it answers a different question ("what could I capture?"). Reusing it would make music
playback trigger meeting suggestions. `meeting_detection.apps` is therefore a
**separate, user-editable list**, seeded with Teams, Zoom and Webex.

Browser-based meetings (Meet, Webex in a tab) are deliberately excluded from the seed
list: by process name a browser in a meeting is indistinguishable from a browser
playing YouTube. Mic capture is what separates them, so a browser should only be added
by a user who has `use_mic_capture` enabled.

## Suggestion content

The calendar signal earns its place by making the suggestion specific. Instead of
"a meeting seems to be running", the banner and toast read:

> **Sprint Planning** started 2 minutes ago — record it?

Accepting tags the recording with that calendar event immediately, reusing the
existing calendar plumbing. Without a calendar match it falls back to the app name:
"A Microsoft Teams call started 2 minutes ago."

## Missed audio

Detection needs `threshold_seconds` to confirm, and the user needs time to react, so
the meeting's opening is gone by the time recording begins.

**Out of scope:** a rolling pre-roll buffer that would recover it. That needs an
always-on capture stream and memory management — a separate subsystem deserving its
own spec, not a rider on this one.

**In scope:** record the gap honestly. Recording metadata gains
`detected_before_start_seconds`, and the recording header notes that the meeting was
already underway when capture began, so a transcript is never silently misleading
about what it missed.

## Error handling

- Every probe is individually wrapped. A failing probe returns empty for its own
  signal and never breaks the snapshot, matching the existing defensive style in
  `audio_session_monitor.py`.
- A probe that throws repeatedly is disabled for the session and logged once, rather
  than logging on every 3-second poll.
- Calendar lookup already runs off the GUI thread (`CalendarLookupWorker`) and keeps
  doing so; the detector treats a pending lookup as "no calendar signal yet" rather
  than blocking.
- If the tray is unavailable (`isSystemTrayAvailable()` false), the banner alone
  carries the suggestion.

## Testing

Per `ways-of-working.md`: TDD for non-UI logic, smoke tests for Qt.

The detector takes an injected clock, so all timing behavior is tested as pure
functions with no sleeping:

- a chime (activity shorter than `threshold_seconds`) never reaches SUGGESTED
- sustained activity with no confirming signal never reaches SUGGESTED
- mic capture alone reaches SUGGESTED
- a dismissed session does not re-prompt while it remains active
- an audio dropout shorter than `session_end_seconds` does not split a session
- a dropout longer than it ends the session and clears the dismissal
- Spotify or Discord activity never triggers, even while a real meeting app is idle
- `mode: "off"` produces no decisions at all
- config migration: legacy `auto_record` true/false map to the right modes, and an
  existing config with `auto_record: false` ends up at `"off"` rather than inheriting
  the new `"suggest"` default
- a fresh config with no legacy keys defaults to `"suggest"`

`meeting_signals.probe()` is tested against fake pycaw/psutil layers. The banner gets
a smoke test plus pure-helper unit tests for its text formatting.

## Risks

- **Mic-capture enumeration is unproven.** pycaw's `GetAllSessions()` returns render
  sessions; capture sessions need enumeration over a different data-flow direction.
  This warrants a spike before the main build. If it proves unreliable the design
  still stands on calendar and title corroboration, with reduced accuracy — which is
  precisely why the rule does not depend on any single signal.
- **Polling cost.** Window-title enumeration is the most expensive probe and is off by
  default. The audio probe reuses the existing 3-second cadence rather than adding a
  second timer.
- **`mode: "suggest"` as the new-user default** is a behavior change for fresh
  installs only; existing users keep their current setting through migration.
