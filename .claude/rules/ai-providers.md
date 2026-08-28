# AI Providers: config keys, context limits, timeouts, embed cache

Conventions from issues #13, #15, #30, #31, #33, #36.

## Config keys

- Local provider model: `config["ai"]["local_model_name"]` holds the catalog key of the selected built-in model; `config["ai"]["local_model_path"]` holds a custom GGUF path and wins when set. The `model` combo carries no placeholder entry for local (the old `"(set path below)"` was removed). `provider_factory` reads `local_model_path` first (#13); keep that precedence.
- Per-provider API keys/models live in `provider_settings` keyed by provider name.

## Local model catalog

- `app/ai/model_catalog.py` — hard-coded `CATALOG` of `CatalogModel`
  (ungated HF GGUF repos, Q4_K_M). Adding a model is one entry.
- `app/ai/model_store.py` — `models/manifest.json` under `APP_DATA_DIR`.
  "Downloaded" = manifest entry AND file on disk within 20% of the recorded
  size. `is_downloaded` never trusts the manifest alone.
- `app/ai/model_downloader.py` — wraps `hf_hub_download`; progress via a
  `tqdm_class` shim, cancel via a `cancel_check` polled from that shim.
- Selecting a catalog model writes BOTH `ai.local_model_name` (the key) and
  `ai.local_model_path` (resolved absolute path). `provider_factory` still
  reads `local_model_path` first (#13); `local_model_name` only feeds
  `_resolve_local_n_ctx` → `LocalProvider(n_ctx=...)`.
- A non-empty custom `local_model_path` (Advanced section) always wins over
  the catalog selection.

## Context limits

- `AIProvider.max_context_chars` (class attr): 100k default (cloud), `LocalProvider` overrides to 8k (n_ctx=4096 tokens + instruction/completion headroom).
- Summary/action-item prompt builders take `max_transcript_chars`; pass `provider.max_context_chars`. Truncation keeps head AND tail (60/40) with a marker — action items cluster late in meetings, so a chat-style head-only cut drops exactly what the prompt needs.
- Chat has its own separate 12k char cap (`chat.MAX_CONTEXT_CHARS`).

## Request timeouts (120s convention, #36)

- Anthropic / OpenAI / Grok: `timeout=120.0` constructor kwarg.
- Gemini (`google.generativeai`): per-call `request_options={"timeout": 120.0}` on `generate_content` — no client-level kwarg.
- Mistral: NO timeout kwarg in the SDK — pass `client=httpx.Client(timeout=120.0)` to the constructor.
- New providers must set an explicit timeout; SDK defaults (~10 min) hang the worker and block app close.

## Embeddings

- All local-embedding providers use `provider.get_sentence_transformer(name)` (module-level cache). Never instantiate `SentenceTransformer` directly — per-call construction cost seconds and re-downloaded on first use (#31).
- Search runs in `recordings_list._SearchWorker` (QThread, latest-query-wins).
- **Per-recording embedding cache (#33)**: each recording dir gets `embeddings.npz` mapping sha1(segment text) → vector, keyed by `provider.embed_model_id`. `embedding_cache.get_corpus_vectors` embeds only cache misses and prunes stale hashes — transcript edits invalidate per segment automatically. Every provider must set `embed_model_id` (base default None = caching disabled); it MUST change whenever `embed()`'s vectors would (`st:<sentence-transformer name>` / `openai:<api model>` convention). A model-id mismatch or corrupt npz drops the whole file for that recording — never mix vectors across models.

## Summary + action items — one combined call

- `summarizer.build_summary_prompt(...)` asks for **both** the markdown summary and the action-items JSON in a single response, separated by a line equal to `summarizer.ACTION_ITEMS_DELIMITER` (`===ACTION_ITEMS_JSON===`). `summarizer.split_summary_response(response)` returns `(summary_markdown, action_items)` — summary is everything before the last delimiter line; items parse from the tail via `parse_action_items`. A missing delimiter (or garbage tail) degrades to summary-only + `[]`, never an error.
- Both call sites make exactly one `provider.complete()` — `MainWindow.SummarizeWorker` (emits `summary_ready` then `actions_ready` back to back) and `app/batch/pipeline._run_batch_summary`. There is no separate action-items prompt; `build_action_items_prompt` was removed. Don't reintroduce a second call — the transcript is embedded once now, and two calls doubled the input tokens.
- `meta["seconds"]` times the whole single call. `generated_by` stays `"talktrack-app"` (in-app) / `"talktrack-batch"` (batch run) / `"talktrack-batch-summarize"` (the Claude-session skill).

## Error surfacing

- Summarize errors go through `_on_summarize_error` → panels' `set_error()` (restores prior content when it exists). Never leave panels in `set_loading` state on failure.
- Settings "Test Connection" calls `provider.complete()` directly so the real auth/model exception reaches the dialog — `AIProvider.test_connection()` swallows exceptions and is only suitable for boolean checks.
