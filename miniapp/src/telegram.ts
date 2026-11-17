// Минимальные типы для telegram-web-app.js (подключён в index.html).

type HapticImpact = "light" | "medium" | "heavy" | "rigid" | "soft";
type HapticNotification = "error" | "success" | "warning";

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  close(): void;
  HapticFeedback?: {
    impactOccurred(style: HapticImpact): void;
    notificationOccurred(type: HapticNotification): void;
    selectionChanged(): void;
  };
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export const tg: TelegramWebApp | undefined = window.Telegram?.WebApp;

/** Вибрация — только внутри Telegram и только если поддерживается клиентом. */
export const haptic = {
  tick: () => tg?.HapticFeedback?.selectionChanged(),
  impact: (style: HapticImpact = "medium") => tg?.HapticFeedback?.impactOccurred(style),
  notify: (type: HapticNotification) => tg?.HapticFeedback?.notificationOccurred(type),
};
