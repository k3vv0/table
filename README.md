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
