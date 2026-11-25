// Иконки и картинки интерфейса гостя — свои SVG вместо эмодзи: выглядят одинаково
// на любом телефоне и не похожи на шаблон. Цвет — currentColor (наследуется от текста).

import type { ReactNode } from "react";
import type { GameType } from "./api";

export type IconName =
  | "gamepad" | "gift" | "rules" | "volume" | "mute" | "offline" | "lock" | "alert" | "pause"
  | "tool" | "clock" | "back" | "qr" | "check" | "phone" | "x" | "trophy" | "retry";

const PATHS: Record<IconName, ReactNode> = {
  gamepad: (
    <>
      <path d="M7 6h10a5 5 0 0 1 4.9 6l-.8 4a3 3 0 0 1-5.2 1.4L14 15h-4l-1.9 2.4A3 3 0 0 1 2.9 16l-.8-4A5 5 0 0 1 7 6z" />
      <path d="M6 11h4M8 9v4" />
      <circle cx="15.5" cy="10.5" r=".9" fill="currentColor" stroke="none" />
      <circle cx="17.5" cy="13" r=".9" fill="currentColor" stroke="none" />
    </>
  ),
  gift: (
    <>
      <rect x="3" y="8" width="18" height="4" rx="1" />
      <path d="M5 12v8a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-8M12 8v13" />
      <path d="M12 8S10.5 3 8 3.5 6.5 8 12 8zM12 8s1.5-5 4-4.5S17.5 8 12 8z" />
    </>
  ),
  rules: (
    <>
      <path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </>
  ),
  volume: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />
    </>
  ),
  mute: (
    <>
      <path d="M11 5 6 9H3v6h3l5 4z" />
      <path d="m16 9 5 6M21 9l-5 6" />
    </>
  ),
  offline: (
    <>
      <path d="M3 3l18 18" />
      <path d="M8.5 16.4a5 5 0 0 1 7 0M5 12.9a10 10 0 0 1 5-2.7M19 12.9a10 10 0 0 0-2.4-1.6M2 8.8a15 15 0 0 1 4.2-2.6M10.7 5.1A15 15 0 0 1 22 8.8" />
      <circle cx="12" cy="20" r=".9" fill="currentColor" stroke="none" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="11" width="16" height="10" rx="2" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </>
  ),
  alert: (
    <>
      <path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  pause: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M10 9v6M14 9v6" />
    </>
  ),
  tool: <path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L3 17.8V21h3.2l6.3-6.3a4 4 0 0 0 5.2-5.4l-2.6 2.6-2.4-.6-.6-2.4z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  back: <path d="m15 18-6-6 6-6" />,
  qr: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <path d="M14 14h3v3h-3zM20 14v.01M14 20h.01M17 20h4v-3" />
    </>
  ),
  check: <path d="m5 12 5 5 9-10" />,
  phone: (
    <>
      <rect x="7" y="2" width="10" height="20" rx="2" />
      <path d="M11 18h2" />
    </>
  ),
  x: <path d="M6 6l12 12M18 6 6 18" />,
  trophy: (
    <>
      <path d="M8 21h8M12 17v4M7 4h10v5a5 5 0 0 1-10 0z" />
      <path d="M7 6H4a3 3 0 0 0 3 4M17 6h3a3 3 0 0 1-3 4" />
    </>
  ),
  retry: (
    <>
      <path d="M3 12a9 9 0 1 0 3-6.7L3 8" />
      <path d="M3 3v5h5" />
    </>
  ),
};

