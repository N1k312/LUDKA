// Клиент API бэкенда. Каждый запрос подписан initData от Telegram,
// а X-Bot-Id говорит серверу, через какого бота открыт Mini App (?bot= в адресе).

import { tg } from "./telegram";
import { mockFetch } from "./mock";

export interface Me {
  club: { name: string; timezone: string };
  guest: { first_name: string; registered: boolean };
  available: boolean;
  wheel: { enabled: boolean; cooldown_seconds: number | null; next_spin_at: string | null };
}

export interface Sector {
  id: number;
  title: string;
  emoji: string | null;
  is_empty: boolean;
}

export interface SpinResult {
  sector: Sector;
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

async function request<T>(method: "GET" | "POST", path: string): Promise<T> {
  if (isMock) return mockFetch<T>(method, path);

  const initData = tg?.initData;
  if (!initData || !botId) {
    throw new ApiError(401, "unauthorized", "Откройте игру из бота клуба");
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      method,
      headers: { Authorization: `tma ${initData}`, "X-Bot-Id": botId },
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет");
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const d = body?.detail ?? {};
    throw new ApiError(
      response.status,
      d.code ?? "error",
      d.message ?? "Что-то пошло не так, попробуйте позже",
      d.next_spin_at ?? null,
    );
  }
  return body as T;
}

export const api = {
  me: () => request<Me>("GET", "/me"),
  wheel: () => request<{ sectors: Sector[] }>("GET", "/wheel").then((r) => r.sectors),
  spin: () => request<SpinResult>("POST", "/spin"),
  prizes: () => request<{ prizes: Prize[] }>("GET", "/prizes").then((r) => r.prizes),
  rules: () => request<{ club: string; rules: string | null }>("GET", "/rules"),
};
