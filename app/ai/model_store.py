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
