# Move App Data Directory to Documents

## Goal

Move TalkTrack's app-data folder — `settings.json`, `talktrack.log` (+ rotated
backups), the single-instance lock file, and `settings.json.bak` — from
`%USERPROFILE%\.talktrack` to `%USERPROFILE%\Documents\TalkTrack`, migrating
existing users' data automatically on first launch after the update.

## Background

Two places currently compute the app-data directory independently, as the
same literal:

- `app/utils/config.py:86` — `CONFIG_DIR = Path.home() / ".talktrack"`
- `main.py:24` — `LOG_DIR = Path.home() / ".talktrack"`

Both point at the same folder today by coincidence of being the same
expression, not because either references the other. `main.py` also passes
`LOG_DIR` to `SingleInstanceGuard`, which keeps its lock file there too.

Recordings and transcripts have their own independently configurable
directories (`output.directory`, `transcripts.directory`) and are unaffected
by this change.

## Design

### `app/utils/app_paths.py` (new)

A single module, imported by both `config.py` and `main.py`, that resolves
the app-data directory once at import time and performs a one-time migration
if needed:

```python
_LEGACY_DIR = Path.home() / ".talktrack"
APP_DATA_DIR = Path.home() / "Documents" / "TalkTrack"
```

Resolution runs at import time, not inside a function, so both call sites
see the same already-resolved path with no extra calls to remember to make:

1. If `APP_DATA_DIR` already exists, use it as-is (fresh install already at
   the right place, or migration already ran on a prior launch).
2. Else, if `_LEGACY_DIR` exists, attempt `shutil.move(_LEGACY_DIR,
   APP_DATA_DIR)` (creating `Documents` first if needed). One whole-directory
   move — nothing else ever writes into this folder, so there is no
   per-file merge case to handle.
3. If the move raises `OSError` (permissions, a cross-volume edge case),
   `APP_DATA_DIR` is reassigned to `_LEGACY_DIR` so the app keeps working
   from the old location rather than losing settings or splitting log/config
   across two folders.
4. If neither directory exists (brand-new install), `APP_DATA_DIR` stays at
   the Documents target; the existing `mkdir(parents=True, exist_ok=True)`
   calls in `config.py`/`main.py` create it on first write, unchanged from
   today's behavior.

### Call site changes

- `app/utils/config.py`: `CONFIG_DIR = Path.home() / ".talktrack"` becomes
  `from app.utils.app_paths import APP_DATA_DIR as CONFIG_DIR`.
- `main.py`: `LOG_DIR = Path.home() / ".talktrack"` becomes the equivalent
  import. `SingleInstanceGuard(LOG_DIR)` is unchanged — it already receives
  whatever directory is passed in.

No other code references `.talktrack` directly (confirmed by repo-wide
search), so these are the only two call sites.

## Testing

One test module, `tests/test_app_paths.py`, patching `Path.home()` to a
pytest `tmp_path`:

1. **Fresh install:** neither directory exists → `APP_DATA_DIR` resolves to
   the Documents target, nothing is moved.
2. **Migration:** legacy dir exists with a marker file → after import,
   the marker file is found at the new location and the legacy dir is gone.
3. **Already migrated:** both a populated new dir and a legacy dir exist →
   the new dir's contents are used unchanged; the legacy dir is left alone
   (not merged, not deleted) since it holds whatever the user did with it
   after a previous successful migration.
4. **Migration failure:** `shutil.move` mocked to raise `OSError` → resolved
   path falls back to the legacy dir, and it still contains the marker file
   (nothing lost).

Each case reloads `app_paths` fresh (`importlib.reload`) since resolution
happens at import time.

## Risks

- **Two machines / roaming profile sharing `~` via different mechanisms:**
  out of scope — same limitation exists today with `Path.home()/.talktrack`.
- **Documents redirected to OneDrive:** `Path.home() / "Documents"` resolves
  through whatever junction/redirection Windows has in place, same as any
  other app targeting the Documents folder; no special handling needed.
