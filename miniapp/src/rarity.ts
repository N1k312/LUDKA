// Цвет редкости приза и оформление карточек игр.
// Гость не видит шансов: только цвет — красный очень редкий, синий редкий, зелёный обычный.

import type { GameType, Rarity, Sector } from "./api";

export const RARITY_LABEL: Record<Rarity, string> = {
  legendary: "Очень редкий",
  rare: "Редкий",
  common: "Обычный",
  empty: "Пусто",
};

/** Два оттенка на редкость — соседние секторы колеса не сливаются. */
export const RARITY_FILL: Record<Rarity, [string, string]> = {
  legendary: ["#d91f3c", "#ff3d5a"],
  rare: ["#2563eb", "#3b82ff"],
  common: ["#16a34a", "#22c55e"],
  empty: ["#2b2f42", "#363b52"],
};

export const rarityOf = (s: Pick<Sector, "rarity" | "is_empty">): Rarity => s.rarity ?? (s.is_empty ? "empty" : "common");

export const GAME_META: Record<GameType, { icon: string; sub: string; color: string; action: string }> = {
  wheel: { icon: "🎡", sub: "Крути и выигрывай", color: "#7c5cff", action: "🎡 Крутить колесо" },
  cups: { icon: "🥤", sub: "Выбери 1 из 6", color: "#ff7a45", action: "" },
  case: { icon: "📦", sub: "Открой кейс с призами", color: "#22c55e", action: "📦 Открыть кейс" },
};

/** Призы по редкости: от редких к обычным, пустые в конце. */
export function byRarity<T extends Pick<Sector, "rarity" | "is_empty">>(items: T[]): T[] {
  const order: Rarity[] = ["legendary", "rare", "common", "empty"];
  return [...items].sort((a, b) => order.indexOf(rarityOf(a)) - order.indexOf(rarityOf(b)));
}
