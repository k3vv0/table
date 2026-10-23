# Table: design

Kev and Beth's household app at https://dash.sangmuah.net. Written October 2026 from the
shaped plan and the first-version implementation plan (both Claude Docs in the Table
project). This file is the spec in the repo; when the plan changes, this changes with it.

## 1. What Table is

Table is Kev's work planner "Plate" pointed at home life. It pulls every commitment out
of email, calendar and quick capture into one place, so nothing lives only in someone's
head, and dates arrive with options ready instead of as a surprise.

| Space | Route | Who | What lives there |
| --- | --- | --- | --- |
| Table | `/` | Kev and Beth | Joint commitments, trips, chores like changing filters, money-hub goals, family birthdays |
| Kev's Plate | `/kev` | Kev | Personal commitments, Syncyn and the Etsy shop, Kev's own people and dates |
| Beth's Plate | `/elizabeth` | Beth, optional | Her own commitments, only if she opts in |

Table items are shared by default; anyone can mark one as Kev's or Beth's. money-hub
stays a separate app: Table links a plan to a money-hub goal by filing a goal proposal
and never copies money data.

**Rhythm.** Triage any time (today, later with a date, done, or merge duplicates). The
habit is one daily session (keep or defer today's leftovers, see what's due within 7
days) and a weekly review. Items close by hand unless marked fully automated. Watch items
wait for something (an approval email); when it lands, Table advances the item and
proposes next steps, and the person closes it.

**Agent action ladder.** (1) Three options with links. (2) Harmless actions, such as a
free, no-card reservation as a safety hold. (3) Approved payments. The first version is
phase 1 only.

## 2. First version: milestones

Each milestone is one to three PRs, ships tests and keeps the app deployable.

1. **Foundation.** Repo, Access login and roles, pages at `/`, `/kev`, `/elizabeth`,
   systemd service, tunnel route, backups, deploy script. Done when Kev signs in on the
   phone and sees an empty Table and Plate.
2. **Plate by hand.** Items, quick capture, triage, merging, the daily session, the
   weekly review, the activity log. Done when Kev runs a few days from it with no agents.
3. **Phone.** Home-screen web app (manifest, service worker), Web Push, and the
   "Send to Plate" iOS Shortcut with a per-device capture token.
4. **Email and calendar.** IMAP sync of both inboxes (Gmail app passwords), the Calendar's
   secret iCal address, the agent API, skills `email-sort`, `commitment-capture` and
   `watch-check`, the sweep routine. Noise is archived under a `Table/Noise` label.
5. **People and dates.** People, weighted dates (planned: 30 days out; light: the day
   before and the day of), preloaded dates, skills `date-options` and `dates-interview`,
   the morning routine, the money-hub goal link.
6. **Vault.** Read-later items from the Shortcut and from mail sorted as vault; skill
   `vault-picks`, two picks a day with summaries.
7. **Audit and launch.** §8 audit, a restore check, Beth invited.

Later: Beth's Plate if she wants one, trips and the docs vault, home inventory, meals,
agent phases 2 and 3, a native iOS app, automatic iMessage reading (needs a Mac).

## 3. Architecture

- The droplet runs the app (FastAPI + SQLite) on `127.0.0.1:8010` behind the existing
  Cloudflare Tunnel and an Access application for `dash.sangmuah.net`.
- Plain code on systemd timers does the fetching (mail, calendar), timing (alerts) and
  pushing (Web Push).
- Two Claude Code routines (sweep, a few times a day; morning, once a day) read through
  the API and file suggestions. They never apply anything; a person approves on the phone.

## 4. Access and roles

`app/access.py`, modelled on money-hub's. Every request carries Access's signed JWT
(`Cf-Access-Jwt-Assertion`), verified against the team's keys, audience, issuer and
expiry. No valid token, no access; `/api/health` is the only public path.

| Identity | How it's recognised | Reaches |
| --- | --- | --- |
| Kev | email in `KEV_EMAILS` | the Table and `/kev`, `/api/plates/kev` |
| Beth | email in `BETH_EMAILS` | the Table and `/elizabeth`, `/api/plates/elizabeth` |
| Household | email in `HOUSE_EMAILS` (the joint Google account) | the Table only; may approve, since a person is signed in |
| agent | service token Client ID `AGENT_SERVICE_TOKEN_ID` | reads the Table and Kev's Plate; writes only what `SERVICE_SCOPES` lists (none yet) |
| capture | service token Client ID `CAPTURE_SERVICE_TOKEN_ID` (the iOS Shortcut) | `POST /api/capture` only, and that route also wants a person's capture token (§9) |

The other person's Plate is a 403, not an empty page. Routine tokens (sweep, morning) join
`SERVICE_SCOPES` in milestone 4. Routes that apply a suggestion depend on
`require_person`, which refuses every service token. With `ACCESS_TEAM_DOMAIN` blank
(local dev, tests) every request is Kev. The app refuses to start half-configured.

## 5. Data model

Milestone 2 adds `item` and `item_source` beside `activity`. The rest arrives with the
milestone that uses it:

