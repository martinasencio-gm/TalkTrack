# TalkTrack - CLAUDE.md

## Project Rules

Topic-specific rules are in `.claude/rules/`. `ways-of-working.md` is always loaded; the others are loaded on demand by topic.

@./.claude/rules/ways-of-working.md

- [audio-pipeline.md](.claude/rules/audio-pipeline.md) — AudioStream callback order, mute/gain scoping, MainWindow→capture access pattern.
- [per-app-audio-capture.md](.claude/rules/per-app-audio-capture.md) — Windows 11 process-loopback COM invariants (IAgileObject, device path, ctypes arg passing, generator caching gotcha).
- [ui-patterns.md](.claude/rules/ui-patterns.md) — CollapsibleSection, left-panel conventions, DAW meter fill direction, peak-sample bar semantics, Qt QSS gotchas, Catppuccin palette.
- [packaging-and-launch.md](.claude/rules/packaging-and-launch.md) — uv-first `.venv` install, CPU/CUDA torch extras, dep version caps, taskbar-icon-via-Start-Menu-shortcut (no exe), uv-on-Windows cache gotcha.
- [transcription-pipeline.md](.claude/rules/transcription-pipeline.md) — worker session binding, serial job queue, shutdown handling, model caches, SimpleDiarizer invariants.
- [ai-providers.md](.claude/rules/ai-providers.md) — provider config keys, context limits, per-SDK timeout conventions, shared embed cache, error surfacing.

## Project Overview

TalkTrack is a Windows desktop application that records, transcribes, and diarizes audio from calls (Teams, Zoom, etc.). It is a modern clone of Evaer for Teams with AI-powered transcription and speaker identification.

## Tech Stack

- **GUI:** PyQt6
- **Audio Capture:** sounddevice + WASAPI, comtypes (Win11 per-process capture)
- **Audio Session Enumeration:** pycaw (Windows Core Audio API)
- **Transcription:** faster-whisper (local OpenAI Whisper, no internet needed)
- **Speaker Diarization:** pyannote.audio 4.0 (requires free HuggingFace token)
- **Deep Learning:** torch, torchaudio
- **Audio Processing:** scipy, pydub, soundfile, numpy
- **Process Detection:** psutil (for known audio app enumeration)
- **NLP/Embeddings:** transformers, sentence-transformers (pyannote dependencies)
- **Windows Integration:** pywin32, comtypes

## Project Structure

