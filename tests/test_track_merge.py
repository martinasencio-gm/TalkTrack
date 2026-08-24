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
        # The remote side came out of the speakers and into the mic, so both
        # tracks transcribed it. The system copy is the true source. Bleed is
        # scored over the whole recording, so the echo has to be the pattern
        # it is in reality — the mic copying the other side throughout.
        remote = [seg(10.0, 12.0, "we should ship it on Friday"),
                  seg(14.0, 16.0, "the staging box is already updated"),
                  seg(18.0, 20.0, "I will send the release notes over")]
        mic = [seg(10.0, 12.0, "We should ship it on Friday."),
               seg(14.0, 16.0, "the staging box is already updated"),
               seg(18.0, 20.0, "I'll send the release notes over")]
        merged = merge_tracks([("You", mic), ("Remote", remote)])
        self.assertEqual(len(merged), 3)
        self.assertTrue(all(s.speaker == "Remote" for s in merged))

    def test_tolerates_small_transcription_differences(self):
        # The bleed copy is a degraded recording, so its transcript rarely
        # matches word for word.
        remote = [seg(10.2, 12.1, "So we should ship it on Friday"),
                  seg(14.0, 16.0, "the staging box is already updated"),
                  seg(18.0, 20.0, "I will send the release notes over")]
        mic = [seg(10.0, 12.0, "we should ship it on friday"),
               seg(14.1, 16.2, "the staging box is already up to date"),
               seg(18.1, 20.1, "I will send the release notes over")]
        merged = merge_tracks([("You", mic), ("Remote", remote)])
        self.assertEqual(len(merged), 3)
        self.assertTrue(all(s.speaker == "Remote" for s in merged))

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
        remote = [seg(0.0, 2.0, "the numbers look wrong to me"),
                  seg(3.0, 5.0, "let me pull up the dashboard"),
                  seg(6.0, 8.0, "this is the figure I meant")]
        mic = [seg(0.0, 2.0, "the numbers look wrong to me"),
               seg(3.0, 5.0, "let me pull up the dashboard"),
               seg(6.0, 8.0, "this is the figure I meant")]
        merged = merge_tracks([("You", mic), ("Remote", remote)])
        self.assertEqual([s.speaker for s in merged],
                         ["Remote", "Remote", "Remote"])


class TestBleedNeedsCorroboration(unittest.TestCase):
    """A mic that never heard the speakers must keep everything it recorded.

    Simultaneous agreement is indistinguishable from an echo one segment at
    a time, so the old per-segment rule deleted the user's own words from
    recordings made on headphones. Bleed is now scored across the call.
    """

    def test_keeps_a_lone_collision_with_the_other_side(self):
        # Recorded on headphones. Both sides land on the same stock phrase
        # at the same moment — the user's copy is real speech, not an echo.
        merged = merge_tracks([
            ("You", [seg(0.0, 3.0, "let me share my screen now"),
                     seg(30.0, 32.0, "sounds good to me")]),
            ("Remote", [seg(10.0, 14.0, "here are the numbers for Q3"),
                        seg(30.1, 32.2, "Sounds good to me!")]),
        ])
        self.assertEqual(len(merged), 4)
        self.assertEqual(sum(1 for s in merged if s.speaker == "You"), 2)

    def test_keeps_a_couple_of_collisions_in_a_long_call(self):
        # Two matches out of thirty remote utterances is coincidence, and
        # nowhere near the share of the call real bleed would cover.
        remote = [seg(i * 10.0, i * 10.0 + 2.0, f"remote point number {i}")
                  for i in range(30)]
        remote[3] = seg(30.0, 32.0, "that works for me")
        remote[17] = seg(170.0, 172.0, "have a good weekend everyone")
        mic = [seg(30.0, 32.0, "that works for me"),
               seg(170.0, 172.0, "have a good weekend everyone"),
               seg(200.0, 203.0, "I will follow up on Monday")]
        merged = merge_tracks([("You", mic), ("Remote", remote)])
        self.assertEqual(sum(1 for s in merged if s.speaker == "You"), 3)

    def test_still_drops_bleed_that_covers_the_call(self):
        # The mic hearing most of the other side is the real signature.
        remote = [seg(i * 10.0, i * 10.0 + 2.0, f"remote point number {i}")
                  for i in range(10)]
        mic = [seg(i * 10.0, i * 10.0 + 2.0, f"remote point number {i}")
               for i in range(10)]
        mic.append(seg(200.0, 203.0, "I will follow up on Monday"))
        merged = merge_tracks([("You", mic), ("Remote", remote)])
        self.assertEqual(sum(1 for s in merged if s.speaker == "You"), 1)
        self.assertEqual(sum(1 for s in merged if s.speaker == "Remote"), 10)

    def test_detector_needs_both_a_count_and_a_share(self):
        from app.transcription.track_merge import bleed_detected
        self.assertFalse(bleed_detected(0, 10))
        self.assertFalse(bleed_detected(2, 4))    # too few to corroborate
        self.assertFalse(bleed_detected(3, 100))  # too sparse to be bleed
        self.assertTrue(bleed_detected(3, 10))
        self.assertTrue(bleed_detected(40, 50))

    def test_no_remote_speech_means_no_bleed(self):
        from app.transcription.track_merge import bleed_detected
        self.assertFalse(bleed_detected(0, 0))

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

    def _plan(self, session, diarization_enabled=False, hf_token="", engine="pyannote", missing=()):
        from app.transcription.track_merge import dual_track_plan
        return dual_track_plan(
            session, diarization_enabled, hf_token, engine=engine,
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

    def test_declines_when_sherpa_onnx_diarization_will_run(self):
        plan = self._plan(self._session(mic="mic.wav", system="sys.wav"),
                          diarization_enabled=True, hf_token="", engine="sherpa_onnx")
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
