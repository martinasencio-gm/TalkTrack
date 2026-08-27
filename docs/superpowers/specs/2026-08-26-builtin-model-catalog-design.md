# Built-in Model Catalog — Design

**Date:** 2026-08-26
**Status:** Approved (brainstorm)

## Motivation

TalkTrack already has a `local` AI provider (`app/ai/local_provider.py`) that
produces offline summaries via `llama-cpp-python`, plus an on-demand pip
installer for that package. The gap versus competitors (e.g. Meetily) is the
UX layer: today a user must find a GGUF file themselves and paste its path.

This feature adds a **curated catalog of downloadable models**, an in-app
**download manager with progress**, and **model selection** — so a user picks
a model from a list, clicks Download, and offline summaries work with no API
key, no cost, and no file hunting.

## Decisions locked in brainstorm

- **Scope:** curated catalog + downloader + selection (not a distinct
  provider — folded into the existing `local` provider).
- **Inference backend:** keep `llama-cpp-python`, but install it from the
  official **prebuilt CPU wheel index**
  (`https://abetlen.github.io/llama-cpp-python/whl/cpu`) so end users never
  hit a from-source compile. GPU acceleration is out of scope for v1.
- **Catalog:** hard-coded in a Python module. No remote fetch, nothing to host.
- **Download mechanism:** `huggingface_hub.hf_hub_download` (already in the
  dependency tree) — gives HTTP resume, integrity, and a progress hook.
- **Settings placement:** fold into the existing "Local Model" provider. The
  catalog list becomes the primary control; the manual GGUF path drops to a
  collapsed "Advanced" row and still wins when set (back-compat).
- **Starter catalog:** ungated models only (Qwen2.5, Phi-3.5). Gated
  Gemma/Llama entries are a later catalog edit; the token param is plumbed now.
- **Behavioural defaults:** no auto-download, no auto-select; missing model →
  same "configure in Settings" error shape as "no API key"; Remove action to
  reclaim disk; disk-space pre-check (warn, not block); download runs in the
  Settings dialog only (no background-after-close); RAM hint is advisory text.

## Architecture

Four new pieces. All Qt-free except the widget, so the logic is unit-testable.

| Module | Responsibility |
|---|---|
| `app/ai/model_catalog.py` | Hard-coded catalog: list of `CatalogModel` dataclasses. `models_dir()` → `APP_DATA_DIR / "models"`, `local_path_for(key)`, `get(key)`. |
| `app/ai/model_store.py` | On-disk state. Reads/writes `models/manifest.json`. `is_downloaded(key)`, `list_status()`, `remove(key)`, `free_disk_bytes()`. |
| `app/ai/model_downloader.py` | `download(model, token="", progress_cb=None, cancel_check=None) -> Path`. Wraps `hf_hub_download`, verifies size, writes manifest. Raises `DownloadCancelled` / `DownloadError`. No Qt. |
| `app/ui/model_catalog_widget.py` | `QWidget` shown in the AI tab when provider == `local`. One row per catalog model. Owns a `QThread` `_DownloadWorker`. |

`app/ui/settings_dialog.py` gets the wiring. `app/ai/local_provider.py`,
`app/ai/provider_factory.py`, `app/utils/config.py`,
`app/utils/package_installer.py` get minor changes (below).

### `CatalogModel` fields

`key`, `display_name`, `hf_repo`, `hf_filename`, `size_bytes`,
`context_tokens`, `ram_hint_gb`, `license`, `description`.

## Catalog contents (v1)

All ungated, all `Q4_K_M` GGUF. Exact filenames/sizes verified against
HuggingFace during implementation; the table shape is the contract.

| key | Display | Repo → file | ~Size | Context | RAM hint | License |
|---|---|---|---|---|---|---|
| `qwen2.5-3b` | Qwen2.5 3B Instruct | `Qwen/Qwen2.5-3B-Instruct-GGUF` → `qwen2.5-3b-instruct-q4_k_m.gguf` | 2.0 GB | 32k | ~4 GB | Apache-2.0 |
| `phi-3.5-mini` | Phi-3.5 Mini Instruct | `bartowski/Phi-3.5-mini-instruct-GGUF` → `Phi-3.5-mini-instruct-Q4_K_M.gguf` | 2.4 GB | 128k | ~5 GB | MIT |
| `qwen2.5-7b` | Qwen2.5 7B Instruct | `Qwen/Qwen2.5-7B-Instruct-GGUF` → `qwen2.5-7b-instruct-q4_k_m.gguf` | 4.7 GB | 32k | ~8 GB | Apache-2.0 |

