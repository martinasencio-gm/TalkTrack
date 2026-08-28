import unittest


class TestSummaryPromptBuilder(unittest.TestCase):
    def test_build_summary_prompt(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Let's discuss the budget.", speaker="Alice"),
            TranscriptSegment(5.0, 10.0, "I think we need more funding.", speaker="Bob"),
        ]
        prompt = build_summary_prompt(segments, {"Alice": "Alice", "Bob": "Bob"})
        self.assertIn("Alice", prompt)
        self.assertIn("budget", prompt)

    def test_build_summary_prompt_with_notes(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Let's discuss the budget.", speaker="Alice"),
        ]
        prompt = build_summary_prompt(segments, {"Alice": "Alice"}, notes="Ask about Q3 numbers")
        self.assertIn("Ask about Q3 numbers", prompt)
        self.assertIn("USER NOTES", prompt)

    def test_build_summary_prompt_without_notes(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Hello.", speaker="Alice"),
        ]
        prompt = build_summary_prompt(segments, {"Alice": "Alice"}, notes="")
        self.assertNotIn("USER NOTES", prompt)

    def test_build_summary_prompt_asks_for_an_action_items_section(self):
        """Action items are a markdown section inside the summary now — the
        prompt must ask for the exact heading and the bullet shape, and must
        not ask for a JSON payload or a delimiter."""
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Bob, can you send the report by Friday?", speaker="Alice"),
        ]
        prompt = build_summary_prompt(segments, {"Alice": "Alice", "Bob": "Bob"})
        self.assertIn("## Action Items", prompt)
        self.assertIn("_None._", prompt)
        self.assertIn("action item", prompt.lower())
        self.assertNotIn("JSON", prompt)
        self.assertNotIn("===", prompt)

    def test_build_summary_prompt_includes_instruction(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [TranscriptSegment(0.0, 5.0, "Hello.", speaker="Alice")]
        prompt = build_summary_prompt(
            segments, {"Alice": "Alice"}, instruction="Focus on risks"
        )
        self.assertIn("Focus on risks", prompt)


class TestTranscriptTruncation(unittest.TestCase):
    def test_short_text_unchanged(self):
        from app.ai.summarizer import truncate_transcript
        text = "short transcript"
        self.assertEqual(truncate_transcript(text, 1000), text)

    def test_long_text_keeps_head_and_tail(self):
        from app.ai.summarizer import truncate_transcript
        text = "HEAD " + ("x" * 10000) + " TAIL"
        out = truncate_transcript(text, 2000)
        self.assertLessEqual(len(out), 2000)
        self.assertTrue(out.startswith("HEAD"))
        self.assertTrue(out.endswith("TAIL"))
        self.assertIn("truncated", out)

    def test_build_summary_prompt_respects_cap(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(float(i), float(i + 1), "word " * 50, speaker="A")
            for i in range(500)
        ]
        prompt = build_summary_prompt(
            segments, {}, max_transcript_chars=5000
        )
        # Prompt = instructions + capped transcript; generous upper bound.
        self.assertLess(len(prompt), 7000)
        self.assertIn("truncated", prompt)

    def test_build_summary_prompt_uncapped_by_default(self):
        from app.ai.summarizer import build_summary_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 1.0, "hello world", speaker="A"),
        ]
        prompt = build_summary_prompt(segments, {})
        self.assertNotIn("truncated", prompt)


if __name__ == "__main__":
    unittest.main()
