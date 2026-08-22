"""Tag management and persistence for TalkTrack.

Manages global tag definitions in tags.json (name and Catppuccin color)
as well as tag assignment, renaming, and deletion across recording sessions.
"""
import hashlib
import json
import logging
from pathlib import Path

from app.utils.app_paths import APP_DATA_DIR
from app.utils.atomic_io import atomic_write_json

logger = logging.getLogger(__name__)

# Curated Catppuccin Mocha colors for tags
TAG_PALETTE = [
    "#cba6f7",  # Mauve
    "#89b4fa",  # Blue
    "#74c7ec",  # Sapphire
    "#94e2d5",  # Teal
    "#a6e3a1",  # Green
    "#f9e2af",  # Yellow
    "#fab387",  # Peach
    "#f5c2e7",  # Pink
    "#b4befe",  # Lavender
    "#eba0ac",  # Maroon
    "#f38ba8",  # Red
    "#f5e0dc",  # Rosewater
]

DEFAULT_TAGS = [
    {"name": "Meeting", "color": "#89b4fa"},
    {"name": "1-on-1", "color": "#cba6f7"},
    {"name": "Interview", "color": "#a6e3a1"},
    {"name": "Standup", "color": "#94e2d5"},
    {"name": "Action Required", "color": "#fab387"},
]

DEFAULT_TAGS_FILE = APP_DATA_DIR / "tags.json"


def _get_tags_file(override_path=None) -> Path:
    return Path(override_path) if override_path else DEFAULT_TAGS_FILE


def _deterministic_color(name: str) -> str:
    """Generate a consistent Catppuccin color for a tag based on its name."""
    if not name:
        return TAG_PALETTE[0]
    idx = int(hashlib.md5(name.lower().encode("utf-8")).hexdigest(), 16) % len(TAG_PALETTE)
    return TAG_PALETTE[idx]


def load_all_tags(tags_file=None) -> list[dict]:
    """Load all registered tags from tags.json, initializing with defaults if missing."""
    file_path = _get_tags_file(tags_file)
    if not file_path.exists():
        save_all_tags(DEFAULT_TAGS, tags_file=file_path)
        return [dict(t) for t in DEFAULT_TAGS]

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            valid_tags = []
            seen_names = set()
            for item in data:
                if isinstance(item, dict) and "name" in item:
                    name = str(item["name"]).strip()
                    if name and name.lower() not in seen_names:
                        seen_names.add(name.lower())
                        color = item.get("color") or _deterministic_color(name)
                        valid_tags.append({"name": name, "color": color})
            return valid_tags
    except (OSError, json.JSONDecodeError):
        logger.exception("Failed to load tags from %s", file_path)

    return [dict(t) for t in DEFAULT_TAGS]


def save_all_tags(tags: list[dict], tags_file=None) -> bool:
    """Save tag list to tags.json."""
    file_path = _get_tags_file(tags_file)
    cleaned = []
    seen = set()
    for t in tags:
        if isinstance(t, dict) and "name" in t:
            name = str(t["name"]).strip()
            if name and name.lower() not in seen:
                seen.add(name.lower())
                color = t.get("color") or _deterministic_color(name)
                cleaned.append({"name": name, "color": color})

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(file_path, cleaned, indent=2, ensure_ascii=False)
        return True
    except OSError:
        logger.exception("Failed to save tags to %s", file_path)
        return False


def get_tag_color(name: str, tags_file=None) -> str:
    """Lookup the color for a tag by name, or compute a deterministic color."""
    tags = load_all_tags(tags_file=tags_file)
    name_lower = name.strip().lower()
    for t in tags:
        if t["name"].lower() == name_lower:
            return t["color"]
    return _deterministic_color(name)


