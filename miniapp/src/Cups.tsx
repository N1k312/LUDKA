// «Стаканчики»: шесть стаканчиков стоят на месте, гость выбирает один.
// Содержимое всех шести разыгрывает сервер в момент выбора; здесь только показ:
// сначала поднимается выбранный, потом остальные — видно, что было под каждым.

import { useState } from "react";
import type { Sector, SpinResult } from "./api";
import { CupArt, PrizeIcon } from "./Icon";
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
                  <PrizeIcon empty={item.is_empty} size={22} />
                  {item.is_empty ? "Пусто" : item.title}
                </span>
              )}
              <span className="cup-body">
                <CupArt id={`cup-shade-${i}`} />
              </span>
            </button>
          );
        })}
      </div>
      <div className="cups-floor" />
    </>
  );
}
