"""Tests for TranscriptSegment and TranscriptResult."""
import math
import sys
import unittest
from unittest.mock import MagicMock, patch

from app.transcription.transcriber import TranscriptSegment, TranscriptResult


class _FwMocks:
    """Build a mocked faster_whisper module returning given segments."""

    def __init__(self, segments=(), duration=5.0):
        self.module = MagicMock()
        self.model = MagicMock()
        self.module.WhisperModel.return_value = self.model
        info = MagicMock(language="en", duration=duration)
        self.model.transcribe.return_value = (iter(list(segments)), info)


class TestWhisperModelCache(unittest.TestCase):
    def test_same_params_reuse_model(self):
        fw = _FwMocks()
        with patch.dict(sys.modules, {"faster_whisper": fw.module}):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            m1 = tr._get_model("base", "cpu", "int8")
            m2 = tr._get_model("base", "cpu", "int8")
        self.assertIs(m1, m2)
        self.assertEqual(fw.module.WhisperModel.call_count, 1)

    def test_different_params_create_new_model(self):
        fw = _FwMocks()
        with patch.dict(sys.modules, {"faster_whisper": fw.module}):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            tr._get_model("base", "cpu", "int8")
            tr._get_model("small", "cpu", "int8")
        self.assertEqual(fw.module.WhisperModel.call_count, 2)


class TestRunSegmentMapping(unittest.TestCase):
    def _run_worker(self, segments):
        fw = _FwMocks(segments=segments)
        with patch.dict(sys.modules, {"faster_whisper": fw.module}):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            worker = tr.TranscriptionWorker(
                "a.wav", model_size="base", device="cpu"
            )
            results = []
            worker.finished.connect(results.append)
            worker.run()
        return results, fw.model

    def test_confidence_populated_from_avg_logprob(self):
        seg = MagicMock(start=0.0, end=1.0, text=" hi ", avg_logprob=-0.5)
        results, _ = self._run_worker([seg])
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(
            results[0].segments[0].confidence, math.exp(-0.5), places=5
        )

    def test_word_timestamps_not_requested(self):
        _, model = self._run_worker([])
        kwargs = model.transcribe.call_args.kwargs
        self.assertNotIn("word_timestamps", kwargs)

    def test_result_records_model_size_used(self):
        results, _ = self._run_worker([])
        self.assertEqual(results[0].model_size, "base")

    def test_progress_percent_emitted_relative_to_duration(self):
        fw = _FwMocks(
            segments=[
                MagicMock(start=0.0, end=5.0, text="a", avg_logprob=-0.1),
                MagicMock(start=5.0, end=10.0, text="b", avg_logprob=-0.1),
            ],
            duration=10.0,
        )
        with patch.dict(sys.modules, {"faster_whisper": fw.module}):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            worker = tr.TranscriptionWorker("a.wav", model_size="base", device="cpu")
            percents = []
            worker.progress_percent.connect(percents.append)
            worker.run()
        # Per-segment percents, plus a guaranteed final 100% so the bar
        # always visually completes even if trailing silence trimmed by
        # VAD means the last segment.end never quite reaches info.duration.
        self.assertEqual(percents, [50, 100, 100])

    def test_progress_text_not_reemitted_per_segment(self):
        # Each `progress` (text) emission resets the UI progress bar to
        # indeterminate mode (see TranscriptViewer.show_progress) — firing
        # it per segment made the bar visibly flash/flicker on every
        # segment. Only phase-boundary messages should use it; per-segment
        # ticks must go through progress_percent alone.
        fw = _FwMocks(
            segments=[
                MagicMock(start=0.0, end=5.0, text="a", avg_logprob=-0.1),
                MagicMock(start=5.0, end=10.0, text="b", avg_logprob=-0.1),
                MagicMock(start=10.0, end=15.0, text="c", avg_logprob=-0.1),
            ],
            duration=15.0,
        )
        with patch.dict(sys.modules, {"faster_whisper": fw.module}):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            worker = tr.TranscriptionWorker("a.wav", model_size="base", device="cpu")
            texts = []
            worker.progress.connect(texts.append)
            worker.run()
        # Phase-boundary messages only: loading, transcribing-start, complete.
        # None of them should repeat per segment (3 segments here).
        self.assertEqual(
            texts,
            ["Loading transcription model...", "Transcribing audio...", "Transcription complete."],
        )