export function Icon({ name, size = 20, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={`icon ${className ?? ""}`}
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}

/** Значок приза: подарок или «пусто». Цвет — от редкости (класс rarity-* у родителя). */
export function PrizeIcon({ empty, size = 22 }: { empty: boolean; size?: number }) {
  return <Icon name={empty ? "x" : "gift"} size={size} className="prize-icon" />;
}

// --- Картинки игр -------------------------------------------------------------------------

function WheelArt({ size }: { size: number }) {
  const colors = ["#ff3d5a", "#22c55e", "#3b82ff", "#22c55e", "#7c5cff", "#22c55e"];
  const r = 20;
  const sector = (i: number) => {
    const a1 = ((i * 60 - 90) * Math.PI) / 180;
    const a2 = (((i + 1) * 60 - 90) * Math.PI) / 180;
    return `M24 26 L${24 + r * Math.cos(a1)} ${26 + r * Math.sin(a1)} A${r} ${r} 0 0 1 ${24 + r * Math.cos(a2)} ${26 + r * Math.sin(a2)} Z`;
  };
  return (
    <svg viewBox="0 0 48 48" width={size} height={size} aria-hidden="true">
      <circle cx="24" cy="26" r="22" fill="#0f111a" />
      {colors.map((c, i) => <path key={i} d={sector(i)} fill={c} stroke="#0f111a" strokeWidth="1" />)}
      <circle cx="24" cy="26" r="5" fill="#0f111a" stroke="#ffffff" strokeWidth="1.5" />
      <path d="M20 1h8l-4 8z" fill="#ffffff" />
    </svg>
  );
}

export function CupArt({ size, id = "cup-shade" }: { size?: number; id?: string }) {
  return (
    <svg viewBox="0 0 80 100" width={size ? size * 0.8 : undefined} height={size} aria-hidden="true">
      <path d="M10 10 H70 L61 94 Q40 100 19 94 Z" style={{ fill: "var(--accent)" }} />
      <path d="M10 10 H70 L61 94 Q40 100 19 94 Z" fill={`url(#${id})`} />
      <path d="M16 14 H26 L22 88 H19 Z" fill="#ffffff" opacity="0.22" />
      <path d="M13 34 H67 L66 42 H14 Z" fill="#ffffff" opacity="0.85" />
      <path d="M14.5 50 H65.5 L64.7 56 H15.3 Z" fill="#ffffff" opacity="0.5" />
      <rect x="5" y="3" width="70" height="11" rx="5.5" style={{ fill: "var(--accent)" }} />
      <rect x="5" y="3" width="70" height="11" rx="5.5" fill="#ffffff" opacity="0.28" />
      <defs>
        <linearGradient id={id} x1="0" x2="1">
          <stop offset="0" stopColor="#000" stopOpacity="0" />
          <stop offset="0.65" stopColor="#000" stopOpacity="0.08" />
          <stop offset="1" stopColor="#000" stopOpacity="0.38" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/** Кейс: ящик с крышкой и замком. */
export function CaseArt({ size }: { size: number }) {
  return (
    <svg viewBox="0 0 64 56" width={size} height={size * 0.875} aria-hidden="true">
      <rect x="4" y="20" width="56" height="32" rx="5" fill="#1d6b45" />
      <rect x="4" y="20" width="56" height="32" rx="5" fill="url(#case-shine)" />
      <path d="M8 6h48a4 4 0 0 1 4 4v10H4V10a4 4 0 0 1 4-4z" fill="#22c55e" />
      <path d="M4 20h56" stroke="#0f111a" strokeWidth="2" />
      <path d="M14 6v46M50 6v46" stroke="#0f111a" strokeOpacity="0.35" strokeWidth="3" />
      <rect x="26" y="15" width="12" height="13" rx="2.5" fill="#f5c542" stroke="#0f111a" strokeWidth="1.5" />
      <circle cx="32" cy="21.5" r="1.8" fill="#0f111a" />
      <defs>
        <linearGradient id="case-shine" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#fff" stopOpacity="0.12" />
          <stop offset="1" stopColor="#000" stopOpacity="0.25" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export function GameArt({ type, size = 40 }: { type: GameType; size?: number }) {
  if (type === "wheel") return <WheelArt size={size} />;
  if (type === "cups") return <CupArt size={size} id="cup-shade-lobby" />;
  return <CaseArt size={size} />;
}
