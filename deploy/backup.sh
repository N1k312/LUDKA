#!/bin/sh
# Ежедневный бэкап PostgreSQL: сразу при запуске, затем раз в сутки.
# Файлы bot-ГГГГ-ММ-ДД_ЧЧММ.sql.gz (база) и media-….tar.gz (картинки клубов) в /backups,
# старше KEEP_DAYS дней удаляются.
# Восстановление: gunzip -c файл.sql.gz | psql -h localhost -U <user> -d <db>
set -u

while true; do
  # После перезагрузки контейнеры стартуют в любом порядке — ждём, пока база примет подключения.
  until pg_isready -q; do sleep 3; done

  name="bot-$(date -u +%Y-%m-%d_%H%M).sql.gz"
  dump="/backups/.dump.sql"
  # Код возврата pg_dump проверяем отдельно (в «pg_dump | gzip» он теряется),
  # и пустой дамп за успех не считаем.
  if pg_dump --no-owner --clean --if-exists -f "$dump" && [ -s "$dump" ] \
      && gzip -c "$dump" > "/backups/$name.tmp"; then
    mv "/backups/$name.tmp" "/backups/$name"
    echo "$(date -u) backup ok: $name"
  else
    rm -f "/backups/$name.tmp"
    echo "$(date -u) backup FAILED" >&2
  fi
  rm -f "$dump"

  if [ -d /media ] && [ -n "$(ls -A /media 2>/dev/null)" ]; then
    tar -czf "/backups/media-$(date -u +%Y-%m-%d_%H%M).tar.gz" -C /media . && echo "$(date -u) media backup ok"
  fi
  find /backups \( -name 'bot-*.sql.gz' -o -name 'media-*.tar.gz' \) -mtime +"${KEEP_DAYS:-14}" -delete
  sleep 86400
done
