// Режим сканера для админа клуба (/app/?bot=..&scan=1, открывается кнопкой в админ-панели бота).
// Сканирует QR промокода встроенным сканером Telegram и отправляет код боту через sendData;
// проверку и погашение делает бот — у Mini App здесь нет доступа к промокодам.

import { useEffect, useState } from "react";
import { haptic, tg } from "./telegram";

const CODE = /^\s*(?:GG[\s-]?)?[A-Z0-9]{6}\s*$/i;
// Сканер открывается автоматически один раз (StrictMode в dev вызывает эффект дважды).
let autoOpened = false;

export function Scanner() {
  const [closed, setClosed] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const supported = !!tg && tg.isVersionAtLeast("6.4");

  const scan = () => {
    if (!tg || !supported) return;
    setClosed(false);
    setHint(null);
    tg.showScanQrPopup({ text: "Наведите камеру на QR-код промокода" }, (text) => {
      if (!CODE.test(text)) {
        haptic.notify("error");
        setHint("Это не QR промокода. Попробуйте ещё раз.");
        return true;
      }
      haptic.notify("success");
      tg?.sendData(text.trim());
      return true;
    });
  };

  useEffect(() => {
    if (!tg || !supported) return;
    tg.ready();
    const onClosed = () => setClosed(true);
    tg.onEvent("scanQrPopupClosed", onClosed);
    if (!autoOpened) {
      autoOpened = true;
      scan();
    }
    return () => tg?.offEvent("scanQrPopupClosed", onClosed);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="screen">
      <div className="notice">
        <div className="notice-emoji">📷</div>
        <h2>Сканер промокодов</h2>
        {!supported ? (
          <p>Сканер доступен только в мобильном Telegram. Отправьте промокод боту сообщением.</p>
        ) : (
          <>
            {hint && <p className="error-text">{hint}</p>}
            {(closed || hint) && <button className="button" onClick={scan}>Сканировать</button>}
            <button className="button button-secondary" onClick={() => tg?.close()}>Закрыть</button>
          </>
        )}
      </div>
    </div>
  );
}
