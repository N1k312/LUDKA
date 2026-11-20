// Даты показываются в часовом поясе клуба (на сервере всё хранится в UTC).

export function formatDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone,
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  }).format(new Date(iso));
}

export function formatDateTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat("ru-RU", {
    timeZone,
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

const pad = (n: number) => String(n).padStart(2, "0");

/** «3 д 04:05:06» или «04:05:06». */
export function formatCountdown(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const days = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const time = `${pad(h)}:${pad(m)}:${pad(s)}`;
  return days > 0 ? `${days} д ${time}` : time;
}

/** «сгорит через 3 дня», «сгорит через 5 ч», «сгорит меньше чем через час». */
export function expiresIn(iso: string, now = Date.now()): string {
  const ms = new Date(iso).getTime() - now;
  if (ms <= 0) return "истёк";
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "сгорит меньше чем через час";
  if (hours < 24) return `сгорит через ${hours} ч`;
  const days = Math.floor(hours / 24);
  const word = days % 10 === 1 && days % 100 !== 11 ? "день"
    : [2, 3, 4].includes(days % 10) && ![12, 13, 14].includes(days % 100) ? "дня" : "дней";
  return `сгорит через ${days} ${word}`;
}
