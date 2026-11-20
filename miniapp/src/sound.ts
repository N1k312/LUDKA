// Звук трещотки колеса: короткий щелчок через WebAudio, без файлов. По умолчанию выключен
// (в клубе и так шумно); выбор гостя запоминается на этом устройстве.

const KEY = "wheel-sound";
let ctx: AudioContext | null = null;
let enabled = (() => {
  try {
    return localStorage.getItem(KEY) === "on";
  } catch {
    return false;
  }
})();

export const soundEnabled = () => enabled;

export function setSoundEnabled(on: boolean): void {
  enabled = on;
  try {
    localStorage.setItem(KEY, on ? "on" : "off");
  } catch {
    /* хранилище недоступно — настройка действует до закрытия */
  }
  if (on) unlock();
}

/** Браузеры разрешают звук только после жеста пользователя — вызываем при нажатии. */
export function unlock(): void {
  if (!enabled) return;
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
  } catch {
    ctx = null;
  }
}

export function tick(): void {
  if (!enabled || !ctx) return;
  const t = ctx.currentTime;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = "square";
  osc.frequency.setValueAtTime(1400, t);
  gain.gain.setValueAtTime(0.04, t);
  gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.03);
  osc.connect(gain).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + 0.035);
}