class TestTranscriptionTiming(unittest.TestCase):
    """Wall-clock time taken is recorded on the result for display/persistence."""

    def test_transcribe_seconds_recorded_on_result(self):
        fw = _FwMocks(segments=[])
        clock = iter([100.0, 107.5])
        with patch.dict(sys.modules, {"faster_whisper": fw.module}), \
             patch("app.transcription.transcriber.time.monotonic", side_effect=lambda: next(clock)):
            import app.transcription.transcriber as tr
            tr._MODEL_CACHE.clear()
            worker = tr.TranscriptionWorker("a.wav", model_size="small", device="cpu")
            results = []
            worker.finished.connect(results.append)
            worker.run()
        self.assertEqual(results[0].transcribe_seconds, 7.5)


class TestTranscriptSegment(unittest.TestCase):

    def test_to_dict_without_original_text(self):
        seg = TranscriptSegment(start=1.0, end=2.0, text="hello")
        d = seg.to_dict()
        self.assertEqual(d["text"], "hello")
        self.assertNotIn("original_text", d)

    def test_to_dict_with_original_text(self):
        seg = TranscriptSegment(start=1.0, end=2.0, text="Q4", original_text="quarterly")
        d = seg.to_dict()
        self.assertEqual(d["text"], "Q4")
        self.assertEqual(d["original_text"], "quarterly")

    def test_to_dict_with_empty_original_text_omits_it(self):
        seg = TranscriptSegment(start=1.0, end=2.0, text="hello", original_text="")
        d = seg.to_dict()
        self.assertNotIn("original_text", d)

    def test_from_dict_with_original_text(self):
        """TranscriptSegment(**dict) should accept original_text."""
        data = {"start": 1.0, "end": 2.0, "text": "Q4", "original_text": "quarterly",
                "speaker": "", "confidence": 0.0}
        seg = TranscriptSegment(**data)
        self.assertEqual(seg.original_text, "quarterly")

    def test_from_dict_without_original_text(self):
        data = {"start": 1.0, "end": 2.0, "text": "hello", "speaker": "", "confidence": 0.0}
        seg = TranscriptSegment(**data)
        self.assertEqual(seg.original_text, "")

    def test_from_dict_ignores_unknown_keys(self):
        """from_dict should ignore extra keys like speaker_name."""
        data = {"start": 1.0, "end": 2.0, "text": "hello", "speaker": "SPEAKER_00",
                "confidence": 0.9, "speaker_name": "Alice", "extra_field": 42}
        seg = TranscriptSegment.from_dict(data)
        self.assertEqual(seg.text, "hello")
        self.assertEqual(seg.speaker, "SPEAKER_00")
        self.assertFalse(hasattr(seg, "speaker_name"))