## Storage layout

Under `APP_DATA_DIR` (`app/utils/app_paths.py`):

```
models/
  manifest.json
  qwen2.5-3b-instruct-q4_k_m.gguf
  Phi-3.5-mini-instruct-Q4_K_M.gguf
```

`manifest.json` shape:

```json
{ "qwen2.5-3b": { "filename": "...", "sha256": "...", "size": 2019377664, "downloaded_at": "2026-08-26T12:00:00Z" } }
```

Written via `app/utils/atomic_io.py` `atomic_write_json`.

**"Downloaded" test:** the manifest entry exists **and** the referenced file
exists on disk with a matching size. A `.gguf` present but absent from the
manifest (interrupted download, manual drop) counts as **not** downloaded; the
row offers Download, which overwrites. A manifest entry whose file was deleted
outside the app also counts as not downloaded.

## Downloader

`model_downloader.download(model, token="", progress_cb=None, cancel_check=None) -> Path`

- Calls `hf_hub_download(repo_id=model.hf_repo, filename=model.hf_filename,
  local_dir=models_dir(), token=token or None)`. `huggingface_hub` manages the
  `.incomplete` temp file and HTTP resume, so a killed download resumes.
- **Progress:** pass a `tqdm`-compatible class (via `tqdm_class=`) that
  forwards `(downloaded, total)` to `progress_cb`. If that hook proves
  brittle across `huggingface_hub` versions, fall back to a poller thread
  comparing `.incomplete` size to `model.size_bytes`.
- **Cancel:** `cancel_check()` polled from the progress callback; on `True`,
  raise `DownloadCancelled` and delete the partial file (no half-file left to
  masquerade as complete).
- **On success:** verify final size within 1% of `model.size_bytes` (guards
  truncation / an HTML error page saved as `.gguf`); compute sha256; write
  the manifest entry; return the `Path`.
- **Errors** → `DownloadError` with a short message (network, 401/gated,
  disk full, size mismatch).
- **Token:** unused by the v1 catalog; plumbed through so gated models are a
  pure catalog edit later (would read `config["diarization"]["hf_token"]`).

**Disk pre-check** (in the widget, before starting): if
`model_store.free_disk_bytes()` < `1.5 * model.size_bytes`, show a confirm
dialog ("Only X GB free, model needs ~Y GB. Continue?"). Not a hard block.

## Settings UI

In the AI tab, when provider == `local`:

- The bare `ai_local_path` row is replaced as the primary control by
  **`ModelCatalogWidget`**.
- The manual GGUF path field + Browse move into a collapsed
  **"Advanced: use a custom GGUF file"** `CollapsibleSection`
  (`app/ui/collapsible_section.py`). If a custom path is set and non-empty it
  **wins** over the catalog selection (preserves today's behaviour for
  existing users); catalog rows then show a muted "overridden by custom path"
  note.

### `ModelCatalogWidget`

Vertical list, one `_ModelRow` per catalog entry:

- **Line 1:** display name + status pill — `Selected` (blue), `Downloaded`
  (green), or none.
- **Line 2:** muted — `2.0 GB · 32k context · needs ~4 GB RAM · Apache-2.0`.
- **Right side:** a stacked control — **Download** button → (running)
  `QProgressBar` + **Cancel** → settles to **Select** (downloaded, not
  active) or a small **Remove** secondary action (downloaded).
- One radio-like selection across rows; selecting a downloaded model makes it
  active.

### Interaction rules

- Exactly one `_DownloadWorker` (`QThread`) at a time; other Download buttons
  disabled while one runs.
- While a download runs: dialog **OK/Save disabled**, **provider combo
  disabled**, and `reject()` / close triggers `cancel_check` → worker
  `wait()` → then close.
- Download finish → row flips to Downloaded; auto-selects it **only if**
  nothing else is active.
- Remove → confirm → `model_store.remove(key)`; if it was active, clear the
  selection and blank `ai.local_model_name` + `ai.local_model_path`.
- No catalog model downloaded and no custom path → the AI-status line reads
  "No local model selected".

The worker marshals `progress(int)`, `finished(str key)`, `error(str msg)`,
`cancelled()` back to the widget via signals (workers never touch widgets or
disk-for-UI directly — house pattern).

## Provider, factory & config wiring

### `config.py`

`DEFAULT_CONFIG["ai"]` gains one key:

