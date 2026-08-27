# Built-in Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the `local` AI provider a curated in-app catalog of downloadable GGUF models with a download manager (progress, cancel, remove) and model selection, so offline summaries work with no API key and no manual file hunting.

**Architecture:** Three Qt-free logic modules — `model_catalog.py` (hard-coded list), `model_store.py` (on-disk manifest state), `model_downloader.py` (wraps `huggingface_hub.hf_hub_download`) — plus one `QWidget` (`model_catalog_widget.py`) shown inside the AI tab of the settings dialog when the provider is `local`. `LocalProvider` gains an `n_ctx` parameter so larger-context catalog models aren't pinned at 4096. The existing manual GGUF path drops into a collapsed "Advanced" section and still wins when set.

**Tech Stack:** Python, PyQt6, `huggingface_hub` (already a transitive dependency), `llama-cpp-python` (already the local-provider backend), `unittest` + `pytest` runner.

## Global Constraints

- Run tests with the venv interpreter: `.venv\Scripts\python.exe -m pytest tests/ -q`. The global Python has no pytest. Never bare `uv run`.
- Durable file writes go through `app/utils/atomic_io.py` (`atomic_write_json` / `atomic_write_text`) — never bare `open(w)`.
- Non-UI logic is TDD: write the failing test in `tests/`, confirm it fails, implement, confirm it passes.
- UI / PyQt code is smoke-tested only: `python -c "from app.x import Y"`. No Qt widget tests beyond pure-helper unit tests.
- Commits go to the current branch (`feature/ui-redesign`). Conventional prefixes: `feat:`, `fix:`, `docs:`, `ui:`, `config:`, `settings:`. Never add `Co-Authored-By` lines. Never `--amend`.
- Catalog models are ungated (no HuggingFace token). All GGUF, `Q4_K_M` quant.
- Model storage dir: `APP_DATA_DIR / "models"` where `APP_DATA_DIR` comes from `app.utils.app_paths`.
- `provider_factory` reads `config["ai"]["local_model_path"]` first (issue #13 precedence) — keep that. Selecting a catalog model writes its resolved absolute path there too.
- Catppuccin Mocha palette: Selected pill blue `#89b4fa`, Downloaded/healthy green `#a6e3a1`, muted text `#9397ab`, warning/red `#f38ba8`.
- After code changes: run `graphify update .` to refresh the knowledge graph.

---

### Task 1: Model catalog module

**Files:**
- Create: `app/ai/model_catalog.py`
- Test: `tests/test_model_catalog.py`

**Interfaces:**
- Consumes: `app.utils.app_paths.APP_DATA_DIR`
- Produces:
  - `@dataclass(frozen=True) class CatalogModel` with fields `key: str`, `display_name: str`, `hf_repo: str`, `hf_filename: str`, `size_bytes: int`, `context_tokens: int`, `ram_hint_gb: float`, `license: str`, `description: str`
  - `CATALOG: list[CatalogModel]` — 3 entries
  - `get(key: str) -> CatalogModel | None`
  - `models_dir() -> pathlib.Path` → `APP_DATA_DIR / "models"` (does **not** create it)
  - `local_path_for(key: str) -> pathlib.Path` → `models_dir() / <that model's hf_filename>` (raises `KeyError` for an unknown key)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_catalog.py
"""Tests for the built-in model catalog."""
import unittest

from app.ai import model_catalog
from app.ai.model_catalog import CATALOG, CatalogModel, get, local_path_for, models_dir
from app.utils.app_paths import APP_DATA_DIR


class CatalogShapeTest(unittest.TestCase):
    def test_catalog_is_non_empty_list_of_catalog_models(self):
        self.assertIsInstance(CATALOG, list)
        self.assertGreaterEqual(len(CATALOG), 3)
        self.assertTrue(all(isinstance(m, CatalogModel) for m in CATALOG))

    def test_keys_are_unique_and_non_empty(self):
        keys = [m.key for m in CATALOG]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(m.key for m in CATALOG))

    def test_every_entry_has_repo_filename_and_positive_numbers(self):
        for m in CATALOG:
            self.assertTrue(m.hf_repo, m.key)
            self.assertTrue(m.hf_filename.endswith(".gguf"), m.key)
            self.assertGreater(m.size_bytes, 100 * 1024 * 1024, m.key)
            self.assertGreater(m.context_tokens, 0, m.key)
            self.assertGreater(m.ram_hint_gb, 0, m.key)
            self.assertTrue(m.license, m.key)

    def test_get_returns_model_or_none(self):
        self.assertIs(get("nonexistent-key"), None)
        first = CATALOG[0]
        self.assertEqual(get(first.key), first)

    def test_models_dir_is_under_app_data_dir_and_not_created(self):
        self.assertEqual(models_dir(), APP_DATA_DIR / "models")

    def test_local_path_for_joins_models_dir_and_filename(self):
        m = CATALOG[0]
        self.assertEqual(local_path_for(m.key), models_dir() / m.hf_filename)
        with self.assertRaises(KeyError):
            local_path_for("nonexistent-key")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_catalog.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.model_catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/ai/model_catalog.py
"""Curated catalog of downloadable local GGUF models.

Hard-coded on purpose: no server to run, no network needed to browse the
list. Adding a model is a one-entry edit here. All entries are ungated on
HuggingFace (no token) and Q4_K_M quant.

size_bytes is the advertised download size; it drives the disk pre-check
and a soft sanity check after download (a >10% deviation is logged, not
treated as failure, so a stale number here can't block a good download).
"""
from dataclasses import dataclass
from pathlib import Path

from app.utils.app_paths import APP_DATA_DIR


@dataclass(frozen=True)
class CatalogModel:
    key: str
    display_name: str
    hf_repo: str
    hf_filename: str
    size_bytes: int
    context_tokens: int
    ram_hint_gb: float
    license: str
    description: str


