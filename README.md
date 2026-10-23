# Table

Kev and Beth's household app at https://dash.sangmuah.net. See DESIGN.md for what it is
and the milestone plan, and CLAUDE.md for working rules.

## Running locally

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # .venv\Scripts\pip on Windows
cp .env.example .env            # leave the Access values blank: every request is Kev
.venv/bin/uvicorn app.main:app --reload --port 8010
.venv/bin/python -m pytest -q
```

## First deploy

One-time setup, on the same droplet as money-hub (DESIGN.md §6). Order matters: Access
goes in front before the hostname exists, so the app is never reachable without a login.

1. **Access application.** Zero Trust → Access → Applications → Add a self-hosted app
   named "Table" for `dash.sangmuah.net`, covering the whole host. Google login; a policy
   allowing Kev's, Beth's and the joint household account's emails. Copy its Application
   Audience (AUD) tag.
2. **Tunnel hostname.** On the existing tunnel, add the public hostname
   `dash.sangmuah.net` → `http://localhost:8010`.
3. **Service token.** Access → Service Auth → create `table-agent`. Add a Service Auth
   policy for it on the Table app. Its Client ID goes in `.env` as
   `AGENT_SERVICE_TOKEN_ID`; the ID and secret go in the project's cloud environment as
   `TABLE_CF_ACCESS_CLIENT_ID` and `TABLE_CF_ACCESS_CLIENT_SECRET`.
4. **Droplet.**

   ```bash
   adduser --system --group --home /opt/table table
   mkdir -p /opt/table/data && chown -R table:table /opt/table
   sudo -u table python3 -m venv /opt/table/.venv
   ```

   Write `/opt/table/.env` from `.env.example` (`chown table:table`, `chmod 600`).
5. **First deploy** from your PC: `bash scripts/deploy.sh`. Then install the unit once:

   ```bash
   cp /opt/table/deploy/systemd/table.service /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now table
   curl -s http://127.0.0.1:8010/api/health
   ```

6. **Check** on the phone, over cellular: dash.sangmuah.net asks for a Google login, then
   shows the Table; `/elizabeth` is a 403 for Kev.

## Phone (milestone 3)

1. **Push keys.** On your PC, `python scripts/make_vapid_keys.py`; put the two lines it
   prints in the droplet's `.env`, then deploy (the deploy restarts the app).
2. **Capture service token.** Access → Service Auth → create `table-capture`. Add a
   Service Auth policy for it on the Table app, next to `table-agent`'s. Its Client ID
   goes in the droplet's `.env` as `CAPTURE_SERVICE_TOKEN_ID`; keep the ID and secret
   for the Shortcut.
3. **Evening reminder.** On the droplet:

   ```bash
   cp /opt/table/deploy/systemd/table-reminder.* /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now table-reminder.timer
   ```

4. **Each phone.** In Safari, open dash.sangmuah.net → Share → Add to Home Screen. Open
   Table from the Home Screen, go to Settings and tap "Turn on for this device", then send a
   test.
5. **The Shortcut.** In Settings, make a capture token for the phone and copy it. In the
   Shortcuts app, make a Shortcut that shows in the share sheet and accepts text and URLs,
   with one "Get Contents of URL" action:
   - URL `https://dash.sangmuah.net/api/capture`, method POST, request body JSON with
     `text` = Shortcut Input (and optionally `to` = `table`);
   - headers `CF-Access-Client-Id` and `CF-Access-Client-Secret` (the `table-capture`
     token) and `X-Capture-Token` (from Settings).

   Add a "Show Notification" after it if you want a confirmation. Beth does steps 4 and 5
   on her phone with her own capture token; the service token is shared.

## Mail and calendar (milestone 4)

1. **App passwords.** For each Gmail inbox to sync (needs 2-Step Verification): Google
   Account → Security → App passwords, make one named "Table". Then in Gmail settings →
   Forwarding and POP/IMAP, make sure IMAP is on.
2. **Calendar addresses.** Google Calendar on the web → the calendar's Settings →
   "Integrate calendar" → copy "Secret address in iCal format".