```python
"local_model_name": "",   # catalog key of the selected built-in model; "" = none/custom
```

### Settings save (`settings_dialog._save` / per-provider cache)

When a catalog model is selected, resolve its absolute path and write **both**:

- `ai.local_model_name = "<key>"`
- `ai.local_model_path = str(local_path_for(key))`

Writing `local_model_path` too keeps `provider_factory` and the `#13`
precedence rule (`local_model_path` read first) untouched. Setting a custom
Advanced path overwrites `local_model_path` and blanks `local_model_name`.

### `provider_factory.create_provider` (`local` branch)

Behaviour unchanged; pass the selected model's advertised context so the
provider isn't pinned at 4096:

```python
return LocalProvider(
    model_path=config.get("local_model_path") or config.get("model", ""),
    embed_model=config.get("embed_model", "all-MiniLM-L6-v2"),
    n_ctx=_resolve_n_ctx(config),   # min(catalog_model.context_tokens, 8192) or 4096
)
```

`_resolve_n_ctx`: if `local_model_name` names a catalog model, return
`min(that model.context_tokens, 8192)`; else `4096`. Cap at 8192 keeps CPU
memory sane regardless of a model's advertised window.

### `LocalProvider.__init__`

Gains `n_ctx: int = 4096`. Used in `_get_llm()` (`Llama(n_ctx=...)`) and to
derive `max_context_chars = n_ctx * 3` (≈ chars per token), replacing the
hard-coded `8_000`. `complete()` and `embed()` unchanged.

### Packaging

- `pyproject.toml` + `requirements.txt` + `.claude/rules/packaging-and-launch.md`:
  document that `llama-cpp-python` installs from the prebuilt CPU wheel index.
- `package_installer.py` `_install_command`: for the `local` package only,
  append `--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`
  so the ad-hoc install stops trying to compile from source.

## Testing

### TDD (unit, `tests/`)

- `test_model_catalog.py` — entries well-formed (unique keys, non-empty
  repo/filename, positive sizes/context); `models_dir()` / `local_path_for()`
  derive from `APP_DATA_DIR`.
- `test_model_store.py` — manifest round-trip in a temp dir; `is_downloaded`
  False when file present but manifest missing; `is_downloaded` False when
  manifest entry present but file deleted; `remove()` deletes file + entry;
  `list_status()` join; `free_disk_bytes()` returns a positive int.
- `test_model_downloader.py` — `hf_hub_download` mocked: success writes
  manifest + returns path; size-mismatch → `DownloadError`; `cancel_check`
  True → `DownloadCancelled` + partial file removed; progress callback
  forwards `(downloaded, total)`.
- `test_ai_provider.py` additions — `LocalProvider(n_ctx=32768)` sets
  `max_context_chars == 98304`; default `n_ctx` stays 4096 → 8000-ish;
  factory `_resolve_n_ctx` picks catalog context, caps at 8192.
- `test_package_installer.py` additions — `local` install command carries the
  CPU wheel `--extra-index-url`; other providers' commands do not.
- `test_config.py` — `local_model_name` default present.

### Smoke (per ways-of-working — no Qt widget tests)

- `python -c "from app.ui.model_catalog_widget import ModelCatalogWidget"`
- `python -c "from app.ui.settings_dialog import SettingsDialog"`
- Manual: real download of `qwen2.5-3b` on the dev box, generate a summary,
  confirm it runs offline.

## Edge cases

| Case | Handling |
|---|---|
| Interrupted download | HF `.incomplete` resume; if manifest absent, Download overwrites cleanly. |
| App closed mid-download | `reject()` sets cancel flag, `wait()`s the worker, deletes partial. |
| Disk fills mid-download | `hf_hub_download` raises → `DownloadError`; partial removed. |
| Model file deleted outside the app | `model_store.is_downloaded` stats the file, not just the manifest → next summary fails with the "configure in Settings" message. |
| Switch provider away from `local` mid-download | Blocked — provider combo disabled while a worker runs. |
| Custom Advanced path + catalog selection both set | Custom path wins (`local_model_path` precedence); catalog rows show "overridden". |
| `llama-cpp-python` not installed when user picks a catalog model | Existing on-demand installer prompt fires first (now with the CPU wheel index). |

## Out of scope (v1)

- GPU / CUDA llama.cpp wheels.
- Gated models (Gemma, Llama) — catalog edit + token wiring later.
- Remote/updatable catalog.
- Background downloads that survive closing the Settings dialog.
- Per-model prompt-template tuning (the current single prompt is reused).
