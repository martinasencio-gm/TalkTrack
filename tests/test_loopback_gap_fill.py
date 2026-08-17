# tests/test_loopback_gap_fill.py
import unittest

from app.recording.audio_capture import _SilenceGapFiller


SR = 16000


class TestSilenceGapFiller(unittest.TestCase):
    """WASAPI loopback delivers no packets while nothing renders, so silent
    stretches were simply absent from system_audio.wav rather than written
    as silence. The track came out time-compressed and everything after a
    gap shifted earlier — 51s lost over a 24-minute meeting, with the
    mic/system offset drifting from 0.5s to 1.7s."""

    def _filler(self, **kw):
        f = _SilenceGapFiller(SR, **kw)
        f.start(now=100.0)
        return f

    def test_continuous_callbacks_need_no_padding(self):
        f = self._filler()
        # 0.1s of audio arriving every 0.1s
        self.assertEqual(f.gap_frames(now=100.1, chunk_frames=1600), 0)
        self.assertEqual(f.gap_frames(now=100.2, chunk_frames=1600), 0)

    def test_pads_a_silent_gap(self):
        f = self._filler()
        f.gap_frames(now=100.1, chunk_frames=1600)
        # Audio resumes at 105.1 with a chunk covering the 0.1s before it,
        # so the silence to materialise runs 100.1 -> 105.0, i.e. 4.9s.
        pad = f.gap_frames(now=105.1, chunk_frames=1600)
        self.assertAlmostEqual(pad, 4.9 * SR, delta=SR // 100)

    def test_small_jitter_is_not_padded(self):
        # Callback scheduling jitter is normal; padding it would inject
        # audible clicks and inflate the track.
        f = self._filler(min_gap_seconds=0.1)
        pad = f.gap_frames(now=100.15, chunk_frames=1600)
        self.assertEqual(pad, 0)

    def test_absurd_gap_is_treated_as_a_clock_anomaly(self):
        # Mirrors the existing 30s guard in _alignment_prepad_frames: a
        # jump that large is a clock change, not silence, and materialising
        # it would produce a gigantic file.
        f = self._filler(max_gap_seconds=30.0)
        self.assertEqual(f.gap_frames(now=100000.0, chunk_frames=1600), 0)

    def test_padding_is_not_repeated_on_the_next_chunk(self):
        f = self._filler()
        f.gap_frames(now=105.0, chunk_frames=1600)
        self.assertEqual(f.gap_frames(now=105.1, chunk_frames=1600), 0)

    def test_paused_time_is_not_padded(self):
        # The mic stream drops frames while paused too, so both tracks lose
        # the pause identically. Padding it would misalign them.
        f = self._filler()
        f.gap_frames(now=100.1, chunk_frames=1600)
        f.pause(now=100.1)
        f.resume(now=160.1)  # a minute paused
        self.assertEqual(f.gap_frames(now=160.2, chunk_frames=1600), 0)

    def test_gap_after_a_pause_still_pads(self):
        f = self._filler()
        f.gap_frames(now=100.1, chunk_frames=1600)
        f.pause(now=100.1)
        f.resume(now=160.1)
        # 3s of genuine silence after resuming.
        pad = f.gap_frames(now=163.2, chunk_frames=1600)
        self.assertAlmostEqual(pad, 3 * SR, delta=SR // 100)

    def test_no_padding_before_start(self):
        f = _SilenceGapFiller(SR)
        self.assertEqual(f.gap_frames(now=100.0, chunk_frames=1600), 0)


class _FakeSink:
    def __init__(self):
        self.chunks = []

    def put(self, chunk):
        self.chunks.append(chunk)


class TestLoopbackCallbackFillsGaps(unittest.TestCase):
    """Drives the real callback with synthetic packets and a fake clock, so
    the gap filling is exercised end-to-end without an audio device."""

    def _stream(self, sink):
        from app.recording.audio_capture import LoopbackStream
        s = LoopbackStream(sample_rate=SR, sink=sink)
        s._recording = True
        s._paused = False
        s._native_channels = 1
        s._resampler = None
        return s

    def _packet(self, frames=1600):
        import numpy as np
        return np.zeros(frames, dtype=np.float32).tobytes()

    def test_silence_between_packets_is_written_to_the_sink(self):
        import unittest.mock as mock
        from app.recording import audio_capture

        sink = _FakeSink()
        stream = self._stream(sink)
        clock = [100.0]
        with mock.patch.object(audio_capture.time, "monotonic",
                               side_effect=lambda: clock[0]):
            stream._gap_filler.start(clock[0])
            clock[0] = 100.1
            stream._callback(self._packet(), 1600, None, None)
            # Endpoint renders nothing for ~5s, then audio returns.
            clock[0] = 105.1
            stream._callback(self._packet(), 1600, None, None)

        written = sum(len(c) for c in sink.chunks)
        # 2 packets (3200 frames) plus 4.9s of materialised silence.
        self.assertAlmostEqual(written, 3200 + 4.9 * SR, delta=SR // 100)

    def test_continuous_packets_add_no_silence(self):
        import unittest.mock as mock
        from app.recording import audio_capture

        sink = _FakeSink()
        stream = self._stream(sink)
        clock = [100.0]
        with mock.patch.object(audio_capture.time, "monotonic",
                               side_effect=lambda: clock[0]):
            stream._gap_filler.start(clock[0])
            for i in range(1, 11):
                clock[0] = 100.0 + i * 0.1
                stream._callback(self._packet(), 1600, None, None)

        self.assertEqual(sum(len(c) for c in sink.chunks), 16000)


if __name__ == "__main__":
    unittest.main()