def create_tag(name: str, color: str = None, tags_file=None) -> dict:
    """Create a new tag if not already existing. Returns the tag dict."""
    name = name.strip()
    if not name:
        raise ValueError("Tag name cannot be empty")

    tags = load_all_tags(tags_file=tags_file)
    for t in tags:
        if t["name"].lower() == name.lower():
            if color and t.get("color") != color:
                t["color"] = color
                save_all_tags(tags, tags_file=tags_file)
            return t

    new_tag = {"name": name, "color": color or _deterministic_color(name)}
    tags.append(new_tag)
    save_all_tags(tags, tags_file=tags_file)
    return new_tag


def rename_tag(old_name: str, new_name: str, recordings_dir=None, tags_file=None) -> bool:
    """Rename a tag globally in tags.json and across all recording sessions."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not old_name or not new_name:
        return False

    tags = load_all_tags(tags_file=tags_file)
    target_tag = None
    for t in tags:
        if t["name"].lower() == old_name.lower():
            target_tag = t
            break

    if not target_tag:
        return False

    target_tag["name"] = new_name
    save_all_tags(tags, tags_file=tags_file)

    # Update recordings
    if recordings_dir:
        rec_path = Path(recordings_dir)
        if rec_path.exists():
            for entry in rec_path.iterdir():
                if not entry.is_dir():
                    continue
                meta_file = entry / "metadata.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    curr_tags = meta.get("tags", [])
                    if isinstance(curr_tags, list) and old_name in curr_tags:
                        updated_tags = []
                        for t_name in curr_tags:
                            if t_name == old_name:
                                if new_name not in updated_tags:
                                    updated_tags.append(new_name)
                            elif t_name not in updated_tags:
                                updated_tags.append(t_name)
                        meta["tags"] = updated_tags
                        meta["directory"] = str(entry)
                        atomic_write_json(meta_file, meta, indent=2, ensure_ascii=False)
                        _maybe_refresh_markdown(meta)
                except Exception:
                    logger.exception("Failed to update renamed tag in %s", entry)

    return True


def delete_tag(name: str, recordings_dir=None, tags_file=None) -> bool:
    """Delete a tag globally and unassign it from all recording sessions."""
    name = name.strip()
    if not name:
        return False

    tags = load_all_tags(tags_file=tags_file)
    new_tags = [t for t in tags if t["name"].lower() != name.lower()]
    if len(new_tags) == len(tags):
        return False

    save_all_tags(new_tags, tags_file=tags_file)

    if recordings_dir:
        rec_path = Path(recordings_dir)
        if rec_path.exists():
            for entry in rec_path.iterdir():
                if not entry.is_dir():
                    continue
                meta_file = entry / "metadata.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                    curr_tags = meta.get("tags", [])
                    if isinstance(curr_tags, list) and name in curr_tags:
                        meta["tags"] = [t_name for t_name in curr_tags if t_name != name]
                        meta["directory"] = str(entry)
                        atomic_write_json(meta_file, meta, indent=2, ensure_ascii=False)
                        _maybe_refresh_markdown(meta)
                except Exception:
                    logger.exception("Failed to remove deleted tag from %s", entry)

    return True


def update_tag_color(name: str, new_color: str, tags_file=None) -> bool:
    """Update color for an existing tag."""
    name = name.strip()
    if not name or not new_color:
        return False
    tags = load_all_tags(tags_file=tags_file)
    for t in tags:
        if t["name"].lower() == name.lower():
            t["color"] = new_color
            return save_all_tags(tags, tags_file=tags_file)
    return False


def get_tag_counts(recordings_dir) -> dict[str, int]:
    """Calculate the number of recordings carrying each tag."""
    counts = {}
    rec_path = Path(recordings_dir) if recordings_dir else None
    if not rec_path or not rec_path.exists():
        return counts

    for entry in rec_path.iterdir():
        if not entry.is_dir():
            continue
        meta_file = entry / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            tags = meta.get("tags", [])
            if isinstance(tags, list):
                for t in tags:
                    counts[t] = counts.get(t, 0) + 1
        except Exception:
            continue
    return counts


def get_recording_tags(session_or_meta) -> list[str]:
    """Get the list of tags assigned to a recording."""
    if not session_or_meta:
        return []
    if isinstance(session_or_meta, (str, Path)):
        meta_file = Path(session_or_meta) / "metadata.json"
        if meta_file.exists():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                return [t for t in data.get("tags", []) if isinstance(t, str)]
            except Exception:
                return []
        return []
    elif isinstance(session_or_meta, dict):
        tags = session_or_meta.get("tags", [])
        return [t for t in tags if isinstance(t, str)]
    return []


def set_recording_tags(session_or_dir, tags: list[str], auto_register=True, tags_file=None) -> list[str]:
    """Set the tags list on a recording, persisting to metadata.json and updating transcript.md."""
    clean_tags = []
    seen = set()
    for t in tags:
        if isinstance(t, str):
            t_str = t.strip()
            if t_str and t_str not in seen:
                seen.add(t_str)
                clean_tags.append(t_str)

    if auto_register:
        for t_name in clean_tags:
            create_tag(t_name, tags_file=tags_file)

    session_dir = None
    meta = None
    if isinstance(session_or_dir, (str, Path)):
        session_dir = Path(session_or_dir)
        meta_file = session_dir / "metadata.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {}
    elif isinstance(session_or_dir, dict):
        meta = session_or_dir
        if "directory" in meta:
            session_dir = Path(meta["directory"])

    if meta is not None:
        meta["tags"] = clean_tags
        if session_dir:
            meta["directory"] = str(session_dir)
            meta_file = session_dir / "metadata.json"
            try:
                atomic_write_json(meta_file, meta, indent=2, ensure_ascii=False)
                _maybe_refresh_markdown(meta)
            except Exception:
                logger.exception("Failed to write updated tags to %s", meta_file)

    return clean_tags


def add_tag_to_recording(session_or_dir, tag_name: str, auto_register=True, tags_file=None) -> list[str]:
    """Add a tag to a recording."""
    tag_name = tag_name.strip()
    if not tag_name:
        return get_recording_tags(session_or_dir)
    current = get_recording_tags(session_or_dir)
    if tag_name not in current:
        current.append(tag_name)
        return set_recording_tags(session_or_dir, current, auto_register=auto_register, tags_file=tags_file)
    return current


def remove_tag_from_recording(session_or_dir, tag_name: str) -> list[str]:
    """Remove a tag from a recording."""
    tag_name = tag_name.strip()
    current = get_recording_tags(session_or_dir)
    if tag_name in current:
        current = [t for t in current if t != tag_name]
        return set_recording_tags(session_or_dir, current, auto_register=False)
    return current


def find_tags_for_recording_name(name: str, recordings_dir, exclude_dir=None) -> list[str]:
    """Find the most recent non-empty tag list from other recordings sharing the same name.

    Case-insensitive name comparison. Ignores empty names or self-directory (exclude_dir).
    """
    name_clean = name.strip().lower() if name else ""
    if not name_clean or not recordings_dir:
        return []

    rec_path = Path(recordings_dir)
    if not rec_path.exists():
        return []

    exclude_path = Path(exclude_dir).resolve() if exclude_dir else None
    candidates = []  # list of (mtime, tags)

    for entry in rec_path.iterdir():
        if not entry.is_dir():
            continue
        if exclude_path and entry.resolve() == exclude_path:
            continue
        meta_file = entry / "metadata.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            rec_name = str(meta.get("name") or "").strip().lower()
            if rec_name == name_clean:
                tags = meta.get("tags")
                if isinstance(tags, list) and tags:
                    clean_tags = [t for t in tags if isinstance(t, str) and t.strip()]
                    if clean_tags:
                        mtime = entry.stat().st_mtime
                        candidates.append((mtime, clean_tags))
        except Exception:
            continue

    if not candidates:
        return []

    # Pick the most recently modified matching recording's tags
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def _maybe_refresh_markdown(metadata):
    """Best-effort export of transcript.md with updated tags."""
    try:
        from app.utils.session_io import export_session_markdown
        export_session_markdown(metadata)
    except Exception:
        pass
