# Dynamics 365 CRM Export — Design

Date: 2026-08-23

## Problem

TalkTrack has no way to get a recording's transcript, AI summary, action items, or
call notes into Microsoft Dynamics 365 (Dataverse). Users currently copy content out
manually. This adds an opt-in "Export to Dynamics CRM" feature that attaches selected
content as a Note on an existing Dynamics record (Account, Contact, or Opportunity).

## Non-goals

- Two-way sync (reading from Dynamics back into TalkTrack) — export only.
- Creating new Dynamics records (Contacts/Accounts/Opportunities) — always attaches
  to an existing record the user already has in Dynamics.
- Bulk export of multiple recordings at once — v1 is single-recording only.
- Batch CLI (`app/batch/`) integration — this is an interactive, user-driven action,
  not part of the headless pipeline.

## 1. Architecture overview

A new `app/integrations/dynamics_crm.py` module, parallel in spirit to
`app/integrations/outlook_calendar.py` but HTTP-based instead of COM-based: it wraps
MSAL auth and Dataverse Web API calls (record search, note creation) behind plain,
unit-testable functions. Like `outlook_calendar.py`, it is best-effort — any failure
degrades to "not connected" / "no matches" rather than crashing the app, since the
whole feature is opt-in.

A new `DynamicsExportWorker` (QThread) runs the network calls off the UI thread — the
same pattern already used for AI provider calls. A new `DynamicsExportDialog` handles
record search/pick and section selection, opened from both the transcript viewer and
the recordings list context menu.

Auth uses MSAL's public-client interactive/device-code flow (delegated user OAuth).
This requires an Azure AD app registration (public client, Dataverse delegated
permission) created ahead of time as a setup prerequisite; its Client ID and the
Dynamics org URL are entered in Settings. The resulting token cache is DPAPI-encrypted
via `win32crypt` (already a dependency through pywin32) and stored at
`~/.talktrack/dynamics_token_cache.bin`, separate from `settings.json` — a stronger
bar than the plaintext convention used for AI provider API keys today, because this
credential can act on the user's behalf in Dynamics, not just call a completion API.

## 2. Record matching

Dynamics record suggestions come from two independent signals, merged in the export
dialog:

- **Tag mapping (new).** `tags.json` entries (managed via `app/utils/tag_manager.py`
  and `app/ui/tag_manager_dialog.py`) gain an optional `crm_link` field:
  `{"entity": "account"|"contact"|"opportunity", "id": "<guid>", "display_name": str}`.
  Set via a new "Link to CRM record..." action in the existing Tag Manager dialog,
  which searches Dataverse and stores the picked record against the tag. Any
  recording carrying that tag then suggests that record — this reuses the tagging
  users already do per-client/account, at the Account/Opportunity level rather than
  per-attendee.
- **Calendar attendee match (existing pattern, reused).** Attendee emails already
  captured in a recording's `calendar_event.json` (from the existing opt-in calendar
  tagging feature) are looked up against Dynamics Contacts by email.

**Precedence:** tag-mapped records are shown first in the dialog and pre-selected
when there is exactly one; calendar matches appear as secondary suggestions. If a
recording carries multiple tags that each have a `crm_link`, **all** of their mapped
records are listed as suggestion chips — none is auto-picked, the user must click
one (never silently guess between two explicit, user-curated mappings). A manual
search box is always present as the fallback for anything unmatched or wrong.

**Implementation note:** `tag_manager.load_all_tags` / `save_all_tags` currently only
round-trip the `name` and `color` keys of each tag dict, silently dropping any others.
This must change to preserve `crm_link` through load/save/create/rename/delete.

## 3. Export dialog and data flow

**`DynamicsExportDialog`** (new, `app/ui/dynamics_export_dialog.py`) is opened from:
- a new "Export CRM" button next to `Export TXT` / `Export SRT` in the transcript
  viewer (`app/ui/transcript_viewer.py`), or
- a new "Export to Dynamics CRM..." action in the recordings list context menu
  (`app/ui/recordings_list.py`), hidden/disabled when more than one recording is
  selected (single-recording only per Non-goals).

Both entry points construct the dialog from the same inputs: the recording's
directory, its tags (for tag-mapping suggestions), and its `calendar_event.json` if
present (for the attendee-match suggestion). The action/button itself is hidden
entirely when `dynamics_crm.enabled` is false in config.

Dialog layout:

1. **Record picker.** Suggestion chips first (tag-mapped records, then the
   calendar-attendee match), each showing an entity-type badge (Account/Contact/
   Opportunity) plus display name; clicking one selects it. A search box below
   queries Dataverse by name/email across all three entity types for anything
   unmatched. The Export button stays disabled until a record is selected.
