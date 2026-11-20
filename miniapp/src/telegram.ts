// Минимальные типы для telegram-web-app.js (подключён в index.html).

type HapticImpact = "light" | "medium" | "heavy" | "rigid" | "soft";
type HapticNotification = "error" | "success" | "warning";

interface BottomButton {
  isVisible: boolean;
  setText(text: string): void;
  setParams(params: { text?: string; color?: string; text_color?: string; is_active?: boolean; is_visible?: boolean }): void;
  show(): void;
  hide(): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
  onClick(cb: () => void): void;
  offClick(cb: () => void): void;
}

interface TelegramWebApp {
  initData: string;
  platform: string;
  version: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  close(): void;
  isVersionAtLeast(version: string): boolean;
  /** Отправить данные боту и закрыть Mini App (только при запуске с reply-кнопки). */
  sendData(data: string): void;
  /** Встроенный сканер QR. Вернуть true из колбэка — закрыть сканер. */
  showScanQrPopup(params: { text?: string }, callback?: (text: string) => boolean | void): void;
  closeScanQrPopup(): void;
  /** Попросить у пользователя номер телефона (Bot API 6.9+). Контакт уходит боту сообщением. */
  requestContact?(callback?: (shared: boolean) => void): void;
  /** Полноэкранный режим (Bot API 8.0+). */
  requestFullscreen?(): void;
  onEvent(event: string, handler: () => void): void;
  offEvent(event: string, handler: () => void): void;
  MainButton: BottomButton;
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void; offClick(cb: () => void): void };
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

/** Внутри Telegram initData непустой; в обычном браузере объект есть, но пустой. */
export const inTelegram = Boolean(tg?.initData);

export const supports = (version: string) => Boolean(inTelegram && tg?.isVersionAtLeast(version));

/** Вибрация — только внутри Telegram и только если поддерживается клиентом. */
export const haptic = {
  tick: () => tg?.HapticFeedback?.selectionChanged(),
  impact: (style: HapticImpact = "medium") => tg?.HapticFeedback?.impactOccurred(style),
  notify: (type: HapticNotification) => tg?.HapticFeedback?.notificationOccurred(type),
};

export const reducedMotion = () => window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

/** На телефоне — полноэкранный режим; на компьютере остаётся обычное окно. */
export function goFullscreen(): void {
  if (supports("8.0") && ["android", "ios"].includes(tg!.platform)) {
    try {
      tg!.requestFullscreen?.();
    } catch {
      /* клиент отказал — остаёмся в обычном режиме */
    }
  }
}
