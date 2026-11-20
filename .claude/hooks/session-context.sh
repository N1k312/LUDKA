#!/usr/bin/env bash
# Сводка проекта в начале каждой сессии Claude (хук SessionStart, .claude/settings.json).
# Только чтение, укладывается в несколько секунд. Вывод попадает в контекст Claude.

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || exit 0

echo "[Сводка проекта на $(date '+%Y-%m-%d %H:%M')]"
echo
echo "Последние коммиты:"
git log -5 --format='  %h %ad %s' --date=short 2>/dev/null

changes=$(git status --short 2>/dev/null)
echo
if [ -n "$changes" ]; then
  echo "Незакоммиченные изменения ($(echo "$changes" | wc -l | tr -d ' ') файлов):"
  echo "$changes" | head -15 | sed 's/^/  /'
else
  echo "Незакоммиченных изменений нет."
fi

echo
if command -v docker >/dev/null 2>&1 && timeout 5 docker info >/dev/null 2>&1; then
  echo "Контейнеры рабочего стека:"
  timeout 8 docker compose -f docker-compose.yml -f deploy/compose.server.yml ps \
    --format '  {{.Service}}: {{.Status}}' 2>/dev/null || echo "  (не удалось получить список)"
else
  echo "Docker не запущен — платформа сейчас НЕ работает (см. docs/operations.md)."
fi

tunnel=$(curl -s -m 2 http://127.0.0.1:2000/quicktunnel 2>/dev/null | grep -oE '[a-z0-9-]+\.trycloudflare\.com')
if [ -n "$tunnel" ]; then
  echo "Адрес туннеля: https://$tunnel"
else
  echo "Туннель рабочего стека не отвечает."
fi

echo
echo "Открытые пункты plan.md:"
grep -n '^- \[ \]' plan.md 2>/dev/null | head -12 | sed 's/^/  /'

echo
echo "Перед работой открой документы по теме задачи — таблица в CLAUDE.md (docs/*.md)."
exit 0
