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