2. **Section checkboxes.** Transcript / Summary / Action Items / Notes, default
   all-checked. A section with no content for this recording is greyed out and
   unchecked, mirroring the `has_exportable_content` gating already used for the
   TXT/SRT/JSON exports.
3. **Subject field.** Pre-filled from the recording's name (or the calendar event's
   subject when tag/calendar-matched), editable.
4. **Export button.** Assembles the note body from the checked sections via a new,
   smaller builder function alongside `app/utils/transcript_export.py`'s
   `build_export_markdown` (this needs an arbitrary subset of sections, not the
   fixed frontmatter+everything shape that function produces), then hands it to the
   worker.

**`DynamicsExportWorker`** (QThread, same pattern as the existing AI provider
workers): resolves an access token — silently from the DPAPI-encrypted cache first,
falling back to interactive/device-code auth (safe to block synchronously since this
runs off the UI thread) — then `POST`s a Dataverse `annotation` with `objectid` /
`objecttypecode` set to the picked record, `subject`, and the assembled `notetext`.
Emits success (carrying a deep link back into the Dynamics UI for that note) or
failure back to the dialog.

## 4. Settings, config, and error handling

**New Settings tab: "Dynamics CRM".** Fields: Org URL, Azure AD Client ID (plain
config, same convention as other integration settings). A "Connect" button runs the
initial interactive OAuth and shows connection status — mirrors the existing
per-provider AI status indicator ("Connected as user@org.com" / "Not connected"). A
"Disconnect" button deletes the token cache file.

**Config additions** (`app/utils/config.py` `DEFAULT_CONFIG`):

```python
"dynamics_crm": {
    "org_url": "",
    "client_id": "",
    "enabled": False,
}
```

`enabled` gates whether the Export CRM button/menu action appears at all — the same
on/off pattern used for other optional integrations (calendar tagging, AI providers),
so users who don't use Dynamics see no new UI.

**Token cache.** `~/.talktrack/dynamics_token_cache.bin`, DPAPI-encrypted via
`win32crypt.CryptProtectData` / `CryptUnprotectData`. `dynamics_crm.py` owns
read/write. A corrupt or undecryptable cache is treated as "not connected" (delete
and re-prompt on next use) — never crash the app over this integration, matching the
best-effort philosophy already established by `outlook_calendar.py`.

**Error handling.**
- Auth failure (expired token, revoked consent, wrong org URL): dialog shows the real
  exception message inline (same "surface the real error" rule used for AI provider
  connection tests — never swallow it into a generic message), with a shortcut into
  Settings to reconnect.
- Record search / note creation failures (network, permissions, invalid record):
  dialog stays open with an inline error and a Retry button; the user's section
  selections and picked record are preserved, never silently discarded.
- Any new HTTP client here gets an explicit timeout (120s), consistent with
  `ai-providers.md`'s convention — no relying on an SDK default that can hang the
  worker.

## 5. Testing strategy

Per this repo's convention: non-UI logic gets TDD unit tests; PyQt widgets get
smoke-tested manually rather than widget-tested, beyond pure helper functions.

- **`tests/test_dynamics_crm.py`** — pure-logic tests against
  `app/integrations/dynamics_crm.py` with all Dataverse HTTP calls mocked: note
  payload construction (`objectid`/`objecttypecode`/`subject`/`notetext` shape per
  entity type), record-search response parsing, token-cache encrypt/decrypt
  round-trip, and degrade-to-"not connected" behavior on a corrupt cache.
- **`tests/test_tag_manager.py` additions** — `crm_link` round-trips through
  `load_all_tags` / `save_all_tags` / `create_tag` / `rename_tag` / `delete_tag`
  (encoding the fix noted in Section 2), plus a helper that, given a recording's tag
  list, returns the tags that carry a `crm_link`.
- **Match-precedence logic** — a pure function (e.g.
  `dynamics_crm.suggest_records(tags, calendar_event)`) encoding "tag mapping wins,
  multiple tag mappings are all listed, calendar match is the fallback suggestion" —
  fully unit-testable without Qt or network.
- **`DynamicsExportDialog` / `DynamicsExportWorker`** — smoke-tested manually
  (`python -c` import + construct), not unit-tested beyond the pure helpers above.

## Setup prerequisite

An Azure AD app registration (public client, delegated Dataverse
`user_impersonation` permission) must be created before this feature can be used.
This is documented as a one-time setup step (Client ID + org URL entered in
Settings), not something TalkTrack provisions itself.
