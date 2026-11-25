// «Кейс»: лента призов прокручивается и останавливается на выигрыше.
// Выигрыш уже выбран сервером; остальные предметы ленты — только для вида
// (обычные чаще, редкие реже — как подсказывает цвет).

import { forwardRef, useImperativeHandle, useRef, useState } from "react";
import type { Rarity, Sector } from "./api";
import { CaseArt, PrizeIcon } from "./Icon";
import { RARITY_LABEL, byRarity, rarityOf } from "./rarity";
import { tick as soundTick } from "./sound";
import { haptic, reducedMotion } from "./telegram";

const ITEM = 100; // ширина предмета вместе с отступом (см. .reel-item)
const COUNT = 48;
const WIN = 42;
const LOOK: Record<Rarity, number> = { common: 8, empty: 6, rare: 3, legendary: 1 };

export interface CaseHandle {
  /** Прокрутить ленту до выигрыша; промис — когда лента остановилась. */
  roll(prize: Sector): Promise<void>;
}

function decorative(prizes: Sector[]): Sector {
  const total = prizes.reduce((s, p) => s + LOOK[rarityOf(p)], 0);
  let point = Math.random() * total;
  for (const p of prizes) {
    point -= LOOK[rarityOf(p)];
    if (point < 0) return p;
  }
  return prizes[0];
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 4);

export const CaseGame = forwardRef<CaseHandle, { prizes: Sector[] }>(function CaseGame({ prizes }, ref) {
  const stripRef = useRef<HTMLDivElement>(null);
  const reelRef = useRef<HTMLDivElement>(null);
  const [items, setItems] = useState<Sector[] | null>(null);
  const [won, setWon] = useState(false);

  useImperativeHandle(ref, () => ({
    roll(prize: Sector) {
      const strip = Array.from({ length: COUNT }, (_, i) => (i === WIN ? prize : decorative(prizes)));
      setWon(false);
      setItems(strip);
      return new Promise<void>((resolve) => {
        requestAnimationFrame(() => {
          const el = stripRef.current;
          const box = reelRef.current?.clientWidth ?? 320;
          if (!el) return resolve();
          const jitter = (Math.random() - 0.5) * (ITEM * 0.6);
          const end = -(WIN * ITEM + (ITEM - 8) / 2 - box / 2 + jitter);
          const calm = reducedMotion();
          const duration = calm ? 1200 : 6200;
          const t0 = performance.now();
          let last = 0;
          const frame = (now: number) => {
            const t = Math.min(1, (now - t0) / duration);
            const x = end * easeOut(t);
            el.style.transform = `translateX(${x}px)`;
            const passed = Math.floor((-x + box / 2) / ITEM);
            if (passed !== last) {
              last = passed;
              haptic.tick();
              soundTick();
            }
            if (t < 1) requestAnimationFrame(frame);
            else {
              setWon(true);
              resolve();
            }
          };
          requestAnimationFrame(frame);
        });
      });
    },
  }));

  return (
    <>
      {!items && <div className="case-box" aria-hidden="true"><CaseArt size={120} /></div>}
      {items && (
        <div className="reel" ref={reelRef}>
          <div className="reel-marker" />
          <div className="reel-strip" ref={stripRef}>
            {items.map((p, i) => (
              <div key={i} className={`reel-item rarity-${rarityOf(p)} ${won && i === WIN ? "reel-win" : ""}`}>
                <PrizeIcon empty={p.is_empty} size={30} />
                {p.is_empty ? "Пусто" : p.title}
              </div>
            ))}
          </div>
        </div>
      )}
      <p className="muted game-hint">Что может выпасть:</p>
      <div className="chips">
        {byRarity(prizes).map((p) => (
          <span key={p.id} className={`chip rarity-${rarityOf(p)}`} title={RARITY_LABEL[rarityOf(p)]}>
            <PrizeIcon empty={p.is_empty} size={14} /> {p.is_empty ? "Пусто" : p.title}
          </span>
        ))}
      </div>
    </>
  );
});