CATALOG: list[CatalogModel] = [
    CatalogModel(
        key="qwen2.5-3b",
        display_name="Qwen2.5 3B Instruct",
        hf_repo="bartowski/Qwen2.5-3B-Instruct-GGUF",
        hf_filename="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        size_bytes=1_929_903_264,
        context_tokens=32_768,
        ram_hint_gb=4.0,
        license="Apache-2.0",
        description="Small, fast default. Good summaries on modest hardware.",
    ),
    CatalogModel(
        key="phi-3.5-mini",
        display_name="Phi-3.5 Mini Instruct",
        hf_repo="bartowski/Phi-3.5-mini-instruct-GGUF",
        hf_filename="Phi-3.5-mini-instruct-Q4_K_M.gguf",
        size_bytes=2_393_231_072,
        context_tokens=131_072,
        ram_hint_gb=5.0,
        license="MIT",
        description="Very large context window; strong at summarization.",
    ),
    CatalogModel(
        key="qwen2.5-7b",
        display_name="Qwen2.5 7B Instruct",
        hf_repo="bartowski/Qwen2.5-7B-Instruct-GGUF",
        hf_filename="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        size_bytes=4_683_073_344,
        context_tokens=32_768,
        ram_hint_gb=8.0,
        license="Apache-2.0",
        description="Higher quality; needs ~8 GB RAM and is slower on CPU.",
    ),
]

_BY_KEY = {m.key: m for m in CATALOG}


def get(key: str) -> CatalogModel | None:
    return _BY_KEY.get(key)


def models_dir() -> Path:
    return APP_DATA_DIR / "models"


def local_path_for(key: str) -> Path:
    return models_dir() / _BY_KEY[key].hf_filename
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_catalog.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai/model_catalog.py tests/test_model_catalog.py
git commit -m "feat: add built-in model catalog module"
```

---

### Task 2: Model store (on-disk manifest state)

**Files:**
- Create: `app/ai/model_store.py`
- Test: `tests/test_model_store.py`

**Interfaces:**
- Consumes: `app.ai.model_catalog` (`CATALOG`, `CatalogModel`, `models_dir`, `local_path_for`, `get`); `app.utils.atomic_io.atomic_write_json`
- Produces:
  - `manifest_path() -> Path` → `models_dir() / "manifest.json"`
  - `load_manifest() -> dict` — `{}` if missing/corrupt
  - `is_downloaded(key: str) -> bool` — manifest entry exists **and** `local_path_for(key)` exists on disk with size within 20% of the manifest's recorded `size`
  - `record_download(key: str, sha256: str) -> None` — writes/updates the manifest entry `{filename, sha256, size, downloaded_at}` (size + filename read from disk / catalog; `downloaded_at` = `datetime.now(timezone.utc).isoformat()`)
  - `remove(key: str) -> None` — deletes the `.gguf` file if present and drops the manifest entry; no error if already gone
  - `free_disk_bytes() -> int` — free bytes on the volume that would hold `models_dir()` (walk up to the first existing parent), via `shutil.disk_usage`
  - `@dataclass class ModelStatus` with `model: CatalogModel`, `downloaded: bool`
  - `list_status() -> list[ModelStatus]` — one per `CATALOG` entry, in catalog order

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_store.py
"""Tests for the model store / manifest."""
import json
import unittest
from unittest.mock import patch

from app.ai import model_catalog, model_store


class ModelStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        from pathlib import Path
        self._dir = Path(self._tmp) / "models"
        patcher = patch.object(model_catalog, "models_dir", return_value=self._dir)
        self.enterContext(patcher)
        # model_store calls model_catalog.models_dir() indirectly; make sure
        # both module references resolve to the patched function.
        self.enterContext(patch.object(model_store, "models_dir", model_catalog.models_dir))
        self.key = model_catalog.CATALOG[0].key
        self.fname = model_catalog.CATALOG[0].hf_filename

    def _write_fake_gguf(self, nbytes):
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / self.fname).write_bytes(b"\0" * nbytes)

    def test_load_manifest_missing_returns_empty_dict(self):
        self.assertEqual(model_store.load_manifest(), {})

    def test_load_manifest_corrupt_returns_empty_dict(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        model_store.manifest_path().write_text("{not json")
        self.assertEqual(model_store.load_manifest(), {})

    def test_is_downloaded_false_when_file_present_but_no_manifest_entry(self):
        self._write_fake_gguf(500)
        self.assertFalse(model_store.is_downloaded(self.key))

    def test_is_downloaded_false_when_manifest_entry_present_but_file_missing(self):
        self._dir.mkdir(parents=True, exist_ok=True)
        model_store.manifest_path().write_text(json.dumps(
            {self.key: {"filename": self.fname, "sha256": "x", "size": 500,
                        "downloaded_at": "2026-08-26T00:00:00+00:00"}}))
        self.assertFalse(model_store.is_downloaded(self.key))

    def test_record_download_then_is_downloaded_true(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "abc123")
        self.assertTrue(model_store.is_downloaded(self.key))
        entry = model_store.load_manifest()[self.key]
        self.assertEqual(entry["sha256"], "abc123")
        self.assertEqual(entry["size"], 500)
        self.assertEqual(entry["filename"], self.fname)

    def test_remove_deletes_file_and_entry_and_is_idempotent(self):
        self._write_fake_gguf(500)
        model_store.record_download(self.key, "abc123")
        model_store.remove(self.key)
        self.assertFalse((self._dir / self.fname).exists())
        self.assertNotIn(self.key, model_store.load_manifest())
        model_store.remove(self.key)  # no raise

    def test_free_disk_bytes_is_positive(self):
        self.assertGreater(model_store.free_disk_bytes(), 0)

    def test_list_status_covers_catalog_in_order(self):
        statuses = model_store.list_status()
        self.assertEqual([s.model.key for s in statuses],
                         [m.key for m in model_catalog.CATALOG])
        self.assertTrue(all(s.downloaded is False for s in statuses))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.model_store'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/ai/model_store.py
"""On-disk state for downloaded catalog models.

manifest.json under the models dir is the source of truth for "downloaded",
cross-checked against the file actually being on disk — a manifest entry
whose file was deleted outside the app, or a .gguf with no manifest entry
(interrupted download), both count as NOT downloaded.
"""
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.ai.model_catalog import CATALOG, CatalogModel, get, local_path_for, models_dir
from app.utils.atomic_io import atomic_write_json

_SIZE_TOLERANCE = 0.20  # fraction the on-disk file may deviate from the recorded size


def manifest_path() -> Path:
    return models_dir() / "manifest.json"


def load_manifest() -> dict:
    path = manifest_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _save_manifest(data: dict) -> None:
    models_dir().mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_path(), data, indent=2)


def is_downloaded(key: str) -> bool:
    entry = load_manifest().get(key)
    if not entry:
        return False
    path = local_path_for(key)
    if not path.exists():
        return False
    recorded = entry.get("size", 0)
    if recorded <= 0:
        return True
    actual = path.stat().st_size
    return abs(actual - recorded) <= _SIZE_TOLERANCE * recorded


def record_download(key: str, sha256: str) -> None:
    model = get(key)
    if model is None:
        raise KeyError(key)
    path = local_path_for(key)
    data = load_manifest()
    data[key] = {
        "filename": model.hf_filename,
        "sha256": sha256,
        "size": path.stat().st_size,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_manifest(data)


def remove(key: str) -> None:
    model = get(key)
    if model is not None:
        path = local_path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    data = load_manifest()
    if key in data:
        del data[key]
        _save_manifest(data)


def free_disk_bytes() -> int:
    probe = models_dir()
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free


@dataclass
class ModelStatus:
    model: CatalogModel
    downloaded: bool


def list_status() -> list[ModelStatus]:
    return [ModelStatus(model=m, downloaded=is_downloaded(m.key)) for m in CATALOG]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_store.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai/model_store.py tests/test_model_store.py
git commit -m "feat: add model store for downloaded catalog models"
```

