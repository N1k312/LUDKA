// «Стаканчики»: шесть стаканчиков стоят на месте, гость выбирает один.
// Содержимое всех шести разыгрывает сервер в момент выбора; здесь только показ:
// сначала поднимается выбранный, потом остальные — видно, что было под каждым.

import { useState } from "react";
import type { Sector, SpinResult } from "./api";
import { rarityOf } from "./rarity";
import { haptic, reducedMotion } from "./telegram";

const COUNT = 6;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

interface Props {
  disabled: boolean;
  /** Отправить выбор на сервер; null — отказ (лимит, ошибка). */
  onPick: (index: number) => Promise<SpinResult | null>;
  onRevealed: (result: SpinResult) => void;
}

export function Cups({ disabled, onPick, onRevealed }: Props) {
  const [chosen, setChosen] = useState<number | null>(null);
  const [layout, setLayout] = useState<Sector[] | null>(null);
  const [up, setUp] = useState<Set<number>>(new Set());

  const pick = async (index: number) => {
    if (disabled || chosen !== null) return;
    haptic.impact("light");
    setChosen(index);
    const res = await onPick(index);
    if (!res) {
      setChosen(null);
      return;
    }
    const calm = reducedMotion();
    setLayout(res.layout ?? Array.from({ length: COUNT }, () => res.prize));
    await sleep(calm ? 50 : 250);
    setUp(new Set([index]));
    haptic.notify(res.prize.is_empty ? "warning" : "success");
    await sleep(calm ? 300 : 1100);
    setUp(new Set(Array.from({ length: COUNT }, (_, i) => i)));
    await sleep(calm ? 300 : 1300);
    onRevealed(res);
  };

  return (
    <>
      <div className="cups" role="group" aria-label="Стаканчики">
        {Array.from({ length: COUNT }, (_, i) => {
          const item = layout?.[i];
          const isUp = up.has(i);
          return (
            <button
              key={i}
              className={`cup ${isUp ? "cup-up" : ""} ${chosen === i ? "cup-chosen" : ""}`}
              disabled={disabled || chosen !== null}
              onClick={() => pick(i)}
              aria-label={item && isUp ? `Стаканчик ${i + 1}: ${item.title}` : `Стаканчик ${i + 1}`}
            >
              {item && (
                <span className={`cup-prize rarity-${rarityOf(item)}`}>
                  <b>{item.is_empty ? "✖️" : item.emoji ?? "🎁"}</b>
                  {item.is_empty ? "Пусто" : item.title}
                </span>
              )}
              <span className="cup-body">
                <CupArt />
              </span>
            </button>
          );
        })}
      </div>
      <div className="cups-floor" />
    </>
  );
}

function CupArt() {
  return (
    <svg viewBox="0 0 80 100" aria-hidden="true">
      <path d="M10 10 H70 L61 94 Q40 100 19 94 Z" style={{ fill: "var(--accent)" }} />
      <path d="M10 10 H70 L61 94 Q40 100 19 94 Z" fill="url(#cup-shade)" />
      <path d="M16 14 H26 L22 88 H19 Z" fill="#ffffff" opacity="0.22" />
      <path d="M13 34 H67 L66 42 H14 Z" fill="#ffffff" opacity="0.85" />
      <path d="M14.5 50 H65.5 L64.7 56 H15.3 Z" fill="#ffffff" opacity="0.5" />
      <rect x="5" y="3" width="70" height="11" rx="5.5" style={{ fill: "var(--accent)" }} />
      <rect x="5" y="3" width="70" height="11" rx="5.5" fill="#ffffff" opacity="0.28" />
      <defs>
        <linearGradient id="cup-shade" x1="0" x2="1">
          <stop offset="0" stopColor="#000" stopOpacity="0" />
          <stop offset="0.65" stopColor="#000" stopOpacity="0.08" />
          <stop offset="1" stopColor="#000" stopOpacity="0.38" />
        </linearGradient>
      </defs>
    </svg>
  );
}
