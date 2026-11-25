// Мок бэкенда для разработки интерфейса в обычном браузере: /app/?mock
// Варианты: ?mock=unregistered, ?mock=unavailable, ?mock=cooldown, ?mock=slow (медленная загрузка),
// ?mock=offline (нет связи). Оформление клуба: &brand=neon|sunset|ocean|forest|gold (+ &logo).
// В production-сборку не попадает (isMock ложен вне `npm run dev`).

import type { Branding, Game, GameType, Me, Prize, Sector, SpinResult } from "./api";
import { ApiError } from "./api";

const params = new URLSearchParams(window.location.search);
const variant = params.get("mock") ?? "";
const DAY = 24 * 3600 * 1000;

const PALETTES: Record<string, Omit<Branding, "palette" | "hub_label" | "code_prefix" | "logo_url" | "texts" | "customized">> = {
  classic: { accent: "#7c5cff", empty: "#5b6475", rim: "#232733",
             wheel: ["#7c5cff", "#ff5c8a", "#ffb03b", "#2ec4b6", "#4d8cff", "#ff7a45"] },
  neon: { accent: "#00d4ff", empty: "#3a3f55", rim: "#0d0f1a",
          wheel: ["#00d4ff", "#ff2bd6", "#7cff4f", "#ffe600", "#7a5cff", "#ff6b2b"] },
  sunset: { accent: "#ff6b35", empty: "#6d597a", rim: "#2b1b2e",
            wheel: ["#ff6b35", "#f7c548", "#e84855", "#f49d37", "#c2185b", "#ff9f80"] },
  ocean: { accent: "#1e88e5", empty: "#546e7a", rim: "#0f2233",
           wheel: ["#1e88e5", "#00acc1", "#3949ab", "#26a69a", "#4fc3f7", "#5c6bc0"] },
  forest: { accent: "#2e7d32", empty: "#6d6d5f", rim: "#1b2a1c",
            wheel: ["#2e7d32", "#66bb6a", "#00796b", "#c0ca33", "#8d6e63", "#43a047"] },
  gold: { accent: "#c9a227", empty: "#4a4a4a", rim: "#111111",
          wheel: ["#c9a227", "#2b2b2b", "#e0b84c", "#3d3d3d", "#a8841a", "#575757"] },
};
const brandKey = params.get("brand") ?? "";
const LOGO = "data:image/svg+xml," + encodeURIComponent(
  "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='#111'/>" +
  "<text x='32' y='42' font-size='28' font-family='Arial' font-weight='700' text-anchor='middle' fill='#00d4ff'>CA</text></svg>");

const branding: Branding = {
  palette: PALETTES[brandKey] ? brandKey : "classic",
  ...(PALETTES[brandKey] ?? PALETTES.classic),
  hub_label: brandKey ? "CA" : "GG",
  code_prefix: brandKey ? "CA" : "GG",
  logo_url: params.has("logo") ? LOGO : null,
  texts: brandKey
    ? { win: "Красава! Забирай приз у админа Cyber Arena 💪", lose: "Не в этот раз — ждём тебя через неделю!" }
    : { win: null, lose: null },
  customized: Boolean(brandKey),
};

const sectors: Sector[] = [
  { id: 1, title: "1 час игры", emoji: "🎮", is_empty: false, rarity: "common" },
  { id: 2, title: "Энергетик", emoji: "🥤", is_empty: false, rarity: "common" },
  { id: 3, title: "Повезёт в следующий раз", emoji: "😔", is_empty: true, rarity: "empty" },
  { id: 4, title: "Скидка 10%", emoji: "💸", is_empty: false, rarity: "rare" },
  { id: 5, title: "2 часа игры", emoji: "🎮", is_empty: false, rarity: "rare" },
  { id: 6, title: "3 часа игры", emoji: "🏆", is_empty: false, rarity: "legendary" },
];
const cupPrizes: Sector[] = [
  { id: 11, title: "Чипсы", emoji: "🍟", is_empty: false, rarity: "common" },
  { id: 12, title: "Кола", emoji: "🥤", is_empty: false, rarity: "common" },
  { id: 13, title: "Час игры", emoji: "🎮", is_empty: false, rarity: "rare" },
  { id: 14, title: "Пусто", emoji: null, is_empty: true, rarity: "empty" },
];
const casePrizes: Sector[] = [
  { id: 21, title: "Энергетик", emoji: "⚡", is_empty: false, rarity: "common" },
  { id: 22, title: "Скидка 20%", emoji: "💸", is_empty: false, rarity: "common" },
  { id: 23, title: "2 часа игры", emoji: "🎮", is_empty: false, rarity: "rare" },
  { id: 24, title: "Ночь в VIP", emoji: "👑", is_empty: false, rarity: "legendary" },
  { id: 25, title: "Пусто", emoji: null, is_empty: true, rarity: "empty" },
];
const PRIZES: Record<GameType, Sector[]> = { wheel: sectors, cups: cupPrizes, case: casePrizes };
const TITLES: Record<GameType, string> = { wheel: "Колесо", cups: "Стаканчики", case: "Кейс" };
const LOOK = { common: 8, empty: 6, rare: 3, legendary: 1 } as const;
const draw = (list: Sector[]) => {
  const total = list.reduce((s, p) => s + LOOK[p.rarity], 0);
  let r = Math.random() * total;
  for (const p of list) if ((r -= LOOK[p.rarity]) < 0) return p;
  return list[0];
};

