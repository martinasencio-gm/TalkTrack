# Dynamics 365 CRM Export — Design

Date: 2026-08-23 (revised 2026-08-24 after code review)

**Status: designed, reviewed, deferred — not implemented.** Parked 2026-08-24 for a later
date. The design below is current and reviewed against the codebase, so implementation
can start from it directly; what is missing is the plan file
(`docs/superpowers/plans/YYYY-MM-DD-dynamics-crm-export.md`) and a tracking GitHub issue,
both of which come first per `.claude/rules/ways-of-working.md`. No code exists yet —
`app/integrations/dynamics_crm.py`, `app/ui/dynamics_export_dialog.py`, the
`dynamics_crm` config block, and the `msal` dependency are all still to be written.

Prerequisites are documented in [docs/dynamics-crm-setup.md](../../dynamics-crm-setup.md)
and can be arranged with an Entra/Dynamics admin ahead of any code.

Issue: _to be filed before implementation starts._

## Problem

TalkTrack has no way to get a recording's AI summary, call notes, or transcript into
Microsoft Dynamics 365 (Dataverse). Users currently copy content out manually. This adds
an opt-in "Export to Dynamics CRM" feature that writes the summary and notes into a Note
on an existing Dynamics record (Account, Contact, or Opportunity), with the transcript
attached to that same Note as a Markdown file.

## Non-goals

- Two-way sync (reading from Dynamics back into TalkTrack) — export only.
- Creating new Dynamics records (Contacts/Accounts/Opportunities) — always attaches
  to an existing record the user already has in Dynamics.
- Bulk export of multiple recordings at once — v1 is single-recording only.
- Action items as their own exported section. v1's inline body is Summary and Notes
  only. They are not lost, though — the attached `transcript.md` includes them, so they
  reach Dynamics inside the file, just not in the timeline text. Promoting them to a
  third inline section later is one checkbox and one block in the inline builder.
- Batch CLI (`app/batch/`) integration — this is an interactive, user-driven action,
  not part of the headless pipeline.
- **Calendar-attendee record matching.** The original design assumed
  `calendar_event.json` held attendee email addresses to look up against Dynamics
  Contacts. It does not: `outlook_calendar.find_overlapping_events` builds `attendees`
  from `appt.RequiredAttendees`, a semicolon-separated string of Outlook **display
  names**, and `organizer` is a display name too. Matching Contacts on display name is
  too imprecise to be worth the code. A follow-up can add this properly by resolving
  `appt.Recipients` → `AddressEntry.GetExchangeUser().PrimarySmtpAddress` into a new
  `attendee_emails` key — note that would be empty for every existing recording until
  it is re-tagged. v1 ships tag mapping plus manual search, which is the stronger
  signal anyway.

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

### Auth

Auth uses MSAL's public-client interactive/device-code flow (delegated user OAuth):

- **Authority:** `https://login.microsoftonline.com/organizations` (work/school
  accounts only — a Dataverse org is never a personal-account tenant).