---

### Task 3: Model downloader

**Files:**
- Create: `app/ai/model_downloader.py`
- Test: `tests/test_model_downloader.py`

**Interfaces:**
- Consumes: `app.ai.model_catalog.CatalogModel` / `models_dir`; `app.ai.model_store.record_download`; `huggingface_hub.hf_hub_download`
- Produces:
  - `class DownloadError(Exception)`
  - `class DownloadCancelled(Exception)`
  - `_MIN_VALID_BYTES = 100 * 1024 * 1024`
  - `download(model: CatalogModel, token: str = "", progress_cb=None, cancel_check=None) -> pathlib.Path`
    - `progress_cb`, when given, is called as `progress_cb(downloaded_bytes: int, total_bytes: int)`
    - `cancel_check`, when given, is a zero-arg callable returning `bool`; when it returns `True` the download raises `DownloadCancelled` and the partial file/cache under `models_dir()` is removed
    - on success: sanity-checks the file is at least `_MIN_VALID_BYTES`; computes sha256; calls `model_store.record_download(model.key, sha256)`; returns the final `Path`
    - wraps any other failure in `DownloadError`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_downloader.py
"""Tests for the catalog model downloader (hf_hub_download mocked)."""
import hashlib
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai import model_catalog, model_downloader, model_store
from app.ai.model_downloader import DownloadCancelled, DownloadError, download


class DownloaderTest(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(__import__("tempfile").TemporaryDirectory())
        self._dir = Path(tmp) / "models"
        self._dir.mkdir(parents=True)
        self.enterContext(patch.object(model_catalog, "models_dir", return_value=self._dir))
        self.enterContext(patch.object(model_downloader, "models_dir", model_catalog.models_dir))
        self.enterContext(patch.object(model_store, "models_dir", model_catalog.models_dir))
        self.model = model_catalog.CATALOG[0]

    def _fake_hf_download(self, valid_bytes=model_downloader._MIN_VALID_BYTES + 1):
        def _impl(*, repo_id, filename, local_dir, token=None, tqdm_class=None, **kw):
            dest = Path(local_dir) / filename
            dest.write_bytes(b"\0" * valid_bytes)
            return str(dest)
        return _impl

    def test_success_records_manifest_and_returns_path(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=self._fake_hf_download()):
            path = download(self.model)
        self.assertTrue(path.exists())
        self.assertTrue(model_store.is_downloaded(self.model.key))
        expected_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(model_store.load_manifest()[self.model.key]["sha256"], expected_sha)

    def test_truncated_download_raises_download_error(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=self._fake_hf_download(valid_bytes=1024)):
            with self.assertRaises(DownloadError):
                download(self.model)
        self.assertFalse(model_store.is_downloaded(self.model.key))

    def test_network_failure_is_wrapped_in_download_error(self):
        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=OSError("connection reset")):
            with self.assertRaises(DownloadError):
                download(self.model)

    def test_cancel_raises_and_cleans_partial(self):
        def _cancelling_download(*, repo_id, filename, local_dir, token=None,
                                 tqdm_class=None, **kw):
            # Simulate hf streaming: drive the tqdm shim, which polls cancel.
            bar = tqdm_class(total=1000) if tqdm_class else None
            (Path(local_dir) / filename).write_bytes(b"\0" * 2048)  # partial
            if bar:
                bar.update(500)  # progress_cb -> cancel_check True -> raises
            return str(Path(local_dir) / filename)

        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=_cancelling_download):
            with self.assertRaises(DownloadCancelled):
                download(self.model, cancel_check=lambda: True)
        self.assertFalse((self._dir / self.model.hf_filename).exists())

    def test_progress_cb_receives_downloaded_and_total(self):
        seen = []

        def _driving_download(*, repo_id, filename, local_dir, token=None,
                              tqdm_class=None, **kw):
            bar = tqdm_class(total=2000)
            bar.update(1000)
            bar.update(1000)
            dest = Path(local_dir) / filename
            dest.write_bytes(b"\0" * (model_downloader._MIN_VALID_BYTES + 1))
            return str(dest)

        with patch("app.ai.model_downloader.hf_hub_download",
                   side_effect=_driving_download):
            download(self.model, progress_cb=lambda d, t: seen.append((d, t)))
        self.assertIn((1000, 2000), seen)
        self.assertIn((2000, 2000), seen)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_downloader.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.model_downloader'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/ai/model_downloader.py
"""Download a catalog model from HuggingFace into the app's models dir.

Wraps huggingface_hub.hf_hub_download (HTTP resume + its own .incomplete
temp file). Progress is surfaced through a tqdm shim because hf_hub_download
does not expose a plain callback; if a future hub version stops honouring
tqdm_class the bar simply stays at 0 until completion — the download itself
is unaffected.
"""
import hashlib
import shutil

from huggingface_hub import hf_hub_download

from app.ai.model_catalog import CatalogModel, local_path_for, models_dir
from app.ai import model_store

_MIN_VALID_BYTES = 100 * 1024 * 1024  # smaller than this = an error page, not a model


class DownloadError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


def _make_tqdm_class(progress_cb, cancel_check):
    from tqdm.auto import tqdm as _base

    class _CallbackTqdm(_base):
        def update(self, n=1):
            super().update(n)
            if cancel_check is not None and cancel_check():
                raise DownloadCancelled()
            if progress_cb is not None and self.total:
                try:
                    progress_cb(int(self.n), int(self.total))
                except DownloadCancelled:
                    raise
                except Exception:
                    pass

    return _CallbackTqdm


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _clean_partial(model: CatalogModel) -> None:
    try:
        local_path_for(model.key).unlink()
    except FileNotFoundError:
        pass
    cache = models_dir() / ".cache"
    if cache.exists():
        shutil.rmtree(cache, ignore_errors=True)