class TestTranscriptResultExports(unittest.TestCase):

    def _make_result(self):
        return TranscriptResult(
            segments=[
                TranscriptSegment(start=0.0, end=5.0, text="Hello everyone", speaker="SPEAKER_00"),
                TranscriptSegment(start=5.0, end=10.0, text="Hi there", speaker="SPEAKER_01"),
            ],
            language="en",
            duration=10.0,
        )

    def test_to_text_without_speaker_names(self):
        result = self._make_result()
        text = result.to_text()
        self.assertIn("[SPEAKER_00]", text)
        self.assertIn("[SPEAKER_01]", text)

    def test_to_dict_includes_model_size_and_transcribe_seconds(self):
        result = self._make_result()
        result.model_size = "small"
        result.transcribe_seconds = 12.5
        d = result.to_dict()
        self.assertEqual(d["model_size"], "small")
        self.assertEqual(d["transcribe_seconds"], 12.5)

    def test_to_text_with_speaker_names(self):
        result = self._make_result()
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        text = result.to_text(speaker_names=names)
        self.assertIn("[Alice]", text)
        self.assertIn("[Bob]", text)
        self.assertNotIn("SPEAKER_00", text)

    def test_to_text_with_partial_speaker_names(self):
        result = self._make_result()
        names = {"SPEAKER_00": "Alice"}
        text = result.to_text(speaker_names=names)
        self.assertIn("[Alice]", text)
        self.assertIn("[SPEAKER_01]", text)

    def test_to_srt_with_speaker_names(self):
        result = self._make_result()
        names = {"SPEAKER_00": "Alice"}
        srt = result.to_srt(speaker_names=names)
        self.assertIn("[Alice]", srt)
        self.assertIn("[SPEAKER_01]", srt)

    def test_to_dict_with_speaker_names(self):
        result = self._make_result()
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        d = result.to_dict(speaker_names=names)
        self.assertEqual(d["segments"][0]["speaker_name"], "Alice")
        self.assertEqual(d["segments"][1]["speaker_name"], "Bob")
        # Original speaker IDs preserved
        self.assertEqual(d["segments"][0]["speaker"], "SPEAKER_00")

    def test_to_dict_without_speaker_names_has_no_speaker_name_key(self):
        result = self._make_result()
        d = result.to_dict()
        self.assertNotIn("speaker_name", d["segments"][0])


