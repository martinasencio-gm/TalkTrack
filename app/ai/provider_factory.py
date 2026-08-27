"""Factory for creating AI providers from config."""

from app.ai.provider import AIProvider


def local_model_available(config: dict) -> bool:
    """True when the Local provider has a usable model file.

    Either a custom ``local_model_path`` that exists on disk, or a
    ``local_model_name`` that is a catalog key whose GGUF is downloaded.
    Qt-free so callers outside the settings dialog (e.g. MainWindow's
    ``_ai_provider_configured``) can share the check.
    """
    import os

    path = (config.get("local_model_path") or "").strip()
    if path and os.path.isfile(path):
        return True
    name = (config.get("local_model_name") or "").strip()
    if not name:
        return False
    from app.ai import model_store
    try:
        return model_store.is_downloaded(name)
    except KeyError:
        return False


def _resolve_local_n_ctx(config: dict) -> int:
    from app.ai.model_catalog import get
    model = get(config.get("local_model_name") or "")
    if model is None:
        return 4096
    return min(model.context_tokens, 8192)


def create_provider(config: dict) -> AIProvider | None:
    provider_type = config.get("provider", "none")

    if provider_type == "none":
        return None

    if provider_type == "claude":
        from app.ai.claude_provider import ClaudeProvider
        return ClaudeProvider(
            api_key=config["api_key"],
            model=config.get("model", "claude-sonnet-4-6"),
        )

    if provider_type == "openai":
        from app.ai.openai_provider import OpenAIProvider
        return OpenAIProvider(
            api_key=config["api_key"],
            model=config.get("model", "gpt-4o"),
        )

    if provider_type == "grok":
        from app.ai.grok_provider import GrokProvider
        return GrokProvider(
            api_key=config["api_key"],
            model=config.get("model", "grok-3"),
        )

    if provider_type == "gemini":
        from app.ai.gemini_provider import GeminiProvider
        return GeminiProvider(
            api_key=config["api_key"],
            model=config.get("model", "gemini-2.5-flash"),
        )

    if provider_type == "mistral":
        from app.ai.mistral_provider import MistralProvider
        return MistralProvider(
            api_key=config["api_key"],
            model=config.get("model", "mistral-large-latest"),
        )

    if provider_type == "local":
        from app.ai.local_provider import LocalProvider
        return LocalProvider(
            model_path=config.get("local_model_path") or config.get("model", ""),
            embed_model=config.get("embed_model", "all-MiniLM-L6-v2"),
            n_ctx=_resolve_local_n_ctx(config),
        )

    raise ValueError(f"Unknown AI provider: {provider_type}")
