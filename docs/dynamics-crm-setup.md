# Dynamics 365 CRM Export — Setup Guide

What has to exist **before** TalkTrack can export a recording to Dynamics 365.

> **Status:** the feature itself is not implemented yet — see
> [the design spec](superpowers/specs/2026-08-23-dynamics-crm-export-design.md). This
> guide documents the prerequisites so the Entra/Dynamics side can be arranged in
> parallel, and so the one-time setup is written down before anyone has to guess at it.

TalkTrack signs in **as you** (delegated OAuth). It never uses a service account, never
stores a client secret, and can only do in Dynamics what your own security role already
allows. The trade-off is that a Microsoft Entra app registration has to exist first, and
creating one usually needs an administrator.

---

## Who does what

| # | Step | Who | One-time? |
|---|------|-----|-----------|
| 1 | Find the Dataverse environment URL | Anyone | Yes |
| 2 | Register the app in Microsoft Entra ID | Entra admin | Yes, per tenant |
| 3 | Grant admin consent for the Dataverse permission | Entra admin | Yes, per tenant |
| 4 | Confirm the security role can create notes | Dynamics admin | Per user |
| 5 | Confirm attachment size + allowed extensions | Dynamics admin | Yes, per org |
| 6 | Enter Org URL + Client ID in TalkTrack, Connect | Each user | Yes, per machine |
| 7 | Link tags to CRM records | Each user | Ongoing |