class TestMergeAdjacentSameSpeaker(unittest.TestCase):
    """Whisper splits one continuous turn into many small segments — often
    with zero gap between them. Without merging, each of those boundaries
    is also a hard cut point during continuous playback, where Whisper's
    imprecise timestamps can audibly clip a word. Merging same-speaker
    segments separated by a short gap fixes both the fragmented display
    and the playback clipping in one move."""

    def _make_result(self, segs):
        return TranscriptResult(segments=segs, language="en", duration=10.0)

    def test_merges_touching_same_speaker_segments(self):
        segs = [
            TranscriptSegment(0.0, 4.0, "Hello there,", speaker="SPEAKER_00"),
            TranscriptSegment(4.0, 7.0, "how are you?", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker()
        self.assertEqual(len(r.segments), 1)
        self.assertEqual(r.segments[0].start, 0.0)
        self.assertEqual(r.segments[0].end, 7.0)
        self.assertEqual(r.segments[0].text, "Hello there, how are you?")

    def test_merges_across_short_gap(self):
        segs = [
            TranscriptSegment(0.0, 4.0, "First part.", speaker="SPEAKER_00"),
            TranscriptSegment(4.3, 6.0, "Second part.", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker(max_gap=0.5)
        self.assertEqual(len(r.segments), 1)
        self.assertEqual(r.segments[0].text, "First part. Second part.")

    def test_does_not_merge_across_long_gap(self):
        segs = [
            TranscriptSegment(0.0, 4.0, "First part.", speaker="SPEAKER_00"),
            TranscriptSegment(6.0, 8.0, "Much later.", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker(max_gap=0.5)
        self.assertEqual(len(r.segments), 2)

    def test_does_not_merge_different_speakers(self):
        segs = [
            TranscriptSegment(0.0, 4.0, "First part.", speaker="SPEAKER_00"),
            TranscriptSegment(4.0, 6.0, "Reply.", speaker="SPEAKER_01"),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker()
        self.assertEqual(len(r.segments), 2)

    def test_merged_confidence_is_the_minimum_of_the_parts(self):
        segs = [
            TranscriptSegment(0.0, 4.0, "Sure thing.", speaker="SPEAKER_00", confidence=0.9),
            TranscriptSegment(4.0, 6.0, "No problem.", speaker="SPEAKER_00", confidence=0.4),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker()
        self.assertEqual(r.segments[0].confidence, 0.4)

    def test_empty_transcript_is_a_no_op(self):
        r = self._make_result([])
        r.merge_adjacent_same_speaker()
        self.assertEqual(r.segments, [])

    def test_chains_three_or_more_consecutive_segments(self):
        segs = [
            TranscriptSegment(0.0, 2.0, "One.", speaker="SPEAKER_00"),
            TranscriptSegment(2.0, 4.0, "Two.", speaker="SPEAKER_00"),
            TranscriptSegment(4.0, 6.0, "Three.", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        r.merge_adjacent_same_speaker()
        self.assertEqual(len(r.segments), 1)
        self.assertEqual(r.segments[0].text, "One. Two. Three.")
        self.assertEqual(r.segments[0].end, 6.0)


class TestToPlainText(unittest.TestCase):
    """TranscriptResult.to_plain_text: clipboard-friendly, no timestamps."""

    def _make_result(self, segs):
        return TranscriptResult(segments=segs, language="en", duration=10.0)

    def test_empty_transcript_returns_empty_string(self):
        r = self._make_result([])
        self.assertEqual(r.to_plain_text(), "")

    def test_uses_raw_speaker_id_when_no_name_mapping(self):
        segs = [TranscriptSegment(0.0, 1.0, "Hi there.", speaker="SPEAKER_00")]
        r = self._make_result(segs)
        self.assertEqual(r.to_plain_text(), "SPEAKER_00: Hi there.")

    def test_uses_friendly_name_when_provided(self):
        segs = [TranscriptSegment(0.0, 1.0, "Hi there.", speaker="SPEAKER_00")]
        r = self._make_result(segs)
        out = r.to_plain_text(speaker_names={"SPEAKER_00": "Alice"})
        self.assertEqual(out, "Alice: Hi there.")

    def test_empty_friendly_name_falls_back_to_raw_id(self):
        segs = [TranscriptSegment(0.0, 1.0, "Hi.", speaker="SPEAKER_00")]
        r = self._make_result(segs)
        out = r.to_plain_text(speaker_names={"SPEAKER_00": ""})
        self.assertEqual(out, "SPEAKER_00: Hi.")

    def test_segment_without_speaker_has_no_prefix(self):
        segs = [TranscriptSegment(0.0, 1.0, "Unidentified line.")]
        r = self._make_result(segs)
        self.assertEqual(r.to_plain_text(), "Unidentified line.")

    def test_blank_line_between_speaker_changes(self):
        segs = [
            TranscriptSegment(0.0, 1.0, "One.", speaker="SPEAKER_00"),
            TranscriptSegment(1.0, 2.0, "Two.", speaker="SPEAKER_00"),
            TranscriptSegment(2.0, 3.0, "Three.", speaker="SPEAKER_01"),
            TranscriptSegment(3.0, 4.0, "Four.", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        names = {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
        out = r.to_plain_text(speaker_names=names)
        expected = (
            "Alice: One.\n"
            "Alice: Two.\n"
            "\n"
            "Bob: Three.\n"
            "\n"
            "Alice: Four."
        )
        self.assertEqual(out, expected)

    def test_no_timestamps_in_output(self):
        segs = [
            TranscriptSegment(0.0, 1.5, "Hello.", speaker="SPEAKER_00"),
            TranscriptSegment(1.5, 3.0, "World.", speaker="SPEAKER_00"),
        ]
        r = self._make_result(segs)
        out = r.to_plain_text(speaker_names={"SPEAKER_00": "Alice"})
        # No digits-colon-digits timestamps and no square brackets
        self.assertNotIn("[", out)
        self.assertNotIn("]", out)
        self.assertNotIn("->", out)

    def test_no_trailing_newline(self):
        segs = [TranscriptSegment(0.0, 1.0, "Hi.", speaker="SPEAKER_00")]
        r = self._make_result(segs)
        out = r.to_plain_text()
        self.assertFalse(out.endswith("\n"))

    def test_text_is_stripped_of_leading_whitespace(self):
        """Whisper often emits leading spaces on segment text."""
        segs = [TranscriptSegment(0.0, 1.0, " Hello.", speaker="SPEAKER_00")]
        r = self._make_result(segs)
        self.assertEqual(r.to_plain_text(), "SPEAKER_00: Hello.")


if __name__ == "__main__":
    unittest.main()
