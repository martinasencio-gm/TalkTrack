# tests/test_ai_provider.py
import unittest
from unittest.mock import patch, MagicMock
import sys


class TestProviderFactory(unittest.TestCase):
    def test_create_claude_provider(self):
        mock_anthropic = MagicMock()
        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            from app.ai.provider_factory import create_provider
            from app.ai.claude_provider import ClaudeProvider
            # Reload to pick up the mock
            import importlib
            import app.ai.claude_provider
            importlib.reload(app.ai.claude_provider)
            from app.ai.claude_provider import ClaudeProvider

            config = {"provider": "claude", "api_key": "test-key", "model": "claude-sonnet-4-6"}
            provider = create_provider(config)
            self.assertIsInstance(provider, ClaudeProvider)

    def test_create_openai_provider(self):
        mock_openai = MagicMock()
        with patch.dict(sys.modules, {"openai": mock_openai}):
            from app.ai.provider_factory import create_provider
            import importlib
            import app.ai.openai_provider
            importlib.reload(app.ai.openai_provider)
            from app.ai.openai_provider import OpenAIProvider

            config = {"provider": "openai", "api_key": "test-key", "model": "gpt-4o"}
            provider = create_provider(config)
            self.assertIsInstance(provider, OpenAIProvider)

    def test_create_unknown_provider_raises(self):
        from app.ai.provider_factory import create_provider
        config = {"provider": "unknown"}
        with self.assertRaises(ValueError):
            create_provider(config)

    def test_create_none_provider(self):
        from app.ai.provider_factory import create_provider
        config = {"provider": "none"}
        provider = create_provider(config)
        self.assertIsNone(provider)

    def test_create_local_provider_uses_local_model_path(self):
        from app.ai.provider_factory import create_provider
        config = {
            "provider": "local",
            "model": "(set path below)",
            "local_model_path": "C:/models/test.gguf",
        }
        provider = create_provider(config)
        self.assertEqual(provider._model_path, "C:/models/test.gguf")

    def test_create_local_provider_falls_back_to_model_key(self):
        from app.ai.provider_factory import create_provider
        config = {
            "provider": "local",
            "model": "C:/models/legacy.gguf",
            "local_model_path": "",
        }
        provider = create_provider(config)
        self.assertEqual(provider._model_path, "C:/models/legacy.gguf")


class TestClaudeProvider(unittest.TestCase):
    def test_complete(self):
        mock_anthropic = MagicMock()
        mock_client = MagicMock()
        mock_anthropic.Anthropic.return_value = mock_client
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Summary of meeting")]
        mock_client.messages.create.return_value = mock_response

        with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
            import importlib
            import app.ai.claude_provider
            importlib.reload(app.ai.claude_provider)
            from app.ai.claude_provider import ClaudeProvider

            provider = ClaudeProvider(api_key="test", model="claude-sonnet-4-6")
            result = provider.complete("Summarize this", "transcript text")
            self.assertEqual(result, "Summary of meeting")
            mock_client.messages.create.assert_called_once()


class TestOpenAIProvider(unittest.TestCase):
    def test_complete(self):
        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.OpenAI.return_value = mock_client
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="AI response"))]
        mock_client.chat.completions.create.return_value = mock_response

        with patch.dict(sys.modules, {"openai": mock_openai}):
            import importlib
            import app.ai.openai_provider
            importlib.reload(app.ai.openai_provider)
            from app.ai.openai_provider import OpenAIProvider

            provider = OpenAIProvider(api_key="test", model="gpt-4o")
            result = provider.complete("Summarize", "transcript")
            self.assertEqual(result, "AI response")


class TestProviderInterface(unittest.TestCase):
    def test_base_class_is_abstract(self):
        from app.ai.provider import AIProvider
        with self.assertRaises(TypeError):
            AIProvider()

    def test_cloud_providers_have_generous_context_limit(self):
        from app.ai.provider import AIProvider
        self.assertGreaterEqual(AIProvider.max_context_chars, 50000)

    def test_local_provider_default_context_limit_unchanged(self):
        from app.ai.local_provider import LocalProvider
        provider = LocalProvider(model_path="x.gguf")
        self.assertEqual(provider.max_context_chars, 8_000)

    def test_local_provider_scales_context_with_n_ctx(self):
        from app.ai.local_provider import LocalProvider
        provider = LocalProvider(model_path="x.gguf", n_ctx=8192)
        self.assertEqual(provider.max_context_chars, (8192 - 2048) * 3)

    def test_resolve_local_n_ctx_uses_catalog_context_capped_at_8192(self):
        from app.ai.provider_factory import _resolve_local_n_ctx
        self.assertEqual(_resolve_local_n_ctx({}), 4096)
        self.assertEqual(_resolve_local_n_ctx({"local_model_name": "nope"}), 4096)
        self.assertEqual(_resolve_local_n_ctx({"local_model_name": "qwen2.5-3b"}), 8192)


