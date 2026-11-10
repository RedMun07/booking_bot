#!/usr/bin/env bash
# Ежедневный бэкап базы бота. Добавить в cron:
#   sudo crontab -e
#   15 4 * * * /opt/booking_bot/deploy/backup.sh >> /var/log/booking-bot-backup.log 2>&1
set -euo pipefail

APP_DIR="/opt/booking_bot"
BACKUP_DIR="/var/backups/booking_bot"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d_%H%M)"
DEST="$BACKUP_DIR/studio_$STAMP.db"

# .backup — безопасно даже при работающем боте (не блокирует запись)
sqlite3 "$APP_DIR/studio.db" ".backup '$DEST'"
gzip -f "$DEST"

find "$BACKUP_DIR" -name 'studio_*.db.gz' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') backup: $DEST.gz ($(du -h "$DEST.gz" | cut -f1))"