Steps 2–5 are the administrator's; if that isn't you, the
[handoff checklist](#handoff-checklist-for-your-administrator) at the end is written to be
pasted into a ticket.

---

## 1. Find the Dataverse environment URL

The origin of your Dynamics 365 org, no trailing slash and no path:

```
https://contoso.crm4.dynamics.com
```

Get it from the browser address bar while in Dynamics (strip everything from `/main.aspx`
onward), or from the Power Platform admin center → **Environments** → your environment →
**Environment URL**.

This single value drives both the API endpoint (`{org_url}/api/data/v9.2/`) and the OAuth
scope (`{org_url}/user_impersonation`), so it has to be exact.

## 2. Register the app in Microsoft Entra ID

In the [Azure portal](https://portal.azure.com) → **Microsoft Entra ID** →
**App registrations** → **+ New registration**:

| Field | Value |
|-------|-------|
| **Name** | `TalkTrack CRM Export` (anything meaningful — users see it on the consent prompt) |
| **Supported account types** | *Accounts in this organizational directory only* (single tenant) |
| **Redirect URI** | leave blank here; added in the next step |

Select **Register**, then configure three things on the new registration:

**a. Redirect URI** — **Authentication** → **Add a platform** → **Mobile and desktop
applications** → check `http://localhost` → **Configure**. This is what MSAL's
interactive browser sign-in returns to. (It is a loopback address on the user's own
machine, not a public endpoint — nothing is exposed to the internet.)

**b. Allow public client flows** — **Authentication** → **Advanced settings** → set
**Allow public client flows** to **Yes** → **Save**. A desktop app can't keep a secret,
so it authenticates as a public client. Without this, sign-in fails.

**c. API permission** — **API permissions** → **+ Add a permission** → the
**APIs my organization uses** tab → search **Dataverse** → **Delegated permissions** →
check **user_impersonation** (shown as *Access Dynamics 365 as organization users*) →
**Add permissions**.

Then copy the **Application (client) ID** GUID from the registration's **Overview** page.
That is the "Client ID" TalkTrack asks for. It is not a secret — it identifies the app,
not the user — so it is fine to share with the people who need to configure TalkTrack.

## 3. Grant admin consent

Still on **API permissions**, select **Grant admin consent for _<tenant>_** → **Yes**,
even if the permission already looks checked.

Skipping this is the single most common cause of a failed first sign-in. Without consent,
users get a consent error at run time — and in tenants where user consent is disabled,
they cannot resolve it themselves. If the button is greyed out, you don't have the rights
to consent and need someone who does.

## 4. Confirm the security role can create notes

Export creates an **annotation** (Note) attached to an Account, Contact, or Opportunity,
so the *user's* security role needs, at minimum:

- **Note** — Create, Append
- **Account / Contact / Opportunity** — Read, Append To

Most standard roles (Salesperson, Sales Manager, System Customizer) already have these.
The Read privileges also decide what the record search can find: the export dialog can
only see records the user could see in Dynamics anyway, so an empty search result means
"nothing visible to you", not "nothing exists".

## 5. Confirm attachment size and allowed extensions

The transcript is attached to the Note as a `.md` file, which two org-wide settings can
block. Both live in **Advanced Settings** → **Settings** → **Administration** →
**System Settings**:

- **Email** tab → **Set file size limit for attachments** → *Maximum file size (in
  kilobytes)*. Default is **5 MB** (5,120 KB); the ceiling is 128 MB (131,072 KB).
  5 MB is plenty — a transcript Markdown file is typically tens to hundreds of KB, and
  TalkTrack posts the file inline rather than in chunks, which is only valid under ~4 MB
  regardless of this setting.
- **General** tab → **Set blocked file extensions for attachments**. `md` is **not** in
  the Dataverse default block list, so this normally needs no change — but confirm your
  org hasn't added it. (The setting is stored in `Organization.BlockedAttachments` if you
  prefer to query it.)

If the attachment is rejected, TalkTrack still creates the Note with the summary and
notes and tells you why the file didn't attach — the export doesn't fail outright.

## 6. Configure TalkTrack

Per user, per machine, in **Settings → Dynamics CRM**:

1. **Org URL** — from step 1.
2. **Azure AD Client ID** — from step 2.
3. Tick **Enable Dynamics CRM export**. The Export CRM button and menu action stay
   hidden until this is on *and* both fields are filled.
4. Select **Connect**. A browser window opens for the normal Microsoft sign-in (MFA
   included). On success the status line reads *Connected as you@contoso.com*.

The resulting token is cached at `~/.talktrack/dynamics_token_cache.bin`, encrypted with
Windows DPAPI and tied to your Windows user account on that machine — copying the file to
another machine or user yields nothing readable. **Disconnect** deletes it.

If the browser can't open or the loopback port is blocked, sign-in falls back to device
code: TalkTrack shows a code to enter at `microsoft.com/devicelogin`.

## 7. Link tags to CRM records

This is the ongoing part, and what makes the feature quick to use rather than a search
every time.

In **Tag Manager**, pick a tag you already use per client or account and choose
**Link to CRM record...**, then search for and select the Account, Contact, or
Opportunity. From then on, any recording carrying that tag suggests that record as a
one-click chip in the export dialog.

Notes on behaviour worth knowing up front:

- A recording with **several** linked tags lists all their records as chips and
  pre-selects none — TalkTrack won't guess between two mappings you made deliberately.
- Manual search is always available, so an unlinked or wrongly-linked tag is never a
  dead end.
- Deleting and recreating a tag loses its CRM link (recordings reference tags by name),
  the same way it loses the tag's colour.

---

## Verify it end-to-end

1. Open a recording that has a transcript and a summary.
2. **Export CRM** → pick a record you can safely write to (a test Account is ideal).
3. Export, then follow the link in the success message.
4. In Dynamics, the record's timeline should show a Note whose title is your subject,
   whose body holds the summary and notes, and which carries a `.md` attachment.

If step 4 shows the Note but no attachment, revisit step 5.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| *Need admin approval* / consent error on Connect | Admin consent not granted | Step 3 |
| Sign-in fails immediately, before any prompt | **Allow public client flows** is off | Step 2b |
| Browser opens then errors on the redirect | `http://localhost` not registered | Step 2a |
| Connects, but every request 401s | Org URL wrong, or wrong tenant's account | Step 1; sign out and reconnect |
| Search returns nothing for a record you can see in Dynamics | Read privilege, or searching a field other than name/email | Step 4 |
| Export fails naming a privilege | Role lacks Create/Append on Note or Append To on the record | Step 4 |
| Note created, attachment missing | Size limit or blocked extension | Step 5 |
| Status resets to *Not connected* after working before | Token cache corrupt, or Windows profile/machine changed | Select **Connect** again |

## What TalkTrack does and does not do

- **Writes** exactly one thing: a Note (with optional file attachment) on a record you
  pick. Nothing else is created or modified.
- **Reads** only Accounts, Contacts, and Opportunities, to search and to display names.
- **Never** creates records, edits existing ones, or deletes anything.
- **Never** uses a client secret, certificate, or service account; there is no
  application user to provision and no S2S trust to establish.
- **Never** sends anything to Dynamics unless you choose sections and select Export.
  The transcript stays local until then.

---

## Handoff checklist for your administrator

> **Request: Entra app registration for TalkTrack → Dynamics 365 note export**
>
> TalkTrack is a desktop meeting-recording app. We want it to attach a meeting summary
> and transcript as a Note on Dynamics records. It authenticates as the signed-in user
> (delegated OAuth, public client, no secret) and only creates notes — no service
> account, no application user, no write access beyond annotations.
>
> Please:
> 1. Create an Entra app registration named `TalkTrack CRM Export`, single tenant.
> 2. Authentication → add platform **Mobile and desktop applications** with redirect URI
>    `http://localhost`.
> 3. Authentication → Advanced settings → **Allow public client flows** = **Yes**.
> 4. API permissions → APIs my organization uses → **Dataverse** → Delegated →
>    **user_impersonation**.
> 5. **Grant admin consent** for the tenant.
> 6. Send us the **Application (client) ID** and confirm our Dataverse environment URL.
>
> Also please confirm, in System Settings: attachments are allowed up to at least 5 MB
> (Email tab), and `md` is not in the blocked file extensions list (General tab).

## Reference

- [Register an app with Microsoft Entra ID](https://learn.microsoft.com/power-apps/developer/data-platform/walkthrough-register-app-azure-active-directory)
- [Use OAuth authentication with Microsoft Dataverse](https://learn.microsoft.com/power-apps/developer/data-platform/authenticate-oauth)
- [Use file data with Attachment and Note records](https://learn.microsoft.com/power-apps/developer/data-platform/attachment-annotation-files)
- [Note (Annotation) table reference](https://learn.microsoft.com/power-apps/developer/data-platform/reference/entities/annotation)
- [Files and images overview — blocking file types](https://learn.microsoft.com/power-apps/developer/data-platform/files-images-overview)
