#!/usr/bin/env bash
# One-command deploy for Table, run from your own machine (Git Bash on Windows is
# fine) against the droplet. See README.md "Deploying to the droplet" for the one-time
# ~/.ssh/config setup this depends on.
#
# No credentials live in this script or need configuring separately: it only ever
# calls `ssh`/`tar | ssh` against the DROPLET_HOST alias below, so whatever key your
# ssh-agent is already holding is exactly what authenticates -- same as running any
# of these commands by hand.
#
# What it does, in order, every single time (no "did this one touch the schema?"
# judgment call -- see DESIGN.md §13j for why that question is easy to get wrong):
#   1. Streams the current directory up over SSH as a tarball, skipping data/, .env,
#      and .venv/ -- those three must never be overwritten by your local copies --
#      then hands everything it just wrote back to the table:table user/group,
#      since it's transferred in as root but the service runs as table.
#   2. Reinstalls dependencies into the droplet's existing venv (covers a changed
#      requirements.txt; a no-op otherwise), then re-applies table:table
#      ownership again, since pip (also run as root) creates new files in .venv/.
#   3. Takes a timestamped `.backup` snapshot of the live database before touching
#      anything -- the WAL-safe way to snapshot SQLite (money-hub DESIGN.md §11g), not a plain
#      file copy. Readable by root only; older snapshots beyond the newest three are
#      deleted.
#   4. Runs every scripts/migrate_*.py. Each one checks first and no-ops if its
#      column/table already exists, so running all of them on every deploy is safe --
#      that removes the "which one is new" judgment call entirely.
#      A migration that fails stops the deploy here, before the restart.
#   5. Restarts the systemd service and confirms it's actually back up.
#
# If step 5 doesn't come back healthy, restore the backup this run just took -- the
# exact command is printed at the end, with that run's real timestamp filled in.
#
# DROPLET_HOST logs in as root directly (see README.md), so restarting the service
# needs no sudo -- if you later switch to a non-root deploy user, step 5 below will
# need `sudo systemctl` added back in, and that user will need a NOPASSWD sudoers
# rule scoped to just the restart/is-active commands (a real TTY works too, but
# defeats running this unattended).

set -euo pipefail

# --- these match the droplet as it's actually set up (confirmed from its systemd
#     unit and directory layout -- see README.md if you ever rebuild the droplet
#     from scratch and these need to change) ---
DROPLET_HOST="money-hub-droplet"         # the same droplet and ~/.ssh/config alias as money-hub
APP_DIR="/opt/table"                      # where the app lives on the droplet
SERVICE="table"                           # the systemd unit name
RUN_AS="table"                            # the user/group the service runs as
# ----------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> Deploying $PROJECT_ROOT to $DROPLET_HOST:$APP_DIR"
echo

echo "==> [1/5] Copying code up (data/, .env, .venv/ are never touched)..."
tar \
  --exclude='.venv' \
  --exclude='data' \
  --exclude='.env' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache' \
  --exclude='*.egg-info' \
  --exclude='.git' \
  -czf - . | ssh "$DROPLET_HOST" "mkdir -p '$APP_DIR' && tar -xzf - -C '$APP_DIR' && chown -R '$RUN_AS:$RUN_AS' '$APP_DIR'"
echo

echo "==> [2/5] Installing dependencies into the existing venv..."
ssh "$DROPLET_HOST" "cd '$APP_DIR' && .venv/bin/pip install -q -r requirements.txt && chown -R '$RUN_AS:$RUN_AS' '$APP_DIR/.venv'"
echo

BACKUP_NAME="table_pre_deploy_$(date +%Y%m%d_%H%M%S).db"
echo "==> [3/5] Backing up the live database to /tmp/$BACKUP_NAME on the droplet..."
# The very first deploy has no database yet (the app creates it on start), so there is
# nothing to back up.
ssh "$DROPLET_HOST" "cd '$APP_DIR' && if [ -f data/table.db ]; then sqlite3 data/table.db \".backup /tmp/$BACKUP_NAME\" && chmod 600 /tmp/$BACKUP_NAME; else echo '    (no database yet, skipped)'; fi"
# Keep only the three newest snapshots: each is a full copy of the database, and the
# nightly encrypted R2 backup (scripts/backup.sh) is the long-term copy.
ssh "$DROPLET_HOST" "ls -1t /tmp/table_pre_deploy_*.db 2>/dev/null | tail -n +4 | xargs -r rm -f --"
echo

echo "==> [4/5] Running every migration script (idempotent -- a no-op if nothing changed)..."
ssh "$DROPLET_HOST" "cd '$APP_DIR' && for f in scripts/migrate_*.py; do [ -e \"\$f\" ] || continue; echo \"  -- \$f\"; .venv/bin/python \"\$f\" || exit 1; done"
echo

echo "==> [5/5] Restarting $SERVICE and confirming it's back up..."
# The app takes a few seconds to start, so poll /api/health for up to 20s rather than
# checking once after a fixed sleep (a 2s sleep reported "HTTP 000" on a healthy start).
ssh "$DROPLET_HOST" "systemctl restart '$SERVICE' && for i in \$(seq 1 20); do code=\$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/api/health); [ \"\$code\" = 200 ] && break; sleep 1; done; systemctl is-active '$SERVICE'; echo \"HTTP \$code\"; [ \"\$code\" = 200 ]" \
  || echo "    !! Health check never returned 200 -- check: ssh $DROPLET_HOST journalctl -u $SERVICE -n 50"
echo

echo "==> Deploy finished."
echo "    Backup saved on the droplet at /tmp/$BACKUP_NAME"
echo "    If anything looks wrong, restore it with:"
echo "    ssh $DROPLET_HOST \"systemctl stop $SERVICE && cp /tmp/$BACKUP_NAME '$APP_DIR/data/table.db' && systemctl start $SERVICE\""
