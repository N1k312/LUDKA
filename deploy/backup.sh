#!/bin/sh
# Ежедневный бэкап PostgreSQL: сразу при запуске, затем раз в сутки.
# Файлы bot-ГГГГ-ММ-ДД_ЧЧММ.sql.gz в /backups, старше KEEP_DAYS дней удаляются.
# Восстановление: gunzip -c файл.sql.gz | psql -h localhost -U <user> -d <db>
set -u

while true; do
  name="bot-$(date -u +%Y-%m-%d_%H%M).sql.gz"
  if pg_dump --no-owner --clean --if-exists | gzip > "/backups/$name.tmp"; then
    mv "/backups/$name.tmp" "/backups/$name"
    echo "$(date -u) backup ok: $name"
  else
    rm -f "/backups/$name.tmp"
    echo "$(date -u) backup FAILED" >&2
  fi
  find /backups -name 'bot-*.sql.gz' -mtime +"${KEEP_DAYS:-14}" -delete
  sleep 86400
done