def download(model: CatalogModel, token: str = "", progress_cb=None,
             cancel_check=None):
    models_dir().mkdir(parents=True, exist_ok=True)
    tqdm_class = _make_tqdm_class(progress_cb, cancel_check)
    try:
        result = hf_hub_download(
            repo_id=model.hf_repo,
            filename=model.hf_filename,
            local_dir=str(models_dir()),
            token=token or None,
            tqdm_class=tqdm_class,
        )
    except DownloadCancelled:
        _clean_partial(model)
        raise
    except Exception as e:  # network, auth, disk full, hub API changes
        _clean_partial(model)
        raise DownloadError(str(e)) from e

    from pathlib import Path
    path = Path(result)
    if not path.exists() or path.stat().st_size < _MIN_VALID_BYTES:
        _clean_partial(model)
        raise DownloadError(
            f"Downloaded file is too small ({path.stat().st_size if path.exists() else 0} "
            f"bytes) — the download was truncated or the server returned an error page."
        )
    model_store.record_download(model.key, _sha256(path))
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_downloader.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add app/ai/model_downloader.py tests/test_model_downloader.py
git commit -m "feat: add catalog model downloader"
```

---

### Task 4: LocalProvider n_ctx + factory + config key

**Files:**
- Modify: `app/ai/local_provider.py`
- Modify: `app/ai/provider_factory.py:47-52`
- Modify: `app/utils/config.py:56-64` (the `"ai"` block of `DEFAULT_CONFIG`)
- Modify: `tests/test_ai_provider.py:120-123`
- Test: `tests/test_ai_provider.py` (new cases), `tests/test_config.py` (new case)

**Interfaces:**
- Consumes: `app.ai.model_catalog.get`
- Produces:
  - `LocalProvider.__init__(self, model_path: str, embed_model: str = "all-MiniLM-L6-v2", n_ctx: int = 4096)`; instance sets `self.max_context_chars = max(8_000, (n_ctx - 2_048) * 3)` and `self._n_ctx = n_ctx` (used as `Llama(n_ctx=self._n_ctx)`)
  - `provider_factory._resolve_local_n_ctx(config: dict) -> int` — if `config.get("local_model_name")` names a catalog model, `min(model.context_tokens, 8192)`, else `4096`
  - `config` gains `DEFAULT_CONFIG["ai"]["local_model_name"] = ""`

- [ ] **Step 1: Write the failing tests**

Replace the body of `test_local_provider_has_small_context_limit` (line 120) and add two cases in `tests/test_ai_provider.py`:

```python
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
```

Add to `tests/test_config.py` (inside an existing `Config` test class):

```python
    def test_ai_block_has_local_model_name_default(self):
        from app.utils.config import DEFAULT_CONFIG
        self.assertEqual(DEFAULT_CONFIG["ai"]["local_model_name"], "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ai_provider.py tests/test_config.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'n_ctx'`; `ImportError: cannot import name '_resolve_local_n_ctx'`; `KeyError: 'local_model_name'`

- [ ] **Step 3: Write minimal implementation**

`app/ai/local_provider.py` — replace the class-level `max_context_chars` and `__init__`/`_get_llm`:

```python
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
```

Delete the now-unused class attribute `max_context_chars = 8_000` line and its comment.

`app/ai/provider_factory.py` — add the helper and use it in the `local` branch:

```python
def _resolve_local_n_ctx(config: dict) -> int:
    from app.ai.model_catalog import get
    model = get(config.get("local_model_name") or "")
    if model is None:
        return 4096
    return min(model.context_tokens, 8192)
```

```python
    if provider_type == "local":
        from app.ai.local_provider import LocalProvider
        return LocalProvider(
            model_path=config.get("local_model_path") or config.get("model", ""),
            embed_model=config.get("embed_model", "all-MiniLM-L6-v2"),
            n_ctx=_resolve_local_n_ctx(config),
        )
```

`app/utils/config.py` — add one line to the `"ai"` dict:

```python
    "ai": {
        "provider": "none",
        "api_key": "",
        "model": "",
        "local_model_path": "",
        "local_model_name": "",
        "embed_model": "all-MiniLM-L6-v2",
        "auto_summarize": True,
        "provider_settings": {},
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ai_provider.py tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/ai/local_provider.py app/ai/provider_factory.py app/utils/config.py tests/test_ai_provider.py tests/test_config.py
git commit -m "feat: let LocalProvider take n_ctx from the selected catalog model"
```

---

### Task 5: Prebuilt CPU wheel index for llama-cpp-python

**Files:**
- Modify: `app/utils/package_installer.py`
- Modify: `tests/test_package_installer.py`
- Modify: `.claude/rules/packaging-and-launch.md` (add a short note under a new "## llama-cpp-python (local AI provider)" heading)

**Interfaces:**
- Produces:
  - `PREBUILT_INDEX_URLS: dict[str, str]` = `{"local": "https://abetlen.github.io/llama-cpp-python/whl/cpu"}`
  - `extra_index_url_for(provider_type: str) -> str | None`
  - `_install_command(pip_package: str, extra_index_url: str | None = None) -> list[str] | None` — when `extra_index_url` is set, inserts `--extra-index-url <url>` immediately before `pip_package` for both the pip and uv forms
  - `install_package(pip_package: str, extra_index_url: str | None = None) -> tuple[bool, str]` — passes `extra_index_url` through

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_package_installer.py`:

```python
class ExtraIndexUrlTest(unittest.TestCase):
    def test_local_provider_has_a_prebuilt_cpu_wheel_index(self):
        from app.utils.package_installer import extra_index_url_for
        url = extra_index_url_for("local")
        self.assertIn("llama-cpp-python", url)
        self.assertIn("cpu", url)

    def test_api_providers_have_no_extra_index(self):
        from app.utils.package_installer import extra_index_url_for
        for p in ("claude", "openai", "grok", "gemini", "mistral"):
            self.assertIsNone(extra_index_url_for(p))

    def test_install_command_pip_form_inserts_extra_index_before_package(self):
        with patch.object(package_installer.importlib.util, "find_spec", return_value=object()):
            cmd = _install_command("llama-cpp-python>=0.3.0",
                                   extra_index_url="https://example/whl/cpu")
        self.assertEqual(cmd, [
            sys.executable, "-m", "pip", "install",
            "--extra-index-url", "https://example/whl/cpu",
            "llama-cpp-python>=0.3.0",
        ])

    def test_install_command_uv_form_inserts_extra_index_before_package(self):
        with patch.object(package_installer.importlib.util, "find_spec", return_value=None), \
             patch.object(package_installer.shutil, "which", return_value="uv"):
            cmd = _install_command("llama-cpp-python>=0.3.0",
                                   extra_index_url="https://example/whl/cpu")
        self.assertEqual(cmd, [
            "uv", "pip", "install", "--python", sys.executable,
            "--extra-index-url", "https://example/whl/cpu",
            "llama-cpp-python>=0.3.0",
        ])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_package_installer.py -q`
Expected: FAIL — `ImportError: cannot import name 'extra_index_url_for'`; `_install_command() got an unexpected keyword argument 'extra_index_url'`

- [ ] **Step 3: Write minimal implementation**

`app/utils/package_installer.py`:

```python
# llama-cpp-python publishes no wheel on PyPI for many Windows/Python combos,
# so a plain `pip install` compiles from source (needs CMake + MSVC). Point
# it at the maintainer's prebuilt CPU wheel index instead.
PREBUILT_INDEX_URLS = {
    "local": "https://abetlen.github.io/llama-cpp-python/whl/cpu",
}


def extra_index_url_for(provider_type: str) -> str | None:
    return PREBUILT_INDEX_URLS.get(provider_type)
```

```python
def _install_command(pip_package: str, extra_index_url: str | None = None) -> list[str] | None:
    extra = ["--extra-index-url", extra_index_url] if extra_index_url else []
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", *extra, pip_package]
    uv = shutil.which("uv")
    if uv:
        return [uv, "pip", "install", "--python", sys.executable, *extra, pip_package]
    return None


def install_package(pip_package: str, extra_index_url: str | None = None) -> tuple[bool, str]:
    cmd = _install_command(pip_package, extra_index_url)
    # ...rest unchanged...
```

(Leave the existing `_install_command` docstring; append one sentence: "An
`extra_index_url` is inserted right before the package for both forms.")

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_package_installer.py -q`
Expected: PASS

- [ ] **Step 5: Update the packaging rule doc**

Add to `.claude/rules/packaging-and-launch.md`:

```markdown
## llama-cpp-python (local AI provider)

`llama-cpp-python` is NOT in base deps — it's installed on demand when the
user picks the Local Model provider. The ad-hoc installer
(`package_installer.install_package`) passes
`--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`
(`extra_index_url_for("local")`) so pip pulls a prebuilt CPU wheel instead
of compiling from source (which needs CMake + MSVC Build Tools). GPU wheels
are a separate opt-in and out of scope for now.
```

- [ ] **Step 6: Commit**

```bash
git add app/utils/package_installer.py tests/test_package_installer.py .claude/rules/packaging-and-launch.md
git commit -m "feat: install llama-cpp-python from the prebuilt CPU wheel index"
```

---

### Task 6: ModelCatalogWidget

**Files:**
- Create: `app/ui/model_catalog_widget.py`
- Test: `tests/test_model_catalog_widget.py` (pure-helper only)

**Interfaces:**
- Consumes: `app.ai.model_catalog` (`CATALOG`, `CatalogModel`), `app.ai.model_store` (`list_status`, `is_downloaded`, `remove`, `free_disk_bytes`), `app.ai.model_downloader` (`download`, `DownloadError`, `DownloadCancelled`)
- Produces:
  - `def human_size(nbytes: int) -> str` — module function, e.g. `1_929_903_264 -> "1.8 GB"`
  - `def row_detail_line(model: CatalogModel) -> str` — e.g. `"1.8 GB · 32k context · needs ~4 GB RAM · Apache-2.0"` (context shown as `"{n//1000}k"` when >= 1000 else the number)
  - `def disk_warning(free_bytes: int, model: CatalogModel) -> str | None` — a message string when `free_bytes < 1.5 * model.size_bytes`, else `None`
  - `class _DownloadWorker(QThread)` — `__init__(self, model, token="")`; signals `progress = pyqtSignal(int)` (0–100), `finished_ok = pyqtSignal(str)` (model key), `failed = pyqtSignal(str)` (message), `cancelled = pyqtSignal()`; method `cancel(self)`
  - `class ModelCatalogWidget(QWidget)`:
    - `__init__(self, token_getter=lambda: "", parent=None)` (`token_getter` returns the HF token for later gated models; unused by v1 catalog)
    - `selected_key() -> str` / `set_selected_key(key: str) -> None`
    - `refresh() -> None` — rebuild every row's state from `model_store`
    - signals: `selection_changed = pyqtSignal(str)`, `download_active_changed = pyqtSignal(bool)`
    - `is_download_active() -> bool`
    - `abort_active_download() -> None` — cancels the running worker and blocks (`wait()`) until it ends (called from the dialog's `reject`)

- [ ] **Step 1: Write the failing test (pure helpers only)**

```python
# tests/test_model_catalog_widget.py
"""Pure-helper tests for the model catalog widget (no Qt widgets)."""
import unittest

from app.ai.model_catalog import CATALOG
from app.ui.model_catalog_widget import disk_warning, human_size, row_detail_line


class HelperTest(unittest.TestCase):
    def test_human_size_gb(self):
        self.assertEqual(human_size(1_929_903_264), "1.8 GB")
        self.assertEqual(human_size(4_683_073_344), "4.4 GB")

    def test_human_size_mb(self):
        self.assertEqual(human_size(500 * 1024 * 1024), "500.0 MB")

    def test_row_detail_line_has_size_context_ram_license(self):
        line = row_detail_line(CATALOG[0])
        self.assertIn("GB", line)
        self.assertIn("32k context", line)
        self.assertIn("~4 GB RAM", line)
        self.assertIn("Apache-2.0", line)

    def test_disk_warning_none_when_plenty_of_space(self):
        m = CATALOG[0]
        self.assertIsNone(disk_warning(m.size_bytes * 5, m))

    def test_disk_warning_message_when_tight(self):
        m = CATALOG[0]
        msg = disk_warning(int(m.size_bytes * 1.1), m)
        self.assertIsNotNone(msg)
        self.assertIn("free", msg.lower())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_catalog_widget.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ui.model_catalog_widget'`

- [ ] **Step 3: Write the implementation**

```python
# app/ui/model_catalog_widget.py
"""Downloadable-model catalog UI for the Local Model provider.

Shown inside Settings ▸ AI Assistant when the provider is "local". One row
per app/ai/model_catalog.CATALOG entry: name + status pill, a detail line,
and a stacked control that is Download → (progress + Cancel) → Select /
Remove. Exactly one download runs at a time; while it does, the parent
dialog disables Save and the provider combo (see download_active_changed).
"""
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from app.ai.model_catalog import CATALOG, CatalogModel
from app.ai import model_store
from app.ai.model_downloader import DownloadCancelled, DownloadError, download

_PILL_SELECTED = "#89b4fa"
_PILL_DOWNLOADED = "#a6e3a1"
_MUTED = "#9397ab"


def human_size(nbytes: int) -> str:
    mb = nbytes / (1024 * 1024)
    if mb >= 1024:
        return f"{mb / 1024:.1f} GB"
    return f"{mb:.1f} MB"


def row_detail_line(model: CatalogModel) -> str:
    ctx = f"{model.context_tokens // 1000}k" if model.context_tokens >= 1000 else str(model.context_tokens)
    ram = f"{model.ram_hint_gb:g}"
    return f"{human_size(model.size_bytes)} · {ctx} context · needs ~{ram} GB RAM · {model.license}"


def disk_warning(free_bytes: int, model: CatalogModel) -> str | None:
    need = int(1.5 * model.size_bytes)
    if free_bytes >= need:
        return None
    return (
        f"Only {human_size(free_bytes)} free on disk. "
        f"{model.display_name} needs about {human_size(model.size_bytes)} "
        f"(plus headroom). Download anyway?"
    )


class _DownloadWorker(QThread):
    progress = pyqtSignal(int)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, model: CatalogModel, token: str = ""):
        super().__init__()
        self._model = model
        self._token = token
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _on_progress(self, done: int, total: int):
        if total:
            self.progress.emit(int(done * 100 / total))

    def run(self):
        try:
            download(
                self._model,
                token=self._token,
                progress_cb=self._on_progress,
                cancel_check=lambda: self._cancelled,
            )
        except DownloadCancelled:
            self.cancelled.emit()
        except DownloadError as e:
            self.failed.emit(str(e))
        except Exception as e:  # defensive: never let the worker crash silently
            self.failed.emit(str(e))
        else:
            self.finished_ok.emit(self._model.key)


class _ModelRow(QWidget):
    select_requested = pyqtSignal(str)
    download_requested = pyqtSignal(str)
    cancel_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    def __init__(self, model: CatalogModel, parent=None):
        super().__init__(parent)
        self.model = model
        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 6, 4, 6)

        top = QHBoxLayout()
        self._name = QLabel(f"<b>{model.display_name}</b>")
        self._pill = QLabel("")
        top.addWidget(self._name)
        top.addWidget(self._pill)
        top.addStretch()

        self._download_btn = QPushButton("Download")
        self._download_btn.clicked.connect(lambda: self.download_requested.emit(model.key))
        self._select_btn = QPushButton("Select")
        self._select_btn.clicked.connect(lambda: self.select_requested.emit(model.key))
        self._remove_btn = QPushButton("Remove")
        self._remove_btn.clicked.connect(lambda: self.remove_requested.emit(model.key))
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.clicked.connect(lambda: self.cancel_requested.emit(model.key))
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        for w in (self._download_btn, self._select_btn, self._remove_btn,
                  self._cancel_btn, self._bar):
            top.addWidget(w)
        outer.addLayout(top)

        detail = QLabel(row_detail_line(model))
        detail.setStyleSheet(f"color: {_MUTED};")
        outer.addWidget(detail)

        self._note = QLabel("")
        self._note.setStyleSheet(f"color: {_MUTED};")
        self._note.setVisible(False)
        outer.addWidget(self._note)

    def set_state(self, *, downloaded: bool, selected: bool, overridden: bool):
        self._pill.setVisible(selected or downloaded)
        if selected:
            self._pill.setText(f'<span style="color:{_PILL_SELECTED};">● Selected</span>')
        elif downloaded:
            self._pill.setText(f'<span style="color:{_PILL_DOWNLOADED};">● Downloaded</span>')
        self._download_btn.setVisible(not downloaded)
        self._select_btn.setVisible(downloaded and not selected)
        self._remove_btn.setVisible(downloaded)
        self._cancel_btn.setVisible(False)
        self._bar.setVisible(False)
        self._note.setVisible(overridden)
        if overridden:
            self._note.setText("Overridden by the custom GGUF path below.")

    def set_downloading(self, percent: int):
        for w in (self._download_btn, self._select_btn, self._remove_btn):
            w.setVisible(False)
        self._cancel_btn.setVisible(True)
        self._bar.setVisible(True)
        self._bar.setValue(percent)

    def set_buttons_enabled(self, enabled: bool):
        for w in (self._download_btn, self._select_btn, self._remove_btn):
            w.setEnabled(enabled)


class ModelCatalogWidget(QWidget):
    selection_changed = pyqtSignal(str)
    download_active_changed = pyqtSignal(bool)

    def __init__(self, token_getter=lambda: "", parent=None):
        super().__init__(parent)
        self._token_getter = token_getter
        self._selected_key = ""
        self._custom_path_active = False
        self._worker: _DownloadWorker | None = None
        self._downloading_key = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._rows: dict[str, _ModelRow] = {}
        for model in CATALOG:
            row = _ModelRow(model)
            row.download_requested.connect(self._start_download)
            row.cancel_requested.connect(self._cancel_download)
            row.select_requested.connect(self._select)
            row.remove_requested.connect(self._remove)
            self._rows[model.key] = row
            layout.addWidget(row)
        self.refresh()

    # ---- public API -------------------------------------------------------

    def selected_key(self) -> str:
        return self._selected_key

    def set_selected_key(self, key: str) -> None:
        self._selected_key = key or ""
        self.refresh()

    def set_custom_path_active(self, active: bool) -> None:
        self._custom_path_active = bool(active)
        self.refresh()

    def is_download_active(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def abort_active_download(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._worker.wait(15000)

    def refresh(self) -> None:
        for key, row in self._rows.items():
            if key == self._downloading_key and self.is_download_active():
                continue
            row.set_state(
                downloaded=model_store.is_downloaded(key),
                selected=(key == self._selected_key and not self._custom_path_active),
                overridden=self._custom_path_active,
            )

    # ---- slots ----------------------------------------------------------

    def _select(self, key: str):
        if not model_store.is_downloaded(key):
            return
        self._selected_key = key
        self.refresh()
        self.selection_changed.emit(key)

    def _remove(self, key: str):
        model = next(m for m in CATALOG if m.key == key)
        if QMessageBox.question(
            self, "Remove model",
            f"Delete {model.display_name} from disk? "
            f"You can download it again later.",
        ) != QMessageBox.StandardButton.Yes:
            return
        model_store.remove(key)
        if self._selected_key == key:
            self._selected_key = ""
            self.selection_changed.emit("")
        self.refresh()

    def _start_download(self, key: str):
        if self.is_download_active():
            return
        model = next(m for m in CATALOG if m.key == key)
        warn = disk_warning(model_store.free_disk_bytes(), model)
        if warn and QMessageBox.question(self, "Low disk space", warn) != \
                QMessageBox.StandardButton.Yes:
            return
        self._downloading_key = key
        self._worker = _DownloadWorker(model, token=self._token_getter() or "")
        self._worker.progress.connect(self._rows[key].set_downloading)
        self._worker.finished_ok.connect(self._on_download_ok)
        self._worker.failed.connect(self._on_download_failed)
        self._worker.cancelled.connect(self._on_download_cancelled)
        self._rows[key].set_downloading(0)
        for other, row in self._rows.items():
            if other != key:
                row.set_buttons_enabled(False)
        self._worker.start()
        self.download_active_changed.emit(True)

    def _cancel_download(self, key: str):
        if self._worker is not None:
            self._worker.cancel()

    def _teardown_worker(self):
        self._worker = None
        self._downloading_key = ""
        for row in self._rows.values():
            row.set_buttons_enabled(True)
        self.download_active_changed.emit(False)
        self.refresh()

    def _on_download_ok(self, key: str):
        self._teardown_worker()
        if not self._selected_key and not self._custom_path_active:
            self._selected_key = key
            self.selection_changed.emit(key)
            self.refresh()

    def _on_download_failed(self, message: str):
        self._teardown_worker()
        QMessageBox.warning(self, "Download failed", message)

    def _on_download_cancelled(self):
        self._teardown_worker()
```

- [ ] **Step 4: Run the helper tests + smoke-test the import**

Run: `.venv\Scripts\python.exe -m pytest tests/test_model_catalog_widget.py -q`
Expected: PASS (5 tests)

Run: `.venv\Scripts\python.exe -c "from app.ui.model_catalog_widget import ModelCatalogWidget, _DownloadWorker; print('ok')"`
Expected: prints `ok`

- [ ] **Step 5: Commit**

```bash
git add app/ui/model_catalog_widget.py tests/test_model_catalog_widget.py
git commit -m "feat: add ModelCatalogWidget for the local model catalog"
```

---

### Task 7: Wire the catalog widget into the settings dialog

**Files:**
- Modify: `app/ui/settings_dialog.py` — AI tab construction (~L447-463), `_on_ai_provider_changed` (~L750-782), `_save_current_provider_settings` (~L783-792), `_restore_provider_settings` (~L794-806), `_load_settings` seed (~L611-629), `_apply_settings` AI block (~L717-734), `reject`/`closeEvent` handling
- Test: none automated (UI). Smoke tests below.

**Interfaces:**
- Consumes: `app.ui.model_catalog_widget.ModelCatalogWidget`; `app.ui.collapsible_section.CollapsibleSection`; `app.ai.model_catalog.local_path_for`
- Produces: no new public API — internal wiring only. The per-provider settings cache dict for `local` now also carries `"local_model_name"`.

- [ ] **Step 1: Build the widgets in the AI tab**

In `_setup_ui`, after the `self.ai_local_*` row is created, add:

```python
        # Built-in model catalog (primary control for the Local provider)
        from app.ui.model_catalog_widget import ModelCatalogWidget
        from app.ui.collapsible_section import CollapsibleSection

        self.model_catalog_widget = ModelCatalogWidget(
            token_getter=lambda: self.hf_token_edit.text().strip()
        )
        self.model_catalog_widget.selection_changed.connect(self._on_catalog_selection_changed)
        self.model_catalog_widget.download_active_changed.connect(self._on_catalog_download_active)
        ai_form.addRow(self.model_catalog_widget)

        self.ai_advanced_section = CollapsibleSection("Advanced: use a custom GGUF file")
        self.ai_advanced_section.content_layout().addWidget(self.ai_local_path)
        self.ai_advanced_section.content_layout().addWidget(self.ai_local_browse)
        ai_form.addRow(self.ai_advanced_section)
        self.ai_local_path.textChanged.connect(self._on_custom_path_changed)
```

Remove the old `local_row`/`ai_form.addRow(self.ai_local_label, local_row)` lines (the two widgets now live inside `ai_advanced_section`). Keep `self.ai_local_label` defined but don't add it to a row (or delete it and its `setVisible` calls — simplest to delete).

Add a `QLabel` handle for the Model row so it can be hidden for `local`:

```python
        self.ai_model_label = QLabel("Model:")
        ai_form.addRow(self.ai_model_label, self.ai_model)
```

(replacing `ai_form.addRow("Model:", self.ai_model)`).

- [ ] **Step 2: Toggle visibility in `_on_ai_provider_changed`**

Replace the `is_local` visibility block:

```python
        is_local = provider == "local"
        self.ai_api_key.setVisible(is_api)
        self.ai_api_key_label.setVisible(is_api)
        self.ai_model.setVisible(not is_local and provider != "none")
        self.ai_model_label.setVisible(not is_local and provider != "none")
        self.model_catalog_widget.setVisible(is_local)
        self.ai_advanced_section.setVisible(is_local)
```

Delete the `elif provider == "local": self.ai_model.addItem("(set path below)")` branch.

- [ ] **Step 3: Add the new slots**

```python
    def _on_catalog_selection_changed(self, key: str):
        # Selecting a catalog model clears any custom path so it takes effect.
        if key:
            self.ai_local_path.blockSignals(True)
            self.ai_local_path.clear()
            self.ai_local_path.blockSignals(False)
            self.model_catalog_widget.set_custom_path_active(False)

    def _on_custom_path_changed(self, text: str):
        active = bool(text.strip())
        self.model_catalog_widget.set_custom_path_active(active)

    def _on_catalog_download_active(self, active: bool):
        # Block Save + provider switching while a model downloads.
        self.ai_provider_combo.setEnabled(not active)
        for btn in self.findChildren(QPushButton):
            if btn.text() == "Save":
                btn.setEnabled(not active)
```

(If a `Save` button handle is already stored, use it instead of the `findChildren` scan — check how `ok_btn` is referenced; store `self._ok_btn = ok_btn` at creation if not.)

- [ ] **Step 4: Persist and restore the selection**

In `_save_current_provider_settings`, extend the `local` dict:

```python
        self._provider_settings[prev] = {
            "api_key": self.ai_api_key.text(),
            "model": self.ai_model.currentText(),
            "local_model_path": self.ai_local_path.text(),
            "local_model_name": self.model_catalog_widget.selected_key(),
        }
```

In `_restore_provider_settings`:

```python
        self.model_catalog_widget.set_selected_key(saved.get("local_model_name", ""))
```

In `_load_settings`, extend the migration seed dict (~L619-623) with:

```python
                "local_model_name": self.config.get("ai", "local_model_name") or "",
```

In `_apply_settings`, after the existing `local_model_path` line, resolve the
catalog selection to a concrete path so `provider_factory` (which reads
`local_model_path` first) keeps working:

```python
        active = self._provider_settings.get(provider_type, {})
        self.config.set("ai", "api_key", active.get("api_key", ""))
        self.config.set("ai", "model", active.get("model", ""))
        local_name = active.get("local_model_name", "")
        local_path = active.get("local_model_path", "")
        if provider_type == "local" and local_name and not local_path:
            from app.ai.model_catalog import local_path_for
            local_path = str(local_path_for(local_name))
        self.config.set("ai", "local_model_path", local_path)
        self.config.set("ai", "local_model_name", local_name if provider_type == "local" else "")
```

- [ ] **Step 5: Abort a running download on dialog close**

Find the `reject` path (Cancel button connects to `self.reject`). Override:

```python
    def reject(self):
        if self.model_catalog_widget.is_download_active():
            self.model_catalog_widget.abort_active_download()
        super().reject()
```

And guard `_save_and_close` at the top:

```python
        if self.model_catalog_widget.is_download_active():
            QMessageBox.information(
                self, "Settings",
                "A model is still downloading. Wait for it to finish or cancel it first.",
            )
            return
```

- [ ] **Step 6: Smoke-test**

Run: `.venv\Scripts\python.exe -c "from app.ui.settings_dialog import SettingsDialog; print('ok')"`
Expected: prints `ok`

Run the full suite to confirm nothing regressed:
Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: PASS (pre-existing `tests/test_single_instance.py` failures only if the app is running)

- [ ] **Step 7: Manual check (dev box, one-time)**

Launch the app, open Settings ▸ AI Assistant, pick **Local Model**. Confirm:
the three model rows render with size/context/RAM/licence; **Download** on
`qwen2.5-3b` shows a progress bar and completes; the row flips to
**Selected**; Save persists; generating a summary on a transcript runs
offline. Then **Remove** it and confirm the row returns to **Download**.

- [ ] **Step 8: Commit**

```bash
git add app/ui/settings_dialog.py
git commit -m "feat: show the built-in model catalog in the Local Model settings"
```

---

### Task 8: Documentation

**Files:**
- Modify: `CLAUDE.md` — "Current Features" list and the "Configuration" → AI settings line
- Modify: `.claude/rules/ai-providers.md` — add a "## Local model catalog" subsection

- [ ] **Step 1: Update `CLAUDE.md`**

Under **Current Features**, add a bullet near the AI provider bullets:

```markdown
- **Built-in model catalog (local provider):** Settings ▸ AI Assistant ▸ Local Model shows a curated list of downloadable GGUF models (Qwen2.5 3B/7B, Phi-3.5 Mini) with size / context / RAM / licence, a download manager (progress, cancel, remove), and one-click selection. Models land in `APP_DATA_DIR/models/` with a `manifest.json`; the manual GGUF path moves to an "Advanced" section and still wins when set. `llama-cpp-python` installs from the prebuilt CPU wheel index.
```

In the **Configuration** section's AI settings line, add `local_model_name` (catalog key of the selected built-in model; its resolved path is mirrored into `local_model_path` for `provider_factory`).

- [ ] **Step 2: Update `.claude/rules/ai-providers.md`**

Add after the "## Config keys" section:

```markdown
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
```

- [ ] **Step 3: Refresh the knowledge graph**

Run: `graphify update .`

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .claude/rules/ai-providers.md graphify-out/
git commit -m "docs: document the built-in model catalog"
```

---

## Self-Review

**1. Spec coverage:**
- Architecture / 4 modules → Tasks 1, 2, 3, 6.
- CPU wheel index → Task 5.
- Hard-coded catalog + starter models → Task 1.
- Storage layout + manifest + "downloaded = manifest AND file" → Task 2.
- Downloader (`hf_hub_download`, resume, progress, cancel, size sanity, sha256) → Task 3.
- Disk pre-check → Task 6 (`disk_warning` + `_start_download`).
- Settings UI: catalog folded into `local`, Advanced collapsible, pills, Download/Select/Remove, one-at-a-time, Save/combo disabled during download, abort on close → Tasks 6 + 7.
- Config `local_model_name`; mirror path into `local_model_path`; factory unchanged precedence; `n_ctx` from catalog context → Task 4 + Task 7 Step 4.
- `LocalProvider` `n_ctx` + `max_context_chars` derivation → Task 4.
- Packaging notes + feature list + rules → Tasks 5 & 8.
- Edge cases (interrupted download, close mid-download, disk full, file deleted outside app, provider switch blocked, custom-path precedence) → covered across Tasks 2 (`is_downloaded` file check), 3 (`_clean_partial`, `DownloadError`), 6 (`abort_active_download`, buttons disabled), 7 (`reject`, combo disable, `_on_custom_path_changed`).
- Out of scope items (GPU wheels, gated models, remote catalog, background downloads, prompt tuning) → not implemented; `token`/`token_getter` params plumbed for the later gated-model addition.

**2. Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Every code step has real code. `size_bytes` values in Task 1 are concrete integers flagged as verify-on-implement, with the downloader's `_MIN_VALID_BYTES` + `model_store`'s 20% tolerance making an imperfect number non-fatal.

**3. Type consistency:**
- `CatalogModel` fields identical across Tasks 1, 2, 3, 6.
- `models_dir()` (no creation) vs `.mkdir` at call sites — consistent: store and downloader create it, catalog does not.
- `_DownloadWorker` signals (`progress`, `finished_ok`, `failed`, `cancelled`) match between Task 6 definition and its connections.
- `ModelCatalogWidget` public methods (`selected_key`, `set_selected_key`, `set_custom_path_active`, `is_download_active`, `abort_active_download`, `refresh`) match Task 7's call sites.
- `_resolve_local_n_ctx` name identical in Task 4 impl and its test.
- `extra_index_url_for` / `install_package(..., extra_index_url=)` names identical across Task 5 impl, tests, and the Task 5 rule doc.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-26-builtin-model-catalog.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