- **Scope:** `{org_url}/user_impersonation` (equivalently `{org_url}/.default` when the
  app registration's Dataverse permission is pre-consented). One scope, derived from
  the configured org URL — not hardcoded.
- **App registration prerequisite:** public client with "Allow public client flows"
  enabled, the delegated Dynamics CRM `user_impersonation` permission, and
  `http://localhost` registered as a redirect URI for `acquire_token_interactive`.
  Device-code flow needs no redirect URI and is the fallback when the loopback
  listener can't bind.
- **Token cache:** MSAL's `SerializableTokenCache`, serialized and DPAPI-encrypted via
  `win32crypt.CryptProtectData` / `CryptUnprotectData` (pywin32 is already a
  dependency), stored at `~/.talktrack/dynamics_token_cache.bin`, separate from
  `settings.json` — a stronger bar than the plaintext convention used for AI provider
  API keys today, because this credential can act on the user's behalf in Dynamics,
  not just call a completion API.

### Dependencies

`msal` is **not** currently a dependency and must be added. Unlike the AI provider SDKs
— which exist behind `app/utils/package_installer.py` specifically because they are
large and only one is ever wanted — `msal` is small and pure-Python, so it goes
straight into `requirements.txt` and `pyproject.toml` (kept in sync, with a major-version
cap per `packaging-and-launch.md`):

```
msal>=1.28,<2
```

`msal` pulls `requests` transitively, and `requests` is what `dynamics_crm.py` uses for
the Dataverse calls — with an explicit `timeout=120` on every call, per the
`ai-providers.md` convention. No SDK-default timeout, which would hang the worker and
block app close.

## 2. Record matching

Dynamics record suggestions come from tag mappings; anything unmatched is found by
manual search.

**Tag mapping (new).** `tags.json` entries (managed via `app/utils/tag_manager.py`
and `app/ui/tag_manager_dialog.py`) gain an optional `crm_link` field:
`{"entity": "account"|"contact"|"opportunity", "id": "<guid>", "display_name": str}`.
Set via a new "Link to CRM record..." action in the existing Tag Manager dialog,
which searches Dataverse and stores the picked record against the tag. Any
recording carrying that tag then suggests that record — this reuses the tagging
users already do per-client/account, at the Account/Opportunity level rather than
per-attendee.

**Precedence.** If a recording carries multiple tags that each have a `crm_link`,
**all** of their mapped records are listed as suggestion chips — none is auto-picked,
the user must click one (never silently guess between two explicit, user-curated
mappings). Exactly one mapping is pre-selected. Zero mappings means the user searches.
A manual search box is always present as the fallback for anything unmatched or wrong.

This is encoded as a pure function, `dynamics_crm.suggest_records(tags)`, taking the
recording's tag-name list and returning the mapped records in tag order — no Qt, no
network, fully unit-testable.

**Implementation note.** `tag_manager.load_all_tags` and `save_all_tags` currently
rebuild each tag as `{"name": …, "color": …}` and silently drop every other key
(`tag_manager.py:74` and `tag_manager.py:93`). Both must instead **preserve unknown
keys** — generically, not by special-casing `crm_link`, so the next optional tag field
doesn't have to rediscover this bug. Fixing those two functions is sufficient:
`create_tag`, `rename_tag`, `delete_tag`, and `update_tag_color` all round-trip through
them. `rename_tag` keeps `crm_link` for free (it mutates the same dict), but note that
deleting and recreating a tag loses its mapping, since recordings reference tags by
name string only — acceptable, and consistent with how tag colors already behave.

## 3. Export dialog and data flow

**`DynamicsExportDialog`** (new, `app/ui/dynamics_export_dialog.py`) is opened from:
- a new "Export CRM" button next to `Export TXT` / `Export SRT` in the transcript
  viewer (`app/ui/transcript_viewer.py`), or
- a new "Export to Dynamics CRM..." action in the recordings list context menu
  (`app/ui/recordings_list.py`), hidden/disabled when more than one recording is
  selected (single-recording only per Non-goals).

**Who constructs the dialog.** `TranscriptViewer` holds only `_transcript` and
`_speaker_names` — it has no session directory or metadata (its own `_export` works
purely off `_transcript`). So its button does **not** build the dialog: it emits a new
signal that `MainWindow` handles, and MainWindow — which owns `_current_session` — opens
the dialog. This is the same `SegmentWidget → TranscriptViewer → MainWindow` flow the
repo already uses for transcript edits. The recordings-list action, which does have the
session in hand, constructs it directly.

**Reading the recording.** The dialog's only input is the session dict (metadata plus
`directory`). Everything else is read through `app/utils/session_io.py`, which already
knows where each piece lives — `load_tags(session)` and the `_read_json` / `_read_text` pattern used by
`export_session_markdown` — here for `summary.md`, `notes.txt`, `transcript.json` (for
the segment count that decides whether the Transcript checkbox is live), and
`transcript.md` (the attachment). Nothing new is plumbed through the widgets.

Dialog layout:

1. **Record picker.** Suggestion chips first (the tag-mapped records), each showing an
   entity-type badge (Account/Contact/Opportunity) plus display name; clicking one
   selects it. A search box below finds anything unmatched. Dataverse has no
   cross-entity query in the Web API without relevance search provisioned, so this is
   **three separate requests** — `accounts?$filter=contains(name,'…')`,
   `contacts?$filter=contains(fullname,'…') or contains(emailaddress1,'…')`,
   `opportunities?$filter=contains(name,'…')` — each `$top=20`, merged and labelled by
   entity type, and debounced (~300 ms) so typing doesn't fire a request per keystroke.
   The Export button stays disabled until a record is selected.
2. **Section checkboxes.** Summary / Notes / Transcript, default all-checked. A section
   with no content for this recording is greyed out and unchecked. This needs its own
   small pure helper — `transcript_export`'s `has_exportable_content` only asks whether
   `transcript_data["segments"]` is non-empty, and is consulted solely by
   `export_transcript`; the TXT/SRT buttons gate on transcript load, not on it. The new
   helper reports per-section emptiness (`summary.md` / `notes.txt` / transcript
   segments) from what `session_io` read, and is unit-tested directly. All three
   unchecked disables Export — an empty Note is not worth a round-trip.