3. **Droplet `.env`.** Add (see `.env.example`):

   ```
   MAIL_ACCOUNTS=kev|kpsangmuah03@gmail.com|<app password>;table|bethnkev2003@gmail.com|<app password>
   CALENDAR_FEEDS=kev|<secret iCal address>;table|<secret iCal address>
   MAIL_ARCHIVE_NOISE=false
   ```

   Deploy, then install the sync timer and run it once:

   ```bash
   cp /opt/table/deploy/systemd/table-sync.* /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now table-sync.timer
   systemctl start table-sync && journalctl -u table-sync -n 20
   ```

   The Mail tab on the board then shows the synced mail as "Not sorted yet".
4. **Sweep token.** Access → Service Auth → create `table-sweep` and add a Service Auth
   policy for it on the Table app. Its Client ID goes in the droplet's `.env` as
   `SWEEP_SERVICE_TOKEN_ID`; deploy again.
5. **Sweep routine.** In Claude Code on the web, make an environment `table-sweep` with
   `TABLE_CF_ACCESS_CLIENT_ID` and `TABLE_CF_ACCESS_CLIENT_SECRET` set to the
   `table-sweep` token, and network access to `dash.sangmuah.net`. Then a routine on this
   repo, in that environment, a few times a day (e.g. 7am, noon, 5pm Eastern), whose prompt
   is the contents of `routines/sweep.md`.
6. After a week of sorts that look right in the Mail tab, set `MAIL_ARCHIVE_NOISE=true` and
   deploy: noise then leaves the inbox under the `Table/Noise` label.

## People and dates (milestone 5)

1. **Deploy.** `deploy.sh` runs `scripts/migrate_suggestion_payload.py`, which adds the new
   tables and one column; nothing else to do for the database.
2. **Date alerts.** On the droplet, install the 6:30am timer and run it once:

   ```bash
   cp /opt/table/deploy/systemd/table-dates.* /etc/systemd/system/
   systemctl daemon-reload && systemctl enable --now table-dates.timer
   systemctl start table-dates && journalctl -u table-dates -n 20
   ```

3. **Add dates.** On the board's Dates tab: tap the common dates you want, add birthdays
   and anniversaries, and give each person notes on what they like. Or ask Claude in this
   project to run the dates interview; its suggestions show on the Dates tab to accept.
4. **Morning token.** Access → Service Auth → create `table-morning`, and add it to a
   Service Auth policy on the Table app (and save). Its Client ID goes in the droplet's
   `.env` as `MORNING_SERVICE_TOKEN_ID`; deploy again.
5. **Morning routine.** An environment `table-morning` like `table-sweep` (same setup
   script, network access to `dash.sangmuah.net` plus the open web for researching ideas)
   with `TABLE_CF_ACCESS_CLIENT_ID` and `TABLE_CF_ACCESS_CLIENT_SECRET` set to the
   `table-morning` token. Then a routine in that environment at 7am Eastern whose prompt is
   `routines/morning.md`, with the same "cd ~/table" preamble as the sweep.

## Vault (milestone 6)

1. **Deploy.** `deploy.sh` runs `scripts/migrate_vault_backfill.py`, which gives mail the
   sweep already sorted as vault its entries in the Vault view.
2. **"Save to Vault" Shortcut.** Duplicate "Send to Plate" and add `vault` = true
   (Boolean) to its JSON body; `to` = `table` saves to the Table's vault instead of your
   Plate's. Beth does the same on her phone.
3. **Morning routine.** Nothing to change: it reads `routines/morning.md` from the repo,
   which now runs `vault-picks` after `date-options`. It needs open web access to read the
   links it summarises.

## Deploying

`bash scripts/deploy.sh` from the repo root (Git Bash on Windows is fine). It uses the
`money-hub-droplet` SSH alias from money-hub's README.

## Backups

Create an R2 bucket `table-backups` with a 30-day lifecycle rule and an R2 token scoped
to it, and fill in the `R2_*` and `BACKUP_AGE_RECIPIENT` values in `.env` (money-hub's age
key is fine). Then, on the droplet:

```bash
apt install sqlite3 age rclone
cp /opt/table/deploy/systemd/table-backup.* /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now table-backup.timer
systemctl start table-backup && journalctl -u table-backup -n 20
```

Prove a backup restores with `python scripts/restore_check.py <file>.db.gz.age <age key>`
on your PC.