```
TalkTrack/
  main.py                              # Entry point, QApplication setup
  batch_transcribe.py                  # Headless batch CLI entry point (Task Scheduler)
  start.bat                            # Launcher (uv-first, .venv isolation, falls back to pip)
  start_debug.bat                      # Debug launcher with console output
  requirements.txt                     # Dependencies
  app/
    main_window.py                     # Main window + orchestration
    batch/
      __init__.py                      # Package init
      runner.py                        # Batch run loop: args, worklist, reporting, exit codes
      pipeline.py                      # One recording through transcribe (+diarize), headless
      worklist.py                      # Which queued recordings to process, oldest first
      cutoff.py                        # --until wall-clock cutoff parsing and checking
      logging_setup.py                 # Per-run log under Documents/TalkTrack/batch Log
    audio/
      __init__.py                      # Package init
      segment_player.py               # Audio clip playback for transcript segments
    integrations/
      __init__.py                      # Package init
      outlook_calendar.py              # Read-only Outlook desktop calendar lookup (COM)
    recording/
      audio_capture.py                 # AudioStream, DualAudioCapture (legacy + per-app modes)
      chunk_writer.py                  # ChunkWriter: streams capture audio to disk (#32)
      process_audio_capture.py         # ProcessCaptureStream, ProcessAudioCapture (Win11 per-PID)
      recorder.py                      # State machine, session management
      import_session.py                # Pure metadata builder for imported recordings
    transcription/
      transcriber.py                   # Whisper worker + dataclasses (single file or per-track)
      track_merge.py                   # Merge per-track transcripts into one timeline, drop mic bleed
      diarizer.py                      # Speaker diarization (pyannote)
    ai/
      __init__.py                      # Package init
      provider.py                      # AIProvider base class
      claude_provider.py               # Claude API implementation
      openai_provider.py               # OpenAI API implementation
      grok_provider.py                 # Grok (xAI) via OpenAI-compatible API
      gemini_provider.py               # Google Gemini API implementation
      mistral_provider.py              # Mistral AI API implementation
      local_provider.py                # Local model (llama-cpp-python)
      provider_factory.py              # Factory for configured provider
      summarizer.py                    # Meeting summary + action items
      search_index.py                  # Transcript search + embeddings
      chat.py                          # Chat context builder
    ui/
      source_selector.py              # Mic dropdown + per-app picker (Win11) or legacy loopback (Win10)
      recording_controls.py           # Record/Pause/Stop buttons + timer
      recording_header.py             # Recording info display with rename
      calendar_banner.py              # Calendar-match suggestion banner
      calendar_lookup_worker.py       # Off-thread Outlook calendar lookup
      import_timestamp_dialog.py      # Confirm/edit an imported recording's start time
      segment_widget.py               # Interactive transcript segment row
      settings_dialog.py              # Settings dialog with tabs
      speaker_name_panel.py           # Collapsible speaker name mapping panel
      status_panel.py                 # System status dialog (dependency health checks)
      transcript_viewer.py            # Display + export transcripts (with interactive segments)
      notes_panel.py                  # Call notes with timestamps
      recordings_list.py              # Past recordings browser
      level_meter.py                   # Real-time audio level meters
      waveform_display.py             # Live waveform visualization
      transcript_search_bar.py        # Find/replace for transcripts
      search_bar.py                    # Recordings search bar
      summary_panel.py                 # AI meeting summary display
      action_items_panel.py            # AI action items display
      chat_panel.py                    # Chat with transcript panel
      about_dialog.py                  # About dialog with donation link
    utils/
      audio_devices.py                # Device enumeration (sounddevice)
      batch_queue.py                  # batch_pending/batch_attempts tag in metadata.json
      audio_session_monitor.py        # Per-app audio session enumeration (pycaw)
      com_session_worker.py           # Isolated worker process for pycaw/comtypes COM polling
      render_activity.py              # Which output endpoint is actually rendering (auto-picks the loopback source)
      config.py                       # JSON config management
      session_io.py                   # Disk-driven transcript/calendar/speaker-name I/O for a session (Qt-free; shared by MainWindow and the batch CLI)
      dependency_checker.py           # System health checks for status panel
      platform_info.py                # Windows version detection
      transcript_export.py            # Pure Markdown builder for LLM-ready transcript.md (written into the recording's own folder)
      transcripts_migration.py        # One-time import of exports stranded in the old separate transcripts/ folder into their session folder
  tests/
    test_platform_info.py             # Windows version detection tests
    test_audio_session_monitor.py     # Audio session enumeration tests
    test_process_audio_capture.py     # Mixer and capture stream tests
    test_chunk_writer.py              # Streaming disk writer tests
    test_dual_audio_capture.py        # Per-app mode integration tests
    test_dependency_checker.py        # Dependency checker tests
    test_audio_devices.py             # Device enumeration + default-output name matching tests
    test_render_activity.py           # Render-endpoint activity tracking tests
    test_com_poller_render_activity.py # Poller render-peak history tests
    test_config.py                    # Config load/save tests (incl. calendar defaults)
    test_transcriber.py               # TranscriptSegment/TranscriptResult tests
    test_transcriber_multitrack.py    # Per-track transcription worker tests
    test_track_merge.py               # Track merge / bleed dedup / dual-track plan tests
    test_segment_player.py            # Audio clip playback tests
    test_recording_header.py          # RecordingHeader helper tests (incl. calendar line formatting)
    test_speaker_name_panel.py        # SpeakerNamePanel helper tests (incl. attendee dropdown mutual exclusion)
    test_outlook_calendar.py          # Outlook calendar overlap-matching tests (COM mocked)
    test_import_session.py            # Import metadata builder tests
    test_segment_widget.py            # SegmentWidget helper tests
    test_level_meter.py                # Audio level meter tests
    test_waveform_display.py           # Waveform ring buffer tests
    test_edit_history.py               # Undo/redo history tests
    test_transcript_search_bar.py      # Find/replace logic tests
    test_ai_provider.py                # AI provider factory tests
    test_summarizer.py                 # Summary prompt builder tests
    test_search_index.py               # Transcript search tests
    test_chat.py                       # Chat context builder tests
    test_transcript_export.py          # LLM-ready transcript Markdown export tests
    test_batch_queue.py                # batch_pending/batch_attempts tag tests
    test_batch_cutoff.py               # --until parsing and cutoff checking tests
    test_batch_worklist.py             # Batch worklist selection tests
    test_batch_pipeline.py             # Per-recording batch pipeline tests (workers faked)
    test_batch_runner.py               # Batch run loop, tag bookkeeping, exit codes
    test_session_io.py                 # Session file reader/writer tests
    test_recordings_list_batch.py      # Batch-queue menu helper tests
  resources/
    style.qss                          # Dark theme stylesheet (Catppuccin Mocha)
    talktrack.ico                      # App icon (multi-size: 16-256px)
    build_ico.py                       # Rebuild .ico from source PNGs
    generate_icon.py                   # Old programmatic icon generator (deprecated)
    favicon.ico                        # Favicon for web use
    TT_icon_*.png                      # Icon source files (32, 64, 128, 256, 512px)
    TT_logo_*.png                      # Logo files (655x200, 1300x400)
  docs/batch-transcription.md         # Batch CLI usage + Task Scheduler setup
  docs/plans/                         # Design docs and implementation plans
  recordings/                         # Output directory (each session folder also holds its transcript.md export)
```

