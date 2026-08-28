import unittest
import json


class TestCombinedPromptBuilder(unittest.TestCase):
    def test_build_combined_prompt(self):
        from app.ai.summarizer import build_combined_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Let's discuss the budget.", speaker="Alice"),
            TranscriptSegment(5.0, 10.0, "Bob, can you send the report by Friday?", speaker="Alice"),
        ]
        prompt = build_combined_prompt(segments, {"Alice": "Alice", "Bob": "Bob"})
        self.assertIn("Alice", prompt)
        self.assertIn("budget", prompt)
        self.assertIn("action item", prompt.lower())
        self.assertIn("report", prompt)

    def test_build_combined_prompt_with_notes(self):
        from app.ai.summarizer import build_combined_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Let's discuss the budget.", speaker="Alice"),
        ]
        prompt = build_combined_prompt(segments, {"Alice": "Alice"}, notes="Ask about Q3 numbers")
        self.assertIn("Ask about Q3 numbers", prompt)
        self.assertIn("USER NOTES", prompt)

    def test_build_combined_prompt_without_notes(self):
        from app.ai.summarizer import build_combined_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 5.0, "Hello.", speaker="Alice"),
        ]
        prompt = build_combined_prompt(segments, {"Alice": "Alice"}, notes="")
        self.assertNotIn("USER NOTES", prompt)


class TestParseActionItems(unittest.TestCase):
    def test_parse_json_response(self):
        from app.ai.summarizer import parse_action_items
        response = json.dumps([
            {"task": "Send report", "assignee": "Bob", "deadline": "Friday"},
            {"task": "Review budget", "assignee": "Alice", "deadline": ""},
        ])
        items = parse_action_items(response)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["task"], "Send report")

    def test_parse_malformed_response(self):
        from app.ai.summarizer import parse_action_items
        items = parse_action_items("This is not JSON")
        self.assertEqual(items, [])


class TestParseActionItemsHardening(unittest.TestCase):
    def test_prose_wrapped_array_without_fences(self):
        from app.ai.summarizer import parse_action_items
        response = 'Here are the items:\n[{"task": "Send report", "assignee": "Bob", "deadline": ""}]\nLet me know!'
        items = parse_action_items(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["task"], "Send report")

    def test_non_dict_entries_filtered(self):
        from app.ai.summarizer import parse_action_items
        response = '[{"task": "A", "assignee": "", "deadline": ""}, "stray string", 42]'
        items = parse_action_items(response)
        self.assertEqual(len(items), 1)

    def test_items_without_task_filtered(self):
        from app.ai.summarizer import parse_action_items
        response = '[{"assignee": "Bob"}, {"task": "Real one"}]'
        items = parse_action_items(response)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["task"], "Real one")

    def test_missing_fields_coerced_to_empty_strings(self):
        from app.ai.summarizer import parse_action_items
        items = parse_action_items('[{"task": "A"}]')
        self.assertEqual(items[0]["assignee"], "")
        self.assertEqual(items[0]["deadline"], "")

    def test_object_response_returns_empty(self):
        from app.ai.summarizer import parse_action_items
        self.assertEqual(parse_action_items('{"task": "not a list"}'), [])


class TestParseCombinedResponse(unittest.TestCase):
    def test_splits_summary_and_fenced_json(self):
        from app.ai.summarizer import parse_combined_response
        response = (
            "## Summary\n- Discussed the budget\n\n"
            "```json\n"
            '[{"task": "Send report", "assignee": "Bob", "deadline": "Friday"}]\n'
            "```"
        )
        summary, items = parse_combined_response(response)
        self.assertIn("Discussed the budget", summary)
        self.assertNotIn("```", summary)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["task"], "Send report")

    def test_empty_action_items_array(self):
        from app.ai.summarizer import parse_combined_response
        response = "## Summary\n- Nothing much happened\n\n```json\n[]\n```"
        summary, items = parse_combined_response(response)
        self.assertIn("Nothing much happened", summary)
        self.assertEqual(items, [])

    def test_json_without_fences_still_splits(self):
        from app.ai.summarizer import parse_combined_response
        response = 'Summary text here.\n[{"task": "Follow up"}]'
        summary, items = parse_combined_response(response)
        self.assertEqual(summary, "Summary text here.")
        self.assertEqual(items[0]["task"], "Follow up")

    def test_no_json_present_returns_whole_text_as_summary(self):
        from app.ai.summarizer import parse_combined_response
        response = "Just a summary, no action items section at all."
        summary, items = parse_combined_response(response)
        self.assertEqual(summary, response)
        self.assertEqual(items, [])


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

    def test_build_combined_prompt_respects_cap(self):
        from app.ai.summarizer import build_combined_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(float(i), float(i + 1), "word " * 50, speaker="A")
            for i in range(500)
        ]
        prompt = build_combined_prompt(
            segments, {}, max_transcript_chars=5000
        )
        # Prompt = instructions + capped transcript; generous upper bound.
        self.assertLess(len(prompt), 7000)
        self.assertIn("truncated", prompt)

    def test_build_combined_prompt_uncapped_by_default(self):
        from app.ai.summarizer import build_combined_prompt
        from app.transcription.transcriber import TranscriptSegment
        segments = [
            TranscriptSegment(0.0, 1.0, "hello world", speaker="A"),
        ]
        prompt = build_combined_prompt(segments, {})
        self.assertNotIn("truncated", prompt)


if __name__ == "__main__":
    unittest.main()