class TestLocalModelAvailable(unittest.TestCase):
    def test_empty_path_and_empty_name_is_not_available(self):
        from app.ai.provider_factory import local_model_available
        self.assertFalse(local_model_available(
            {"local_model_path": "", "local_model_name": ""}))
        self.assertFalse(local_model_available({}))

    def test_existing_custom_path_is_available(self):
        import tempfile
        from pathlib import Path
        from app.ai.provider_factory import local_model_available
        with tempfile.TemporaryDirectory() as tmp:
            gguf = Path(tmp) / "custom.gguf"
            gguf.write_bytes(b"\0")
            self.assertTrue(local_model_available(
                {"local_model_path": str(gguf), "local_model_name": ""}))

    def test_missing_custom_path_is_not_available(self):
        from app.ai.provider_factory import local_model_available
        self.assertFalse(local_model_available(
            {"local_model_path": "/no/such/file.gguf", "local_model_name": ""}))

    def test_downloaded_catalog_name_is_available(self):
        from app.ai import provider_factory
        with patch("app.ai.model_store.is_downloaded", return_value=True):
            self.assertTrue(provider_factory.local_model_available(
                {"local_model_path": "", "local_model_name": "qwen2.5-3b"}))

    def test_undownloaded_catalog_name_is_not_available(self):
        from app.ai import provider_factory
        with patch("app.ai.model_store.is_downloaded", return_value=False):
            self.assertFalse(provider_factory.local_model_available(
                {"local_model_path": "", "local_model_name": "qwen2.5-3b"}))


class TestLocalProviderCompletion(unittest.TestCase):
    """The local provider must drive the GGUF instruct model through the
    chat-completion API so the model's own chat template and EOS token are
    honored — a raw completion call runs to max_tokens every time, which on
    CPU is minutes of wasted generation per summary."""

    def _fake_llama_module(self):
        module = MagicMock()
        llm = MagicMock()
        llm.create_chat_completion.return_value = {
            "choices": [{"message": {"content": "  a summary.  "}}]
        }
        module.Llama.return_value = llm
        return module, llm

    def test_get_llm_disables_verbose_logging(self):
        module, llm = self._fake_llama_module()
        with patch.dict(sys.modules, {"llama_cpp": module}):
            from app.ai.local_provider import LocalProvider
            LocalProvider(model_path="x.gguf")._get_llm()
        _, kwargs = module.Llama.call_args
        self.assertFalse(kwargs.get("verbose", True))

    def test_complete_uses_chat_completion_not_raw_call(self):
        module, llm = self._fake_llama_module()
        with patch.dict(sys.modules, {"llama_cpp": module}):
            from app.ai.local_provider import LocalProvider
            out = LocalProvider(model_path="x.gguf").complete("summarize this")
        self.assertEqual(out, "a summary.")
        llm.assert_not_called()  # no raw __call__ / create_completion
        llm.create_chat_completion.assert_called_once()
        _, kwargs = llm.create_chat_completion.call_args
        roles = [m["role"] for m in kwargs["messages"]]
        self.assertEqual(roles, ["user"])
        self.assertEqual(kwargs["messages"][0]["content"], "summarize this")

    def test_complete_passes_context_as_system_message(self):
        module, llm = self._fake_llama_module()
        with patch.dict(sys.modules, {"llama_cpp": module}):
            from app.ai.local_provider import LocalProvider
            LocalProvider(model_path="x.gguf").complete("q", "the transcript")
        _, kwargs = llm.create_chat_completion.call_args
        self.assertEqual(kwargs["messages"][0], {"role": "system", "content": "the transcript"})
        self.assertEqual(kwargs["messages"][1], {"role": "user", "content": "q"})

    def test_complete_caps_output_tokens_well_below_the_old_2048(self):
        module, llm = self._fake_llama_module()
        with patch.dict(sys.modules, {"llama_cpp": module}):
            from app.ai.local_provider import LocalProvider
            LocalProvider(model_path="x.gguf").complete("q")
        _, kwargs = llm.create_chat_completion.call_args
        self.assertLessEqual(kwargs["max_tokens"], 1536)
        self.assertGreaterEqual(kwargs["max_tokens"], 512)


class TestSentenceTransformerCache(unittest.TestCase):
    def test_same_model_name_loads_once(self):
        mock_st_module = MagicMock()
        with patch.dict(sys.modules, {"sentence_transformers": mock_st_module}):
            from app.ai import provider as provider_mod
            provider_mod._SENTENCE_TRANSFORMER_CACHE.clear()
            m1 = provider_mod.get_sentence_transformer("all-MiniLM-L6-v2")
            m2 = provider_mod.get_sentence_transformer("all-MiniLM-L6-v2")
        self.assertIs(m1, m2)
        self.assertEqual(mock_st_module.SentenceTransformer.call_count, 1)

    def test_claude_provider_embed_uses_shared_cache(self):
        mock_anthropic = MagicMock()
        mock_st_module = MagicMock()
        with patch.dict(sys.modules, {
            "anthropic": mock_anthropic,
            "sentence_transformers": mock_st_module,
        }):
            import importlib
            import app.ai.claude_provider
            importlib.reload(app.ai.claude_provider)
            from app.ai.claude_provider import ClaudeProvider
            from app.ai import provider as provider_mod
            provider_mod._SENTENCE_TRANSFORMER_CACHE.clear()

            p = ClaudeProvider(api_key="k", model="claude-sonnet-4-6")
            p.embed(["a"])
            p.embed(["b"])
        self.assertEqual(mock_st_module.SentenceTransformer.call_count, 1)


if __name__ == "__main__":
    unittest.main()
