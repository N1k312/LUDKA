// «Мои призы»: промокоды со статусами; у активного — QR для сканера администратора.

import QRCode from "qrcode";
import { useEffect, useState } from "react";
import type { Prize, PrizeStatus } from "./api";

const STATUS: Record<PrizeStatus, string> = {
  active: "Активен",
  redeemed: "Получен",
  expired: "Истёк",
};

export function PrizeList({
  prizes,
  timeZone,
  formatDate,
  formatDateTime,
}: {
  prizes: Prize[];
  timeZone: string;
  formatDate: (iso: string, tz: string) => string;
  formatDateTime: (iso: string, tz: string) => string;
}) {
  const [open, setOpen] = useState<string | null>(prizes.find((p) => p.status === "active")?.code ?? null);

  return (
    <ul className="prizes">
      {prizes.map((p) => {
        const isOpen = open === p.code && p.status === "active";
        return (
          <li key={p.code} className={`prize prize-${p.status}`}>
            <button
              className="prize-head"
              onClick={() => setOpen(isOpen ? null : p.code)}
              disabled={p.status !== "active"}
            >
              <span className="prize-emoji">{p.emoji ?? "🎁"}</span>
              <span className="prize-main">
                <span className="prize-title">{p.title}</span>
                <span className="prize-code">{p.code}</span>
              </span>
              <span className={`status status-${p.status}`}>{STATUS[p.status]}</span>
            </button>
            <div className="prize-meta">
              {p.status === "redeemed" && p.redeemed_at
                ? `Получен ${formatDateTime(p.redeemed_at, timeZone)}`
                : p.status === "expired"
                  ? `Истёк ${formatDate(p.expires_at, timeZone)}`
                  : `Действует до ${formatDate(p.expires_at, timeZone)}`}
            </div>
            {isOpen && (
              <div className="prize-qr">
                <QrImage value={p.code} />
                <p className="muted">Покажите QR или промокод администратору</p>
              </div>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function QrImage({ value }: { value: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    QRCode.toDataURL(value, { margin: 1, width: 220, errorCorrectionLevel: "M" }).then(setSrc, () => setSrc(null));
  }, [value]);
  return src ? <img className="qr" src={src} alt={`QR-код ${value}`} width={220} height={220} /> : null;
}