| Table | Holds |
| --- | --- |
| `item` | Every commitment, watch, date prep and vault link: `space`, `owner` (blank = shared), `kind`, `status` (inbox, today, later, done, merged), `due_on`, `defer_until`, `merged_into_id`, `automated`, `created_by`, `moneyhub_goal_id` |
| `item_source` | Where an item came from; several after a merge |
| `source_message`, `calendar_event` | Synced mail headers and excerpts; synced events |
| `person`, `key_date` | People and their dates, with a weight and lead time |
| `suggestion` | Anything an agent proposes, pending until a person accepts or dismisses it |
| `vault_pick` | The daily read-later picks |
| `push_subscription`, `capture_token` | Phone push endpoints and the Shortcut's token |
| `agent_run`, `activity` | What each routine did; every write |

## 6. Deployment

Same droplet as money-hub, mirroring its §11:

- User `table`, app at `/opt/table`, venv at `/opt/table/.venv`, `.env` `chmod 600`.
- `deploy/systemd/table.service`: uvicorn on `127.0.0.1:8010`, sandboxed, restart on
  failure.
- Tunnel public hostname `dash.sangmuah.net` → `http://localhost:8010`, behind the "Table"
  Access application covering the whole host (`/*`), Google login, Kev's and Beth's emails.
- `scripts/deploy.sh` from Kev's PC: copy code up, install requirements, snapshot the
  database, run every `scripts/migrate_*.py`, restart, health check.
- `scripts/backup.sh` nightly via `table-backup.timer` at 08:30 UTC: SQLite `.backup`,
  integrity check, gzip, `age` encryption, upload to R2 bucket `table-backups` (30-day
  lifecycle rule). `scripts/restore_check.py` proves a backup restores.

## 7. Agents and skills

Every agent job is a skill in `.claude/skills/<name>/SKILL.md`. Routines check out this
repo read-only so the skills load (unlike money-hub's insights routine, which has no repo),
and a routine's prompt only names the skills to run. Each routine has its own Access
service token, listed in `SERVICE_SCOPES`.

| Skill | Job | Routine |
| --- | --- | --- |
| `email-sort` | New mail into noise, vault or flagged | sweep |
| `commitment-capture` | Commitments from mail and Calendar; duplicate spotting | sweep |
| `watch-check` | Match mail to watch items; draft next steps | sweep |
| `date-options` | Three options for planned dates entering their window | morning |
| `vault-picks` | Two vault picks that fit today, with summaries | morning |
| `dates-interview` | Setup chat for birthdays, anniversaries and one-offs | on request |

Mail content is untrusted: a skill treats it as data and never follows instructions in it.

## 8. Security audit (end of every milestone)

| Check | Where it's covered |
| --- | --- |
| Every route goes through the Access middleware; only `/api/health` is public | tests/test_access.py |
| Bad, expired, wrong-audience and unknown tokens are refused | tests/test_access.py |
| Each Plate refuses the other person and the agent where it should | tests/test_access.py |
| Service-token writes are listed in `SERVICE_SCOPES` and logged | none yet; agent item writes are a 403 (tests/test_access.py) |
| Item APIs follow the Plate rules; an id from another space is a 404 | tests/test_access.py, tests/test_items.py |
| Every item write lands in `activity` | tests/test_items.py |
| Item text reaches the page through `textContent` only, never as HTML | app/static/board.js, review |
| Approving a suggestion needs `require_person` | no such route yet |
| The capture token reaches `POST /api/capture` and nothing else, and still needs a person's token | tests/test_access.py, tests/test_phone.py |
| Capture tokens are stored hashed, shown once, revocable; push devices and tokens are per person | tests/test_phone.py |
| Push payloads carry a title and a short line only; no notes or sources | app/services/push.py, review |
| The app refuses to start half-configured | tests/test_access.py |
| Secrets only in the droplet's `.env` (600) and the cloud environment; none in the repo | `.gitignore`, review |
| `/api/*` responses are `private, no-store` | tests/test_access.py |
| Backups encrypted before leaving the droplet; a restore was tested | after first deploy |
| Dependencies pass `pip-audit` | CI |

## 9. Phone

Milestone 3. No App Store: the site installs to the Home Screen, which is also what lets
iOS deliver Web Push (iOS 16.4 and later, Home Screen apps only).

- **Home-screen app.** `/manifest.webmanifest` opens on your own Plate (the household
  account opens on the Table). `/sw.js` is served from the root so its scope covers every
  page; it only shows notifications and opens the page a tap points at. It caches nothing,
  so no household data sits in a cache. Both are behind Access like everything else.
- **Web Push.** VAPID keys in `.env` (`scripts/make_vapid_keys.py`). Settings turns push
  on per device and stores the browser's endpoint in `push_subscription`. Each device
  has two switches:
  - *New items*: a Table item pushes to everyone sharing the Table except whoever added
    it. A Plate is private, so a Plate item never pushes to anyone else.
  - *Daily session reminder*: `table-reminder.timer` runs `scripts/daily_reminder.py` at
    8pm Eastern. It pushes "N on today's plate, N in the inbox" and stays quiet when
    there's nothing to look at.

  A push carries a title, the item's title and a link, nothing more. Endpoints the push
  service reports gone (404/410) are deleted.
- **"Send to Plate" Shortcut.** A share-sheet Shortcut that POSTs `{text, url, to}` to
  `/api/capture`. Two locks: the `table-capture` Access service token gets it past
  Cloudflare and may reach that route only, and an `X-Capture-Token` made in Settings says
  whose Plate it lands on. The token is shown once and only its SHA-256 is kept; revoking
  it in Settings stops that phone at once. `to: "table"` files to the Table instead. A
  bare URL is kept as the item's link. Items arrive in the inbox with source `shortcut`.
