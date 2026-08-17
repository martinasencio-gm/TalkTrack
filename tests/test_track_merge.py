# tests/test_track_merge.py
import unittest

from app.transcription.track_merge import merge_tracks
from app.transcription.transcriber import TranscriptSegment as Seg


def seg(start, end, text):
    return Seg(start=start, end=end, text=text)


class TestMergeTracks(unittest.TestCase):
    """Transcribing mic and system separately removes the doubled echo that
    combined_audio.wav fed to Whisper, and makes speaker attribution
    structural (mic = You, system = Remote) instead of a fragile RMS
    comparison. What remains to handle is bleed: with speakers rather than
    headphones the mic also hears the remote side, so the same sentence can
    be transcribed off both tracks."""

    def test_orders_segments_by_start_time(self):
        merged = merge_tracks([
            ("You", [seg(5.0, 6.0, "my turn")]),
            ("Remote", [seg(0.0, 1.0, "hello there")]),
        ])
        self.assertEqual([s.text for s in merged], ["hello there", "my turn"])

    def test_labels_each_track(self):
        merged = merge_tracks([
            ("You", [seg(0.0, 1.0, "hi")]),
            ("Remote", [seg(2.0, 3.0, "hello")]),
        ])
        self.assertEqual([s.speaker for s in merged], ["You", "Remote"])

    def test_drops_bleed_duplicated_onto_the_mic_track(self):
        # The remote sentence came out of the speakers and into the mic, so
        # both tracks transcribed it. The system copy is the true source.
        merged = merge_tracks([
            ("You", [seg(10.0, 12.0, "we should ship it on Friday")]),
            ("Remote", [seg(10.0, 12.0, "We should ship it on Friday.")]),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].speaker, "Remote")

    def test_tolerates_small_transcription_differences(self):
        # The bleed copy is a degraded recording, so its transcript rarely
        # matches word for word.
        merged = merge_tracks([
            ("You", [seg(10.0, 12.0, "we should ship it on friday")]),
            ("Remote", [seg(10.2, 12.1, "So we should ship it on Friday")]),
        ])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].speaker, "Remote")

    def test_keeps_genuinely_different_simultaneous_speech(self):
        # Both sides talking at once is normal and must survive.
        merged = merge_tracks([
            ("You", [seg(10.0, 12.0, "sorry go ahead")]),
            ("Remote", [seg(10.0, 12.0, "the deployment finished this morning")]),
        ])
        self.assertEqual(len(merged), 2)

    def test_keeps_matching_text_that_does_not_overlap_in_time(self):
        # The same phrase said later is a real utterance, not an echo.
        merged = merge_tracks([
            ("You", [seg(60.0, 61.0, "that sounds good to me")]),
            ("Remote", [seg(10.0, 11.0, "that sounds good to me")]),
        ])
        self.assertEqual(len(merged), 2)

    def test_keeps_short_utterances_even_when_they_match(self):
        # "Yeah" over the top of "yeah" is two people agreeing, and short
        # text carries too little evidence to call it an echo. Deleting a
        # real reply is worse than keeping a duplicate.
        merged = merge_tracks([
            ("You", [seg(10.0, 10.5, "yeah")]),
            ("Remote", [seg(10.0, 10.6, "Yeah.")]),
        ])
        self.assertEqual(len(merged), 2)

    def test_never_drops_from_the_authoritative_track(self):
        # Dedup only removes the echo copy; the system track is the source
        # of truth for remote speech and must come through untouched.
        merged = merge_tracks([
            ("You", [seg(0.0, 2.0, "the numbers look wrong to me")]),
            ("Remote", [seg(0.0, 2.0, "the numbers look wrong to me"),
                        seg(3.0, 5.0, "let me pull up the dashboard")]),
        ])
        self.assertEqual([s.speaker for s in merged], ["Remote", "Remote"])

    def test_handles_an_empty_track(self):
        merged = merge_tracks([
            ("You", []),
            ("Remote", [seg(0.0, 1.0, "anyone there")]),
        ])
        self.assertEqual(len(merged), 1)

    def test_handles_no_tracks(self):
        self.assertEqual(merge_tracks([]), [])

    def test_does_not_mutate_the_input_segments(self):
        original = seg(0.0, 1.0, "hi")
        merge_tracks([("You", [original])])
        self.assertEqual(original.speaker, "")


class TestDualTrackPlan(unittest.TestCase):
    """Decides whether a recording should be transcribed track-by-track."""

    def _session(self, **files):
        return {"audio_files": files}

    def _plan(self, session, diarization_enabled=False, hf_token="", missing=()):
        from app.transcription.track_merge import dual_track_plan
        return dual_track_plan(
            session, diarization_enabled, hf_token,
            exists=lambda p: p not in missing,
        )

    def test_uses_both_tracks_when_present(self):
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"))
        self.assertEqual(plan, [("You", "mic.wav"), ("Remote", "sys.wav")])

    def test_mic_comes_first_because_it_is_the_echo_prone_track(self):
        # merge_tracks only ever drops from the first track. A loopback of
        # the render stream cannot pick up the user's voice, so the system
        # track must stay authoritative.
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"))
        self.assertEqual(plan[0][0], "You")

    def test_declines_when_pyannote_diarization_will_run(self):
        # Full diarization clusters voices across the mixed audio; splitting
        # the tracks would take that decision away from it.
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"),
                          diarization_enabled=True, hf_token="hf_abc")
        self.assertIsNone(plan)

    def test_runs_when_diarization_is_enabled_but_unusable(self):
        # Enabled without a token falls back to simple labelling, which is
        # exactly what this replaces.
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"),
                          diarization_enabled=True, hf_token="")
        self.assertIsNotNone(plan)

    def test_declines_when_a_track_is_missing_from_metadata(self):
        self.assertIsNone(self._plan(self._session(mic="mic.wav")))
        self.assertIsNone(self._plan(self._session(system="sys.wav")))

    def test_declines_when_a_track_file_is_gone(self):
        # ChunkWriter deletes a track that captured no frames, so metadata
        # can name a file that isn't on disk.
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"),
                          missing=("sys.wav",))
        self.assertIsNone(plan)

    def test_declines_without_a_session(self):
        self.assertIsNone(self._plan(None))


if __name__ == "__main__":
    unittest.main()