3. **Subject field.** Pre-filled from the calendar event's subject when the recording
   is calendar-tagged, else the recording's name; editable. `annotation.subject` is
   ApplicationRequired and caps at **500 characters**, so the field enforces non-empty
   and truncates at 500.
4. **Export button.** Assembles the payload from the checked sections (below), then
   hands it to the worker.

### Note body: Summary and Notes inline, transcript as an attached file

`annotation.notetext` caps at **100,000 characters** and the Dynamics notes UI renders
it as text, which mangles Markdown. A full meeting transcript routinely blows past both,
while a summary and a page of notes are exactly what someone wants to read without
clicking through. So the payload splits:

- **`notetext`** — a short header naming the recording (name, date, duration), followed
  inline by Summary then Notes when checked, each under its own `## ` heading.
  Truncated at 100,000 with an explicit marker as a backstop. When Transcript is
  checked, the header also says the transcript is attached, so a reader who only sees
  the timeline entry knows there is a file on it.
- **`documentbody`** — when Transcript is checked, the transcript Markdown,
  base64-encoded, with `filename` (≤255 chars, derived from the recording name and
  sanitized of characters illegal on Windows or in Dataverse), `mimetype:
  "text/markdown"` (≤256), and `isdocument: true`. Markdown survives intact and is
  downloadable from Dynamics.

**The attachment is best-effort — the Note is not.** One `POST` creates the annotation
with the file on it, so an attachment problem would otherwise take the whole export down
with it. Direct `documentbody` on create is only valid under ~4 MB (above that Dataverse
requires the chunked `InitializeAnnotationBlocksUpload` / `UploadBlock` /
`CommitAnnotationBlocksUpload` flow, which v1 does not implement), and an org can cap
attachment size or block the file type outright. So: check the encoded size client-side
first, and if the create fails for a reason attributable to the attachment (413, or a
Dataverse error naming `documentbody` / file size / file type), **retry once without
`documentbody`** and report success-with-a-warning — "Note created; transcript could not
be attached: _<reason>_". The summary and notes land either way, and the user can still
use Export TXT. Any other failure is a plain failure, retryable per §4.

**What the attached file actually is:** the recording's existing `transcript.md`, read
off disk. No new builder, no second dialect — that file is written and kept current by
`session_io.export_session_markdown` on every transcript/notes/summary save, and it is
already the LLM-ready document this feature wants. Two consequences worth stating
plainly:

- It carries frontmatter plus summary, action items, and notes *in addition to* the
  transcript, so it overlaps the inline `notetext`. That redundancy is deliberate: the
  inline text is the skim, the file is the complete record. It also means action items
  do reach Dynamics inside the attachment even though they are not an exported section.
- The export is only offered when `transcript.md` exists. It is absent for a recording
  that was never transcribed — the same condition that greys out the Transcript
  checkbox — and for older recordings only until their next save regenerates it.

The inline body, by contrast, needs a new pure builder alongside
`app/utils/transcript_export.py`'s `build_export_markdown`: that function produces a
fixed frontmatter-plus-everything document, whereas the inline body is a header plus an
arbitrary subset of two sections, with no frontmatter.

### The Dataverse request

**`DynamicsExportWorker`** (QThread, same pattern as the existing AI provider workers)
resolves an access token — silently from the DPAPI-encrypted cache first, falling back
to interactive/device-code auth (safe to block synchronously since this runs off the UI
thread) — then `POST`s to `{org_url}/api/data/v9.2/annotations`.

The record is bound with a **navigation-property binding per entity type**, not by
writing `objectid`/`objecttypecode` directly (that is the CRM 2011 / OData-v2 style and
the Web API rejects it):

```python
{
    "subject": subject,
    "notetext": inline_body,
    "objectid_account@odata.bind": "/accounts(00000000-0000-0000-0000-000000000000)",
    # ...plus documentbody / filename / mimetype / isdocument when the
    #    Transcript section is checked (dropped on the retry described above)
}
```

The binding key and path vary by picked entity: `objectid_account@odata.bind` →
`/accounts(id)`, `objectid_contact@odata.bind` → `/contacts(id)`,
`objectid_opportunity@odata.bind` → `/opportunities(id)`.

The worker emits success — carrying the created note's id (from the `OData-EntityId`
response header) and a deep link `{org_url}/main.aspx?etn=annotation&id={id}&pagetype=entityrecord`
— or failure, back to the dialog.

### Duplicate exports

