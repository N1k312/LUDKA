// «Мои призы»: активные сверху; по нажатию на активный — QR на весь экран для администратора.

import QRCode from "qrcode";
import { useEffect, useState } from "react";
import type { Prize, PrizeStatus } from "./api";
import { expiresIn, formatDate, formatDateTime } from "./format";
import { PrizeIcon } from "./Icon";
import { haptic } from "./telegram";

const STATUS: Record<PrizeStatus, string> = {
  active: "Активен",
  redeemed: "Получен",
  expired: "Истёк",
};

/** Активные — по сроку (что сгорит раньше — выше), остальные — от новых к старым. */
export function sortPrizes(prizes: Prize[]): Prize[] {
  const active = prizes.filter((p) => p.status === "active")
    .sort((a, b) => a.expires_at.localeCompare(b.expires_at));
  const rest = prizes.filter((p) => p.status !== "active")
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  return [...active, ...rest];
}

export function PrizeList({ prizes, timeZone }: { prizes: Prize[]; timeZone: string }) {
  const [shown, setShown] = useState<Prize | null>(null);

  return (
    <>
      <ul className="prizes">
        {sortPrizes(prizes).map((p) => {
          const soon = p.status === "active" && new Date(p.expires_at).getTime() - Date.now() < 3 * 86400_000;
          return (
            <li key={p.code} className={`prize prize-${p.status}`}>
              <button
                className="prize-head"
                onClick={() => {
                  haptic.impact("light");
                  setShown(p);
                }}
                disabled={p.status !== "active"}
              >
                <span className="prize-emoji"><PrizeIcon empty={false} size={24} /></span>
                <span className="prize-main">
                  <span className="prize-title">{p.title}</span>
                  <span className="prize-code">{p.code}</span>
                </span>
                <span className={`status status-${p.status}`}>{STATUS[p.status]}</span>
              </button>
              <div className={`prize-meta ${soon ? "prize-soon" : ""}`}>
                {p.status === "redeemed" && p.redeemed_at
                  ? `Получен ${formatDateTime(p.redeemed_at, timeZone)}`
                  : p.status === "expired"
                    ? `Истёк ${formatDate(p.expires_at, timeZone)}`
                    : `${expiresIn(p.expires_at)} · нажмите, чтобы показать QR`}
              </div>
            </li>
          );
        })}
      </ul>
      {shown && <QrModal prize={shown} onClose={() => setShown(null)} />}
    </>
  );
}

/** Промокод для администратора: крупный QR и код на весь экран. */
export function QrModal({ prize, onClose }: { prize: Pick<Prize, "code" | "title" | "emoji">; onClose: () => void }) {
  return (
    <div className="modal-backdrop qr-backdrop" onClick={onClose}>
      <div className="qr-sheet" onClick={(e) => e.stopPropagation()}>
        <div className="qr-title">{prize.title}</div>
        <QrImage value={prize.code} size={280} />
        <div className="code qr-code">{prize.code}</div>
        <p className="muted">Покажите экран администратору. Если сканер не видит код — прибавьте яркость.</p>
        <button className="button" onClick={onClose}>Готово</button>
      </div>
    </div>
  );
}

function QrImage({ value, size }: { value: string; size: number }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(value, { margin: 1, width: size * 2, errorCorrectionLevel: "M" }).then(setSrc, () => setSrc(null));
  }, [value, size]);
  return src ? <img className="qr" src={src} alt={`QR-код ${value}`} width={size} height={size} /> : null;
}
