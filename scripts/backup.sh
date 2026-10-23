#!/usr/bin/env bash
# Nightly encrypted off-droplet backup of the live database (DESIGN.md §6, the same as
# money-hub's). Runs on the droplet as the table user, from
# deploy/systemd/table-backup.timer; the unit loads /opt/table/.env, which supplies the
# settings below. Run it by hand the same way:
#
#   systemctl start table-backup && journalctl -u table-backup -n 20
#
#   1. Snapshot with SQLite's .backup (WAL-safe, unlike cp) and check its integrity.
#   2. gzip, then encrypt to BACKUP_AGE_RECIPIENT with age. Only the matching private
#      key, kept off the droplet, can decrypt: R2, and anyone who later gets a shell on
#      the droplet, sees ciphertext only.
#   3. Upload to R2 as daily/table-<UTC timestamp>.db.gz.age. Old copies are
#      expired by the bucket's lifecycle rule, not by this script, so its R2 token never
#      needs delete rights to do its job.
#
# Needs: sqlite3, age, rclone (apt install sqlite3 age rclone).
# Settings (in .env): R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET,
# BACKUP_AGE_RECIPIENT (an age1... public key).

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DB="$APP_DIR/data/table.db"

for var in R2_ACCOUNT_ID R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET BACKUP_AGE_RECIPIENT; do
  [ -n "${!var:-}" ] || { echo "backup: $var is not set (see scripts/backup.sh)" >&2; exit 1; }
done

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="table-$stamp.db.gz.age"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

sqlite3 "$DB" ".backup '$work/table.db'"
check="$(sqlite3 "$work/table.db" 'PRAGMA integrity_check')"
[ "$check" = ok ] || { echo "backup: integrity_check failed: $check" >&2; exit 1; }

gzip -9 "$work/table.db"
age -r "$BACKUP_AGE_RECIPIENT" -o "$work/$name" "$work/table.db.gz"
rm -f "$work/table.db.gz"

# rclone reads its remote from these variables, so the credentials never land in a
# config file or on a command line where `ps` could show them. RCLONE_CONFIG=/dev/null
# stops rclone looking for ~/.rclone.conf, and the cache dir moves out of ~, both of
# which the unit's ProtectHome hides.
export RCLONE_CONFIG=/dev/null RCLONE_CACHE_DIR="$work"
export RCLONE_CONFIG_R2_TYPE=s3
export RCLONE_CONFIG_R2_PROVIDER=Cloudflare
export RCLONE_CONFIG_R2_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2_ENDPOINT="https://$R2_ACCOUNT_ID.r2.cloudflarestorage.com"
export RCLONE_CONFIG_R2_NO_CHECK_BUCKET=true

rclone copyto "$work/$name" "r2:$R2_BUCKET/daily/$name"
echo "backup: uploaded daily/$name ($(stat -c %s "$work/$name") bytes)"