Two entry points make it easy to export the same recording twice and create two Notes on
the same record. On success the worker's result is recorded in the recording's
`metadata.json` (note id, record entity/id/display name, timestamp), written through
`atomic_io` like every other metadata update. When the dialog opens for a recording that
already has one, it shows "already exported to _<record>_ on _<date>_" with the deep
link — informational, not a block; re-exporting after adding a summary is legitimate.

## 4. Settings, config, and error handling

**New Settings tab: "Dynamics CRM".** Fields: Org URL, Azure AD Client ID (plain
config, same convention as other integration settings). A "Connect" button runs the
initial interactive OAuth and shows connection status — mirrors the existing
per-provider AI status indicator ("Connected as user@org.com" / "Not connected"). A
"Disconnect" button deletes the token cache file.

"Connect" runs through the **same worker**, not on the UI thread — interactive OAuth
opens a browser and waits for the loopback callback, which would freeze the app for the
whole round-trip.

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
so users who don't use Dynamics see no new UI. The gate is `enabled` **and** both
`org_url` and `client_id` non-empty: `enabled` alone can be flipped on with nothing
configured, and a visible button that can only fail is worse than no button.

No config migration is needed. `Config.get` raises `KeyError` on a missing key, but
`Config.load` deep-merges `DEFAULT_CONFIG` under the saved settings, so the
`dynamics_crm` block is always present for existing users — nothing to add to
`config_migration.py`.

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
  selections and picked record are preserved, never silently discarded. Dataverse
  returns a structured `{"error": {"code", "message"}}` body — surface `message`, which
  is what names a missing `prvCreateAnnotation` privilege or a bad org URL.
- Every HTTP call carries an explicit `timeout=120`, consistent with
  `ai-providers.md`'s convention — no relying on a library default that can hang the
  worker.

## 5. Testing strategy

Per this repo's convention: non-UI logic gets TDD unit tests; PyQt widgets get
smoke-tested manually rather than widget-tested, beyond pure helper functions.

- **`tests/test_dynamics_crm.py`** — pure-logic tests against
  `app/integrations/dynamics_crm.py` with all Dataverse HTTP calls mocked:
  - note payload construction, both shapes — inline-only when Transcript is unchecked,
    and inline plus `documentbody`/`filename`/`mimetype`/`isdocument` when checked;
  - the correct `objectid_<entity>@odata.bind` key and `/<entityset>(id)` path for each
    of the three entity types;
  - the inline body across the four Summary/Notes combinations, including the header
    line that announces an attachment;
  - `subject` truncation at 500 and `notetext` truncation at 100,000 with a marker;
  - filename sanitization;
  - the attachment fallback: an oversize `transcript.md` is rejected client-side before
    any request; a 413 and a `documentbody`-attributable Dataverse error each trigger
    exactly one retry with `documentbody` stripped and surface
    success-with-a-warning; any other error does **not** retry;
  - record-search response parsing across the three per-entity queries;
  - token-cache encrypt/decrypt round-trip, and degrade-to-"not connected" on a
    corrupt cache;
  - `suggest_records(tags)` precedence — zero, one (pre-selected), and multiple
    (all listed, none auto-picked) `crm_link` tags.
- **`tests/test_tag_manager.py` additions** — arbitrary unknown keys, `crm_link`
  included, round-trip through `load_all_tags` / `save_all_tags` / `create_tag` /
  `rename_tag` / `delete_tag` / `update_tag_color` (encoding the fix noted in
  Section 2), plus the helper that returns a recording's tags carrying a `crm_link`.
- **Section-emptiness helper** — a pure function over what `session_io` read,
  unit-tested for Summary / Notes / Transcript each present, absent, and
  whitespace-only, plus the all-empty case that disables Export and the
  missing-`transcript.md` case.
- **`DynamicsExportDialog` / `DynamicsExportWorker`** — smoke-tested manually
  (`python -c` import + construct), not unit-tested beyond the pure helpers above.

## Setup prerequisite

A Microsoft Entra app registration must be created before this feature can be used:
public client, single tenant, "Allow public client flows" enabled, `http://localhost` as
a redirect URI, and the delegated Dataverse `user_impersonation` permission **with admin
consent granted**. Org-side, the user's security role needs Create/Append on Note and
Read/Append To on Account/Contact/Opportunity, and the org's attachment size limit and
blocked-extension list must permit a `.md` file (both defaults do).

All of this is one-time manual setup — Client ID + org URL are then entered in Settings —
and none of it is something TalkTrack provisions itself. Full walkthrough, including a
checklist to hand to an administrator and a troubleshooting table:
[docs/dynamics-crm-setup.md](../../dynamics-crm-setup.md).
