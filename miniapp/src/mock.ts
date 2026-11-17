// Мок бэкенда для разработки интерфейса в обычном браузере: /app/?mock
// Варианты: ?mock=unregistered, ?mock=unavailable, ?mock=cooldown.
// В production-сборку не попадает (isMock ложен вне `npm run dev`).

import type { Me, Prize, Sector, SpinResult } from "./api";
import { ApiError } from "./api";

const variant = new URLSearchParams(window.location.search).get("mock") ?? "";
const DAY = 24 * 3600 * 1000;

const sectors: Sector[] = [
  { id: 1, title: "1 час игры", emoji: "🎮", is_empty: false },
  { id: 2, title: "Энергетик", emoji: "🥤", is_empty: false },
  { id: 3, title: "Повезёт в следующий раз", emoji: "😔", is_empty: true },
  { id: 4, title: "Скидка 10%", emoji: "💸", is_empty: false },
  { id: 5, title: "2 часа игры", emoji: "🎮", is_empty: false },
  { id: 6, title: "3 часа игры", emoji: "🏆", is_empty: false },
];

const iso = (ms: number) => new Date(ms).toISOString();
let nextSpinAt: string | null = variant === "cooldown" ? iso(Date.now() + 3 * DAY + 4000) : null;

const prizes: Prize[] = [
  {
    code: "GG-7K3M9Q", title: "Энергетик", emoji: "🥤", status: "redeemed",
    created_at: iso(Date.now() - 9 * DAY), expires_at: iso(Date.now() + 5 * DAY),
    redeemed_at: iso(Date.now() - 8 * DAY),
  },
  {
    code: "GG-4XH2PB", title: "Скидка 10%", emoji: "💸", status: "expired",
    created_at: iso(Date.now() - 30 * DAY), expires_at: iso(Date.now() - 16 * DAY),
    redeemed_at: null,
  },
];

const randomCode = () =>
  "GG-" + Array.from({ length: 6 }, () => "23456789ABCDEFGHJKMNPQRSTUVWXYZ"[Math.floor(Math.random() * 31)]).join("");

export async function mockFetch<T>(method: string, path: string): Promise<T> {
  await new Promise((r) => setTimeout(r, 300));
  const key = `${method} ${path}`;

  if (key === "GET /me") {
    const me: Me = {
      club: { name: "Тестовый клуб", timezone: "Asia/Almaty" },
      guest: { first_name: "Никита", registered: variant !== "unregistered" },
      available: variant !== "unavailable",
      wheel: { enabled: true, cooldown_seconds: 7 * 24 * 3600, next_spin_at: nextSpinAt },
    };
    return me as T;
  }
  if (key === "GET /wheel") return { sectors } as T;
  if (key === "GET /prizes") return { prizes } as T;
  if (key === "GET /rules") {
    return {
      club: "Тестовый клуб",
      rules:
        "1. Крутить колесо можно один раз в 7 дней.\n2. Приз выдаётся по промокоду у администратора клуба.\n" +
        "3. Промокод действует 14 дней с момента выигрыша.\n4. Призы не обмениваются на деньги.",
    } as T;
  }
  if (key === "POST /spin") {
    if (nextSpinAt) throw new ApiError(429, "cooldown", "Следующий спин ещё недоступен", nextSpinAt);
    const sector = sectors[Math.floor(Math.random() * sectors.length)];
    nextSpinAt = iso(Date.now() + 7 * DAY);
    const code = sector.is_empty ? null : { code: randomCode(), expires_at: iso(Date.now() + 14 * DAY) };
    if (code) {
      prizes.unshift({
        code: code.code, title: sector.title, emoji: sector.emoji, status: "active",
        created_at: iso(Date.now()), expires_at: code.expires_at, redeemed_at: null,
      });
    }
    const result: SpinResult = { sector, code, next_spin_at: nextSpinAt };
    return result as T;
  }
  throw new ApiError(404, "not_found", "Не найдено");
}
