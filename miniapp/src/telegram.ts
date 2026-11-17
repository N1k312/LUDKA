// Минимальные типы для telegram-web-app.js (подключён в index.html).

type HapticImpact = "light" | "medium" | "heavy" | "rigid" | "soft";
type HapticNotification = "error" | "success" | "warning";

interface TelegramWebApp {
  initData: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  close(): void;
  /** Отправить данные боту и закрыть Mini App (только при запуске с reply-кнопки). */
  sendData(data: string): void;
  /** Встроенный сканер QR. Вернуть true из колбэка — закрыть сканер. */
  showScanQrPopup(params: { text?: string }, callback?: (text: string) => boolean | void): void;
  closeScanQrPopup(): void;
  onEvent(event: "scanQrPopupClosed", handler: () => void): void;
  offEvent(event: "scanQrPopupClosed", handler: () => void): void;
  isVersionAtLeast(version: string): boolean;
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
