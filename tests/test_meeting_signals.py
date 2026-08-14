import unittest

from app.utils import meeting_signals


class TestIsMeetingApp(unittest.TestCase):
    APPS = ["ms-teams", "Teams", "Zoom", "Webex"]

    def test_matches_ignoring_exe_suffix_and_case(self):
        self.assertTrue(meeting_signals.is_meeting_app("ms-teams.exe", self.APPS))
        self.assertTrue(meeting_signals.is_meeting_app("ZOOM.EXE", self.APPS))
        self.assertTrue(meeting_signals.is_meeting_app("Zoom", self.APPS))

    def test_rejects_non_meeting_apps(self):
        # Regression guard: audio_session_monitor.KNOWN_AUDIO_APPS contains these.
        # Reusing that list would make Spotify playback look like a meeting.
        self.assertFalse(meeting_signals.is_meeting_app("Spotify.exe", self.APPS))
        self.assertFalse(meeting_signals.is_meeting_app("Discord.exe", self.APPS))
        self.assertFalse(meeting_signals.is_meeting_app("chrome.exe", self.APPS))


class TestProbe(unittest.TestCase):
    SETTINGS = {"apps": ["ms-teams", "Zoom"], "use_mic_capture": True,
                "use_calendar": True, "use_window_title": False}

    def _probe(self, settings=None, now=1.0, audio=None, mic=None, names=None,
               titles=None, calendar_event=None):
        return meeting_signals.probe(
            settings or self.SETTINGS,
            calendar_event=calendar_event,
            now=now,
            _audio_apps_fn=audio if callable(audio) else (lambda: list(audio or [])),
            _mic_pids_fn=mic if callable(mic) else (lambda: set(mic or ())),
            _pid_names_fn=lambda pids: dict(names or {}),
            _titles_fn=lambda: list(titles or []),
        )

    def test_probe_reports_meeting_apps_with_audio(self):
        snap = self._probe(now=100.0, audio=[
            {"process_name": "ms-teams.exe", "active": True},
            {"process_name": "Spotify.exe", "active": True},
        ])
        self.assertEqual(snap["audio_apps"], ["ms-teams"])
        self.assertEqual(snap["timestamp"], 100.0)

    def test_inactive_audio_sessions_are_ignored(self):
        snap = self._probe(audio=[{"process_name": "Zoom.exe", "active": False}])
        self.assertEqual(snap["audio_apps"], [])

    def test_mic_capture_maps_pids_to_meeting_apps(self):
        snap = self._probe(mic={4116, 9999},
                           names={4116: "ms-teams.exe", 9999: "chrome.exe"})
        self.assertEqual(snap["mic_capture_apps"], ["ms-teams"])

    def test_mic_capture_skipped_when_disabled(self):
        settings = dict(self.SETTINGS, use_mic_capture=False)
        snap = self._probe(settings=settings, mic={4116},
                           names={4116: "ms-teams.exe"})
        self.assertEqual(snap["mic_capture_apps"], [])

    def test_window_titles_skipped_when_disabled(self):
        snap = self._probe(titles=["Zoom Meeting"])
        self.assertEqual(snap["meeting_titles"], [])

    def test_window_titles_included_when_enabled(self):
        settings = dict(self.SETTINGS, use_window_title=True)
        snap = self._probe(settings=settings, titles=["Zoom Meeting"])
        self.assertEqual(snap["meeting_titles"], ["Zoom Meeting"])

    def test_calendar_event_dropped_when_disabled(self):
        settings = dict(self.SETTINGS, use_calendar=False)
        snap = self._probe(settings=settings, calendar_event={"subject": "Standup"})
        self.assertIsNone(snap["calendar_event"])

    def test_failing_probe_does_not_break_snapshot(self):
        def boom():
            raise OSError("COM exploded")
        snap = self._probe(audio=boom)
        self.assertEqual(snap["audio_apps"], [])
        self.assertIn("timestamp", snap)
        self.assertEqual(snap["mic_capture_apps"], [])

    def test_failing_mic_probe_does_not_break_snapshot(self):
        def boom():
            raise OSError("no capture endpoints")
        snap = self._probe(audio=[{"process_name": "Zoom.exe", "active": True}],
                           mic=boom)
        self.assertEqual(snap["audio_apps"], ["Zoom"])
        self.assertEqual(snap["mic_capture_apps"], [])


if __name__ == "__main__":
    unittest.main()
