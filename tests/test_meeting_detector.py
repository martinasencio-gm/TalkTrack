import unittest

from app.integrations.meeting_detector import MeetingDetector

SETTINGS = {"mode": "suggest", "threshold_seconds": 5, "detect_end": True,
            "end_grace_seconds": 60, "end_action": "stop", "use_mic_capture": True,
            "use_calendar": True, "use_window_title": False}


def snap(t, audio=(), mic=(), titles=(), event=None):
    return {"timestamp": t, "audio_apps": list(audio), "mic_capture_apps": list(mic),
            "meeting_titles": list(titles), "calendar_event": event}


class TestStartDetection(unittest.TestCase):
    def test_chime_shorter_than_threshold_never_suggests(self):
        # A notification sound comes and goes between polls. The gap resets the
        # candidate, so the later burst starts its own clock.
        d = MeetingDetector()
        self.assertEqual(d.update(snap(0, audio=["ms-teams"]), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(2), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(10, audio=["ms-teams"]), SETTINGS).action, "none")

    def test_sustained_audio_without_confirmation_does_not_suggest(self):
        # Audio is the trigger, never its own confirmation.
        d = MeetingDetector()
        d.update(snap(0, audio=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(10, audio=["ms-teams"]), SETTINGS).action, "none")

    def test_mic_capture_alone_is_sufficient(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), SETTINGS).action,
                         "suggest_start")

    def test_calendar_confirms_sustained_audio(self):
        d = MeetingDetector()
        event = {"subject": "Sprint Planning"}
        d.update(snap(0, audio=["ms-teams"], event=event), SETTINGS)
        decision = d.update(snap(10, audio=["ms-teams"], event=event), SETTINGS)
        self.assertEqual(decision.action, "suggest_start")
        self.assertEqual(decision.meeting_name, "Sprint Planning")

    def test_window_title_confirms_sustained_audio(self):
        d = MeetingDetector()
        d.update(snap(0, audio=["Zoom"], titles=["Zoom Meeting"]), SETTINGS)
        self.assertEqual(
            d.update(snap(10, audio=["Zoom"], titles=["Zoom Meeting"]), SETTINGS).action,
            "suggest_start")

    def test_meeting_name_falls_back_to_app_name(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["Zoom"]), SETTINGS)
        self.assertEqual(d.update(snap(10, mic=["Zoom"]), SETTINGS).meeting_name, "Zoom")

    def test_meeting_name_from_window_title_for_unscheduled_call(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"], titles=["Jane Doe | Microsoft Teams, meeting window"]), SETTINGS)
        decision = d.update(snap(10, mic=["ms-teams"], titles=["Jane Doe | Microsoft Teams, meeting window"]), SETTINGS)
        self.assertEqual(decision.action, "suggest_start")
        self.assertEqual(decision.meeting_name, "Jane Doe")

    def test_calendar_takes_precedence_over_window_title(self):
        d = MeetingDetector()
        event = {"subject": "Sprint Planning"}
        d.update(snap(0, mic=["ms-teams"], titles=["Jane Doe | Microsoft Teams"], event=event), SETTINGS)
        decision = d.update(snap(10, mic=["ms-teams"], titles=["Jane Doe | Microsoft Teams"], event=event), SETTINGS)
        self.assertEqual(decision.meeting_name, "Sprint Planning")

    def test_auto_mode_starts_without_suggesting(self):
        d = MeetingDetector()
        settings = dict(SETTINGS, mode="auto")
        d.update(snap(0, mic=["ms-teams"]), settings)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), settings).action, "start")

    def test_off_mode_produces_nothing(self):
        d = MeetingDetector()
        settings = dict(SETTINGS, mode="off")
        d.update(snap(0, mic=["ms-teams"]), settings)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), settings).action, "none")

    def test_suggestion_is_not_repeated_while_pending(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(10, mic=["ms-teams"]), SETTINGS).action,
                         "suggest_start")
        self.assertEqual(d.update(snap(20, mic=["ms-teams"]), SETTINGS).action, "none")

    def test_dismissed_session_does_not_reprompt(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.dismiss_start()
        self.assertEqual(d.update(snap(20, mic=["ms-teams"]), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(30, mic=["ms-teams"]), SETTINGS).action, "none")

    def test_new_session_after_dismissal_may_prompt_again(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.dismiss_start()
        d.update(snap(200), SETTINGS)          # long gap ends the session
        d.update(snap(300, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(310, mic=["ms-teams"]), SETTINGS).action,
                         "suggest_start")


class TestEndDetection(unittest.TestCase):
    def _recording(self):
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), SETTINGS)
        d.update(snap(10, mic=["ms-teams"]), SETTINGS)
        d.accept_start()
        return d

    def test_short_dropout_does_not_end(self):
        d = self._recording()
        self.assertEqual(d.update(snap(40), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(60, mic=["ms-teams"]), SETTINGS).action, "none")

    def test_sustained_absence_suggests_end(self):
        d = self._recording()
        d.update(snap(40), SETTINGS)
        self.assertEqual(d.update(snap(100), SETTINGS).action, "suggest_end")

    def test_end_suggestion_is_not_repeated_while_pending(self):
        d = self._recording()
        self.assertEqual(d.update(snap(100), SETTINGS).action, "suggest_end")
        self.assertEqual(d.update(snap(160), SETTINGS).action, "none")

    def test_signals_returning_during_ending_resumes_silently(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)                      # -> suggest_end
        self.assertEqual(d.update(snap(110, mic=["ms-teams"]), SETTINGS).action, "none")
        # and it may suggest ending again once the signals go for good
        self.assertEqual(d.update(snap(200), SETTINGS).action, "suggest_end")

    def test_keep_recording_suppresses_further_end_prompts(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)
        d.choose_end("keep")
        self.assertEqual(d.update(snap(200), SETTINGS).action, "none")
        self.assertEqual(d.update(snap(400), SETTINGS).action, "none")

    def test_auto_mode_stops_without_prompting(self):
        settings = dict(SETTINGS, mode="auto")
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(100), settings).action, "stop")

    def test_auto_mode_pause_action(self):
        settings = dict(SETTINGS, mode="auto", end_action="pause")
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(100), settings).action, "pause")

    def test_paused_session_resumes_when_signals_return(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)
        d.choose_end("pause")
        self.assertEqual(d.update(snap(120, mic=["ms-teams"]), SETTINGS).action,
                         "resume")

    def test_paused_session_stays_paused_while_quiet(self):
        d = self._recording()
        d.update(snap(100), SETTINGS)
        d.choose_end("pause")
        self.assertEqual(d.update(snap(200), SETTINGS).action, "none")

    def test_detect_end_disabled_never_ends(self):
        settings = dict(SETTINGS, detect_end=False)
        d = MeetingDetector()
        d.update(snap(0, mic=["ms-teams"]), settings)
        d.update(snap(10, mic=["ms-teams"]), settings)
        d.accept_start()
        self.assertEqual(d.update(snap(200), settings).action, "none")

    def test_unrelated_recording_never_gets_end_suggestion(self):
        # Recording started with no meeting active -> detector must not watch it,
        # or an unrelated background call ending would prompt to stop it.
        d = MeetingDetector()
        d.note_recording_started(snap(0))
        self.assertEqual(d.update(snap(200), SETTINGS).action, "none")

    def test_recording_started_during_a_meeting_is_watched(self):
        d = MeetingDetector()
        d.note_recording_started(snap(0, mic=["ms-teams"]))
        d.update(snap(1, mic=["ms-teams"]), SETTINGS)
        self.assertEqual(d.update(snap(100), SETTINGS).action, "suggest_end")

    def test_stop_resets_to_idle(self):
        d = self._recording()
        d.note_recording_stopped()
        self.assertEqual(d.state, "idle")


if __name__ == "__main__":
    unittest.main()