## Current Features

- Record / Pause / Resume / Stop controls with live timer
- **Per-app audio capture (Win11):** select specific apps (Teams, Chrome, etc.) to record
- **Legacy system audio capture:** WASAPI loopback for all system audio (Win10 or fallback)
- Dual audio capture: microphone (your voice) + system/app audio
- **Dual microphone support:** optionally use 2 mics simultaneously (e.g., desk mic + headset), mixed into one track
- Auto-transcribes after recording stops using Faster-Whisper
- Speaker diarization with two modes:
  - Simple mode (no setup): transcribes `mic_audio.wav` and `system_audio.wav` separately and labels each segment "You" or "Remote" by the track it came from, merging the two into one timeline (bleed duplicates dropped). Falls back to the RMS-comparison `SimpleDiarizer` when only the mixed audio exists.
  - Full diarization (pyannote.audio): identifies individual speakers
- **Per-run diarization choice:** an "Identify speakers" checkbox in the transcript header decides whether the *next* transcription runs pyannote (it's the slowest stage — often longer than the recording itself on CPU). It mirrors and writes `diarization.enabled`, and is disabled without a HuggingFace token.
- **On-demand diarization:** an "Identify Speakers" button re-runs pyannote over the transcript already on screen, so a fast unlabelled pass can be upgraded without transcribing again
- **System Status Panel:** startup dependency health check (Help > System Status)
- **Interactive transcript viewer:** per-segment audio playback, inline text editing, speaker name mapping
- **Speaker naming:** assign friendly names to diarized speakers, saved per recording
- **Calendar tagging (opt-in):** after a recording finishes, optionally checks the local Outlook desktop calendar for an overlapping event and offers to tag the recording with its subject, organizer, and attendees (`calendar_event.json`); attendee names populate a mutually-exclusive dropdown in speaker naming
- **Calendar rename suggestion:** when a recording is tagged to a calendar event and has no custom name yet, offers to rename it to the event's subject
- **Calendar remap:** "Change" button in the recording header re-runs the calendar lookup on demand and lets the user retag the recording to a different matching event
- **Rename suggests meetings:** starting a rename looks up the calendar events overlapping the recording and offers their subjects as completions; picking one renames *and* tags/retags the recording to that meeting (a freely-typed name only renames)
- **LLM-ready transcript export:** every transcript/notes/summary save also writes `transcript.md` (frontmatter + summary + action items + notes + transcript) into the recording's own session folder, alongside `transcript.json`
- **Recording header:** shows loaded recording info (name, date, duration, speakers) with rename
- Color-coded transcript with speaker labels and timestamps
- Export transcript to TXT, SRT (subtitles), or JSON with speaker names
- Call notes with timestamp insertion
- Browse and replay past recordings (with friendly names)
- **Recording import:** import an existing audio file (wav/mp3/m4a) as a new session via Recordings > Import..., running it through the same transcribe/diarize pipeline as a live recording
- Settings for model size, sample rate, output format (WAV/MP3)
- Dark theme UI (Catppuccin Mocha palette)
- **Audio level meters:** real-time VU meters for mic and system audio during recording
- **Live waveform:** scrolling waveform visualization during recording
- **Transcript find/replace:** Ctrl+F search across all segments with regex support
- **Transcript undo/redo:** per-segment edit history with context menu
- **AI meeting summaries:** auto-generated after transcription (configurable provider), manual generate/regenerate
- **AI action items:** extracted tasks with assignees and deadlines, manual generate/regenerate
- **Notes in AI context:** user call notes included in AI summary and action item prompts
- **Per-provider AI settings:** API keys and models stored separately per provider with status indicator
- **Searchable history:** text and semantic search across all past recordings
- **Chat with transcript:** ask AI questions about the current recording
- **AI provider choice:** Claude, OpenAI, Grok, Gemini, Mistral, or local models via Settings > AI Assistant
- **Hidden devices filter:** hide unwanted audio devices (e.g., Voicemeeter) from dropdowns
- **Capture settings persistence:** remembers capture mode (per-app vs legacy) and selected apps
- **Min duration filter:** skip auto-transcription for short recordings (configurable in Settings)
- **Multi-select bulk delete:** select multiple recordings and delete at once (Ctrl/Shift+click)
- **Delete scopes:** deleting a recording offers three scopes — recording audio only (removes just the audio tracks, keeps `transcript.json`/`transcript.md`/summary/action items/notes/etc.; the session survives as a transcript-only entry), transcriptions only (removes `transcript.json`, `transcript.md`, summary, and action items, keeps audio), or everything. Either partial scope collapses to removing the whole session folder if it would otherwise leave neither audio nor a transcript behind.
- **Transcribed indicator:** the "Transcribed" pill in the recordings list and the right-click Transcribe/Export actions both key off `transcript.json` inside the recording's own folder
- **Custom app icon:** Start Menu shortcut (offered on first run) targets the venv interpreter and carries the icon + AppUserModelID for a correct taskbar icon
- **About dialog:** version info and Buy Me a Coffee donation link
- **Silence auto-stop:** monitors system/app audio (not mic) for sustained silence, auto-stops recording after configurable duration (Settings > General)
- **Continue from here:** checkbox next to Play All — click any segment to start continuous playback from that point
- **Smart scroll during playback:** auto-scroll to playing segment is suppressed if user has manually scrolled away
- **Speaker-bleed warning:** when per-track transcription drops enough duplicate segments to show the mic is hearing the call audio, warns once per app session and suggests headphones
- **Silent-capture warning:** per-app recordings that receive zero audio for 15s trigger a one-shot warning (Teams/Zoom opt out of process-loopback; suggests legacy mode)
- **Recovered recordings:** crash-orphaned recording dirs (audio but no metadata) are salvaged on startup as "Recovered" entries — never auto-deleted
- **Transcription queue:** back-to-back recordings queue for transcription instead of being dropped; jobs run serially with the session bound at start
- **Batch transcription (companion CLI):** `batch_transcribe.py --until HH:MM` transcribes (and optionally diarizes) every recording tagged for batch processing, then stops before starting one past the cutoff. Meant for Windows Task Scheduler. Recordings are tagged from the recordings list context menu (a peach "Queued" pill marks them), and anything the app declines to transcribe itself is tagged automatically when `general.batch_auto_queue` is on. See [docs/batch-transcription.md](docs/batch-transcription.md).
- **Notes autosave:** switching recordings saves the previous recording's notes before loading the new ones

## Architecture Notes

### Audio Capture (Two Modes)

**Per-App Mode (Win11 only):**
- `ProcessCaptureStream`: Captures audio from a single process by PID using Win11 `ActivateAudioInterfaceAsync` COM API
- `ProcessAudioCapture`: Manages multiple ProcessCaptureStreams, mixes output in real-time
- PIDs are locked at `start()` — no live add/remove during a recording
- Uses `PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE` to capture process + children

**Legacy Mode (Win10/fallback):**
- `AudioStream`: Single-device capture wrapper around sounddevice
- WASAPI loopback captures all system audio

**Common:**
- `DualAudioCapture`: Orchestrates mic + system audio, accepts either mode via `capture_mode` param
- Output files per recording: mic_audio.wav, system_audio.wav, combined_audio.wav

### Audio Session Monitoring
- `audio_session_monitor.py` uses pycaw + psutil to enumerate audio apps
- Two sources: pycaw (apps with active audio sessions) + psutil (known audio apps like Teams/Zoom even when not in a call)
- Groups by display name (deduplicates multi-process apps like Zoom)
- Returns `{"pids": [int], "name": str, "process_name": str, "active": bool}`
- All pycaw/comtypes COM calls (this module's `get_active_audio_apps()` and
  `meeting_signals.get_mic_capture_pids()`) run inside a separate OS process owned by
  `com_session_worker.ComSessionPoller` — comtypes' COM proxy finalization can crash the
  whole process natively (confirmed via production Windows Event Log correlation), so
  isolating it means only the worker dies; `ComSessionPoller.get_snapshot()` detects and
  silently respawns it.
- Auto-refreshes every 5 seconds in the UI (2 seconds while recording), read from the
  poller's cached snapshot rather than calling pycaw directly
- The same worker samples per-endpoint render peaks (`render_activity.sample_render_peaks`,
  **render endpoints only** — capture endpoints expose meters too and the user's own mic
  would outrank the speakers). `ComSessionPoller` folds each new snapshot into a 45s
  activity history and exposes `active_output_index(outputs)`, which `SourceSelector`
  consults only when no `last_loopback` is saved.

### Recording Pipeline
- State machine: IDLE -> RECORDING -> PAUSED <-> RECORDING -> STOPPING -> IDLE
- Each recording gets timestamped directory with audio files + metadata.json
- Metadata includes capture_mode and app_pids
- Transcription/diarization runs in separate QThread workers

### System Status Panel
- `DependencyChecker` runs health checks: mic, WASAPI, GPU/CUDA, Whisper model, HF token, pyannote, FFmpeg, Windows version
- GPU check detects NVIDIA GPU via torch or nvidia-smi, warns if CUDA PyTorch not installed
- `SystemStatusDialog` shows results with actionable fix suggestions
- Auto-shows on startup if critical checks fail
- Accessible via Help > System Status
- Settings dialog shows inline GPU status when CUDA is selected as compute device

### Transcript Enhancement Suite
- `SegmentWidget`: Interactive row per transcript segment — play button, timestamp, speaker label (clickable), editable text, edit indicator
- `SpeakerNamePanel`: Collapsible panel mapping speaker IDs (e.g., SPEAKER_00) to friendly names, with color swatches
- `RecordingHeader`: Shows loaded recording info with inline rename capability
- `SegmentPlayer`: Plays audio clips for individual segments using sounddevice, caches loaded audio
- Speaker names stored per recording in `speaker_names.json`, separate from `transcript.json`
- `TranscriptSegment.original_text` tracks pre-edit text for undo support
- Signal flow: SegmentWidget → TranscriptViewer → MainWindow (saves to disk)

### AI Provider System
- Pluggable provider abstraction: `AIProvider` base class with `complete()` and `embed()` methods
- Six implementations: Claude, OpenAI, Grok (xAI), Gemini (Google), Mistral, Local (llama-cpp-python)
- Factory pattern via `create_provider(config)` — returns configured provider or None
- AI SDK packages installed on-demand when user selects a provider (not bundled in requirements.txt)
- Ad-hoc installer prompts user before installing, shows progress in settings dialog
- All providers run in QThread workers to avoid UI blocking
- Per-provider settings: API keys and models stored in `provider_settings` dict, keyed by provider name
- API key status indicator in settings shows "API key configured (first4...last4)" or "No API key set"
- Settings tab for provider selection, API keys, and model configuration
- Auto-summarize after transcription (disableable in settings), with manual generate/regenerate buttons
- User call notes included in summary and action item AI prompts via `build_summary_prompt(segments, speaker_names, notes="")`
- Chat history persisted per recording as `chat_history.json`
- Search index uses text matching (no AI needed) or semantic embeddings

### Configuration
- Stored at ~/.talktrack/settings.json
- Audio settings: sample_rate, channels, capture_mode ("legacy" or "per_app"), selected_apps, hidden_devices, mic_count (1 or 2)
- Device selections persist by **name** (indices shift as hardware comes and goes): `last_mic`, `last_mic2`, `last_loopback`. System-audio selection priority is **saved choice → endpoint actually rendering audio → `get_default_output()` → first device**. The Windows default output is frequently not the endpoint the meeting app renders to, and capturing the wrong one yields a silent track that `ChunkWriter` then deletes. Capture mode and selected apps are persisted too
- Transcription settings: model size (tiny/base/small/medium/large-v3), language, compute device, min_duration
- AI settings: provider (none/claude/openai/grok/gemini/mistral/local), provider_settings (per-provider api_key/model), auto_summarize
- General settings: min_recording_length, silence_auto_stop, silence_duration, `batch_auto_queue` (tag recordings the app doesn't transcribe itself for the batch run)
- Meeting detection settings (`meeting_detection`): mode ("off"/"suggest"/"auto"), threshold_seconds, detect_end, end_grace_seconds, end_action ("stop"/"pause"), use_mic_capture, use_calendar, use_window_title, apps. Replaces the old `general.auto_record` flag, which `app/utils/config_migration.py` migrates into `mode` ("auto" or "off") on first load after upgrade — `silence_auto_stop` is unaffected and still applies as an independent backstop.
- Output settings: `output.directory` (recordings output folder — `transcript.md` lives inside each session folder here, not a separate directory)
- `transcripts.session_import_done`: one-time flag; once set, `app/utils/transcripts_migration.py` (called from `MainWindow.__init__`) skips re-scanning for exports stranded in the old separate transcripts/ folder (removed) that predate this per-session layout

### Data Files Per Recording
- transcript.json: transcription/diarization source of truth
- transcript.md: LLM-ready Markdown export (frontmatter + summary + action items + notes + transcript), regenerated alongside transcript.json on every save
- summary.md: AI-generated meeting summary
- action_items.json: Extracted action items with assignees
- chat_history.json: Chat conversation history
- embeddings.npz: Cached segment embeddings for semantic search (auto-invalidated on edit)
- metadata.json: `batch_pending` (queued for the batch run) and `batch_attempts` (failed batch attempts; 3 retires it) are optional keys — absent means not queued

## Setup Instructions

### Basic Setup

Preferred: run `start.bat` (uv-first, creates an isolated `.venv`, never touches global Python; see [packaging-and-launch.md](.claude/rules/packaging-and-launch.md)). Plain-pip path still works:

```bash
pip install -r requirements.txt
python main.py
```

### For Full Speaker Diarization (Optional)
1. Get a free HuggingFace token at https://huggingface.co/settings/tokens
2. Accept the pyannote model terms at:
   - https://huggingface.co/pyannote/speaker-diarization-community-1
3. Enter your token in Settings > Transcription > HuggingFace Token

### Usage During a Teams Call
Start a Teams meeting normally, then click Record in TalkTrack.
- **Windows 11:** Select "Microsoft Teams" in the app picker to capture only Teams audio
- **Windows 10:** Captures all system audio via WASAPI loopback

## Running Tests

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

The venv interpreter, not the global `python` — the global install has no
pytest. Never bare `uv run` (it syncs first and can pull CPU torch over the
CUDA build); pass `--no-sync` if uv is required.

## Coding Conventions

- Python with PyQt6 for all UI
- QThread workers for background processing (transcription, diarization)
- Signals/slots for inter-component communication
- Config stored as JSON, loaded via config.py utility
- Durable file writes (transcript, metadata, notes, config) go through `app/utils/atomic_io.py` (temp file + `os.replace`) — never bare `open(w)` for user data
- Dark theme by default (Catppuccin Mocha palette)
- All audio processing uses numpy arrays at 16000 Hz sample rate (speech-optimized)
- Tests use unittest with mock for hardware-dependent code

## Bash Command Style

When running shell commands:
- Avoid complex chained commands with `&&`, `||`, or pipes when possible
- Run simple, single-purpose commands sequentially instead

## Platform Workarounds

- **PyQt6 + PyTorch DLL conflict:** QApplication modifies Windows DLL search order, breaking torch's c10.dll loading in QThreads. Fixed by calling `os.add_dll_directory(torch/lib)` before QApplication init (in main.py).
- **torchcodec not available on Windows:** pyannote.audio 4.0 uses torchcodec for audio decoding, which requires FFmpeg DLLs. Workaround: pre-load audio via soundfile and pass as `{"waveform": tensor, "sample_rate": int}` dict to pyannote pipeline.
- **torchcodec warning suppression:** `warnings.filterwarnings("ignore", module=r"pyannote\.audio\.core\.io")` in main.py.
- **PyQt6 QListWidget truthiness:** Empty QListWidget evaluates as falsy in PyQt6. Always use `is None` / `is not None` checks, never `if widget:` / `if not widget:`.
- **WASAPI device index mismatch:** `sd.default.device[1]` returns DirectSound/MME index that doesn't match WASAPI device indices. `get_default_output()` matches by device name instead of index.
- **Microsoft Store Python AppUserModelID / taskbar icon:** Store-packaged Python's `pythonw` is an execution alias that overrides `SetCurrentProcessExplicitAppUserModelID` and can't be a shortcut target, so the taskbar shows the generic Python icon. Workaround: the app offers (on first run, or Help > Add to Start Menu via `app/utils/start_menu.py`) a Start Menu shortcut that targets the **venv** `pythonw` (`.venv\Scripts\pythonw.exe`, a real binary) and carries `talktrack.ico` + the `TalkTrack.TalkTrack.1` AppUserModelID. main.py sets the matching per-window AppUserModelID, so Windows resolves the shortcut's icon onto the taskbar. No PyInstaller/exe build needed.

## Known Limitations

- **Windows only:** Uses WASAPI and Windows COM APIs
- **Per-app capture requires Windows 11 Build 22000+**

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
