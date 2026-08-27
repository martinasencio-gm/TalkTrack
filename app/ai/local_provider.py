"""Local model provider using llama-cpp-python and sentence-transformers."""

from app.ai.provider import AIProvider


class LocalProvider(AIProvider):
    def __init__(self, model_path: str, embed_model: str = "all-MiniLM-L6-v2",
                 n_ctx: int = 4096):
        self._model_path = model_path
        self._embed_model_name = embed_model
        self.embed_model_id = f"st:{embed_model}"
        self._n_ctx = n_ctx
        # Reserve ~2048 tokens for the completion (matches max_tokens in
        # complete()); ~3 chars/token for the rest. Floor at the previous
        # constant so small models behave exactly as before.
        self.max_context_chars = max(8_000, (n_ctx - 2_048) * 3)
        self._llm = None

    def _get_llm(self):
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(
                model_path=self._model_path,
                n_ctx=self._n_ctx,
                n_threads=4,
            )
        return self._llm

    def _get_embedder(self):
        from app.ai.provider import get_sentence_transformer
        return get_sentence_transformer(self._embed_model_name)

    def complete(self, prompt: str, context: str = "") -> str:
        llm = self._get_llm()
        full_prompt = f"{context}\n\n{prompt}" if context else prompt
        response = llm(full_prompt, max_tokens=2048)
        return response["choices"][0]["text"].strip()

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_embedder()
        embeddings = model.encode(texts)
        return [e.tolist() for e in embeddings]