const iso = (ms: number) => new Date(ms).toISOString();
const nextAt: Record<GameType, string | null> = {
  wheel: variant === "cooldown" ? iso(Date.now() + 3 * DAY + 4000) : null,
  cups: variant === "cooldown" ? iso(Date.now() + 5 * 3600_000) : null,
  case: null,
};
let hasConsent = variant !== "unregistered";
let hasPhone = variant !== "unregistered";

const prizes: Prize[] = [
  {
    code: `${branding.code_prefix}-7K3M9Q`, title: "Энергетик", emoji: "🥤", status: "redeemed",
    created_at: iso(Date.now() - 9 * DAY), expires_at: iso(Date.now() + 5 * DAY),
    redeemed_at: iso(Date.now() - 8 * DAY),
  },
  {
    code: `${branding.code_prefix}-9QW2TR`, title: "1 час игры", emoji: "🎮", status: "active",
    created_at: iso(Date.now() - 12 * DAY), expires_at: iso(Date.now() + 2 * DAY - 3600_000),
    redeemed_at: null,
  },
  {
    code: `${branding.code_prefix}-4XH2PB`, title: "Скидка 10%", emoji: "💸", status: "expired",
    created_at: iso(Date.now() - 30 * DAY), expires_at: iso(Date.now() - 16 * DAY),
    redeemed_at: null,
  },
];

const randomCode = () =>
  `${branding.code_prefix}-` +
  Array.from({ length: 6 }, () => "23456789ABCDEFGHJKMNPQRSTUVWXYZ"[Math.floor(Math.random() * 31)]).join("");

/** Имитация: гость поделился номером, бот его сохранил. */
export async function mockShareContact(): Promise<boolean> {
  await new Promise((r) => setTimeout(r, 600));
  hasPhone = true;
  return true;
}

export async function mockFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
  await new Promise((r) => setTimeout(r, variant === "slow" ? 2500 : 300));
  if (variant === "offline") throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет");
  const key = `${method} ${path}`;

  if (key === "GET /me") {
    const me: Me = {
      club: { name: brandKey ? "Cyber Arena" : "Тестовый клуб", timezone: "Asia/Almaty" },
      guest: { first_name: "Никита", registered: hasConsent && hasPhone, has_phone: hasPhone, has_consent: hasConsent },
      available: variant !== "unavailable",
      games: (["wheel", "cups", "case"] as GameType[]).map((type): Game => ({
        type, title: TITLES[type], enabled: true, cooldown_seconds: 7 * 24 * 3600, next_play_at: nextAt[type],
      })),
      wheel: { enabled: true, cooldown_seconds: 7 * 24 * 3600, next_spin_at: nextAt.wheel },
      branding,
    };
    return me as T;
  }
  if (key === "POST /consent") {
    hasConsent = true;
    return { registered: hasConsent && hasPhone } as T;
  }
  if (key === "GET /wheel") return { sectors } as T;
  const gameMatch = path.match(/^\/games\/(wheel|cups|case)$/);
  if (method === "GET" && gameMatch) return { prizes: PRIZES[gameMatch[1] as GameType] } as T;
  if (key === "GET /prizes") return { prizes } as T;
  if (key === "GET /rules") {
    return {
      club: "Тестовый клуб",
      rules:
        "1. Крутить колесо можно один раз в 7 дней.\n2. Приз выдаётся по промокоду у администратора клуба.\n" +
        "3. Промокод действует 14 дней с момента выигрыша.\n4. Призы не обмениваются на деньги.",
    } as T;
  }
  const playMatch = path.match(/^\/play\/(wheel|cups|case)$/);
  if (method === "POST" && playMatch) {
    const game = playMatch[1] as GameType;
    const waitUntil = nextAt[game];
    if (waitUntil) throw new ApiError(429, "cooldown", "Следующая игра ещё недоступна", waitUntil);
    const list = PRIZES[game];
    const choice = (body as { choice?: number } | undefined)?.choice ?? 0;
    const layout = game === "cups" ? Array.from({ length: 6 }, () => draw(list)) : null;
    const prize = layout ? layout[choice] : draw(list);
    nextAt[game] = iso(Date.now() + 7 * DAY);
    const code = prize.is_empty ? null : { code: randomCode(), expires_at: iso(Date.now() + 14 * DAY) };
    if (code) {
      prizes.unshift({
        code: code.code, title: prize.title, emoji: prize.emoji, status: "active",
        created_at: iso(Date.now()), expires_at: code.expires_at, redeemed_at: null,
      });
    }
    const result: SpinResult = { prize, layout, code, next_spin_at: nextAt[game]! };
    return result as T;
  }
  throw new ApiError(404, "not_found", "Не найдено");
}
