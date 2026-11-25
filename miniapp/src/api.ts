// Клиент API бэкенда. Каждый запрос подписан initData от Telegram,
// а X-Bot-Id говорит серверу, через какого бота открыт Mini App (?bot= в адресе).

import { tg } from "./telegram";
import { mockFetch, mockShareContact } from "./mock";

export interface Branding {
  palette: string;
  accent: string;
  wheel: string[];
  empty: string;
  rim: string;
  hub_label: string;
  code_prefix: string;
  logo_url: string | null;
  texts: { win: string | null; lose: string | null };
  /** false — клуб не настраивал оформление: цвета кнопок берутся из темы Telegram. */
  customized: boolean;
}

export type GameType = "wheel" | "cups" | "case";

/** Редкость приза — по его доле среди призов игры. Самих шансов сервер не отдаёт. */
export type Rarity = "legendary" | "rare" | "common" | "empty";

export interface Game {
  type: GameType;
  title: string;
  enabled: boolean;
  cooldown_seconds: number;
  next_play_at: string | null;
}

export interface Me {
  club: { name: string; timezone: string };
  guest: { first_name: string; registered: boolean; has_phone: boolean; has_consent: boolean };
  available: boolean;
  games: Game[];
  wheel: { enabled: boolean; cooldown_seconds: number | null; next_spin_at: string | null };
  branding: Branding;
}

export interface Sector {
  id: number;
  title: string;
  emoji: string | null;
  is_empty: boolean;
  rarity: Rarity;
}

export interface SpinResult {
  prize: Sector;
  /** Стаканчики: что было под каждым (выбранный — под индексом выбора). */
  layout: Sector[] | null;
  code: { code: string; expires_at: string } | null;
  next_spin_at: string;
}

export type PrizeStatus = "active" | "redeemed" | "expired";

export interface Prize {
  code: string;
  title: string;
  emoji: string | null;
  status: PrizeStatus;
  created_at: string;
  expires_at: string;
  redeemed_at: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public nextSpinAt: string | null = null,
  ) {
    super(message);
  }
}

const params = new URLSearchParams(window.location.search);
const botId = params.get("bot");
/** Мок-режим: интерфейс без Telegram и бэкенда, только при `npm run dev` и ?mock. */
export const isMock = import.meta.env.DEV && params.has("mock");

async function request<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  if (isMock) return mockFetch<T>(method, path, body);

  const initData = tg?.initData;
  if (!initData || !botId) {
    throw new ApiError(401, "unauthorized", "Откройте игру из бота клуба");
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: {
        Authorization: `tma ${initData}`,
        "X-Bot-Id": botId,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const d = data?.detail ?? {};
    throw new ApiError(
      response.status,
      d.code ?? "error",
      d.message ?? "Что-то пошло не так, попробуйте позже",
      d.next_spin_at ?? null,
    );
  }
  return data as T;
}

export const api = {
  me: () => request<Me>("GET", "/me"),
  prizesOf: (game: GameType) => request<{ prizes: Sector[] }>("GET", `/games/${game}`).then((r) => r.prizes),
  play: (game: GameType, choice?: number) =>
    request<SpinResult>("POST", `/play/${game}`, choice === undefined ? undefined : { choice }),
  prizes: () => request<{ prizes: Prize[] }>("GET", "/prizes").then((r) => r.prizes),
  rules: () => request<{ club: string; rules: string | null }>("GET", "/rules"),
  consent: () => request<{ registered: boolean }>("POST", "/consent"),
};

/**
 * Попросить номер телефона: Telegram показывает системное окно и отправляет
 * собственный контакт гостя боту — бот проверяет его и сохраняет номер.
 * Возвращает false, если гость отказался или клиент Telegram слишком старый.
 */
export function shareContact(): Promise<boolean> {
  if (isMock) return mockShareContact();
  return new Promise((resolve) => {
    if (!tg?.requestContact || !tg.isVersionAtLeast("6.9")) return resolve(false);
    tg.requestContact((shared) => resolve(shared));
  });
}

export const canShareContact = () => isMock || Boolean(tg?.requestContact && tg.isVersionAtLeast("6.9"));
