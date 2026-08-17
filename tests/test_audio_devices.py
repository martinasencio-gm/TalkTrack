# tests/test_audio_devices.py
import unittest
from unittest.mock import patch

from app.utils import audio_devices


class _FakeDefault:
    def __init__(self, output_index):
        self.device = [0, output_index]


def _outputs(*names):
    return [{"index": 20 + i, "name": n, "channels": 2,
             "sample_rate": 48000, "hostapi": "WASAPI"}
            for i, n in enumerate(names)]


class TestGetDefaultOutput(unittest.TestCase):
    """get_default_output matches the default endpoint by NAME, because
    sd.default.device[1] is an MME/DirectSound index that doesn't line up
    with WASAPI indices."""

    def _run(self, mme_name, outputs):
        with patch.object(audio_devices, "sd") as sd, \
             patch.object(audio_devices, "get_system_audio_devices",
                          return_value=outputs):
            sd.default = _FakeDefault(5)
            sd.query_devices.return_value = {"name": mme_name}
            return audio_devices.get_default_output()

    def test_exact_name_match_wins(self):
        outs = _outputs("Speakers (AnkerWork B600 Video Bar)",
                        "Speakers (Realtek(R) Audio)")
        self.assertEqual(self._run("Speakers (Realtek(R) Audio)", outs), 21)

    def test_matches_name_truncated_by_mme(self):
        # Windows MME caps device names at 31 characters, so the default
        # endpoint's name arrives clipped and never compares equal to the
        # full WASAPI name. Falling through to "first device in the list"
        # silently tapped an endpoint that renders nothing, producing an
        # empty system_audio.wav.
        full = "DELL S2725QS (2- HD Audio Driver for Display Audio)"
        outs = _outputs("Speakers (AnkerWork B600 Video Bar)", full)
        self.assertEqual(self._run(full[:31], outs), 21)

    def test_ambiguous_truncation_does_not_guess(self):
        # Two endpoints sharing the clipped prefix carry no information to
        # tell them apart — better to fall back than to pick one at random.
        outs = _outputs("Realtek High Definition Audio (Speakers)",
                        "Realtek High Definition Audio (Headphones)")
        clipped = "Realtek High Definition Audio (Speakers)"[:31]
        self.assertEqual(self._run(clipped, outs), 20)

    def test_no_match_falls_back_to_first_output(self):
        outs = _outputs("Speakers (AnkerWork B600 Video Bar)",
                        "Speakers (Realtek(R) Audio)")
        self.assertEqual(self._run("Some Vanished Device", outs), 20)

    def test_returns_none_when_no_outputs(self):
        self.assertIsNone(self._run("Anything", []))

    def test_survives_a_raising_backend(self):
        outs = _outputs("Speakers (Realtek(R) Audio)")
        with patch.object(audio_devices, "sd") as sd, \
             patch.object(audio_devices, "get_system_audio_devices",
                          return_value=outs):
            sd.default = _FakeDefault(5)
            sd.query_devices.side_effect = RuntimeError("PortAudio exploded")
            self.assertEqual(audio_devices.get_default_output(), 20)


if __name__ == "__main__":
    unittest.main()
