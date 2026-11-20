import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  canShareContact,
  isMock,
  shareContact,
  type Branding,
  type Me,
  type Prize,
  type Sector,
  type SpinResult,
} from "./api";
import { Confetti } from "./Confetti";
import { formatCountdown, formatDate } from "./format";
import { PrizeList, QrModal } from "./Prizes";
import { setSoundEnabled, soundEnabled, unlock as unlockSound } from "./sound";
import { goFullscreen, haptic, inTelegram, tg } from "./telegram";
import { Wheel, type WheelHandle } from "./Wheel";

type Tab = "wheel" | "prizes" | "rules";

const params = new URLSearchParams(window.location.search);
const initialTab: Tab = params.get("tab") === "prizes" ? "prizes" : params.get("tab") === "rules" ? "rules" : "wheel";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Цвет кнопок клуба поверх темы Telegram — только если клуб настроил оформление. */
function applyBranding(b: Branding | undefined) {
  const root = document.documentElement.style;
  if (b?.customized) root.setProperty("--accent", b.accent);
  else root.removeProperty("--accent");
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [sound, setSound] = useState(soundEnabled());

  const load = useCallback(async () => {
    try {
      const [meData, sectorData] = await Promise.all([api.me(), api.wheel()]);
      setMe(meData);
      setSectors(sectorData);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", "Что-то пошло не так"));
    }
  }, []);

  useEffect(() => {
    tg?.ready();
    tg?.expand();
    goFullscreen();
    load();
  }, [load]);

  useEffect(() => applyBranding(me?.branding), [me?.branding]);

  // Системная кнопка «Назад» на вкладках призов и правил.
  useEffect(() => {
    const button = tg?.BackButton;
    if (!inTelegram || !button) return;
    const back = () => setTab("wheel");
    if (tab === "wheel") button.hide();
    else button.show();
    button.onClick(back);
    return () => button.offClick(back);
  }, [tab]);

  if (error) {
    const offline = error.code === "network";
    return (
      <Screen>
        <Notice emoji={offline ? "📡" : error.status === 401 ? "🔒" : "⚠️"} title={offline ? "Нет связи" : error.message}>
          {offline && <p>Проверьте интернет и попробуйте ещё раз.</p>}
          {error.status !== 401 && <button className="button" onClick={load}>Повторить</button>}
        </Notice>
      </Screen>
    );
  }
  if (!me) return <Skeleton />;

  const b = me.branding;
  return (
    <Screen>
      <header className="header">
        {b.logo_url && <img className="logo" src={b.logo_url} alt="" />}
        <div className="club-name">{me.club.name}</div>
        {isMock && <span className="badge">мок</span>}
        <button
          className="icon-button"
          aria-label={sound ? "Выключить звук колеса" : "Включить звук колеса"}
          onClick={() => {
            setSoundEnabled(!sound);
            setSound(!sound);
          }}
        >
          {sound ? "🔊" : "🔇"}
        </button>
      </header>

      <main className="content">
        {tab === "wheel" &&
          (me.guest.registered ? (
            <WheelTab me={me} sectors={sectors} onChanged={load} onShowPrizes={() => setTab("prizes")} />
          ) : (
            <Registration me={me} onChanged={load} onShowRules={() => setTab("rules")} />
          ))}
        {tab === "prizes" && <PrizesTab timeZone={me.club.timezone} />}
        {tab === "rules" && <RulesTab />}
      </main>

      <nav className="tabs">
        <TabButton active={tab === "wheel"} onClick={() => setTab("wheel")} icon="🎡" label="Колесо" />
        <TabButton active={tab === "prizes"} onClick={() => setTab("prizes")} icon="🎁" label="Мои призы" />
        <TabButton active={tab === "rules"} onClick={() => setTab("rules")} icon="📜" label="Правила" />
      </nav>
    </Screen>
  );
}

// --- Регистрация прямо в Mini App -------------------------------------------------------

function Registration({ me, onChanged, onShowRules }: { me: Me; onChanged: () => Promise<void>; onShowRules: () => void }) {
  const [busy, setBusy] = useState<"consent" | "phone" | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const { has_consent: consent, has_phone: phone } = me.guest;

  const accept = async () => {
    setBusy("consent");
    setHint(null);
    try {
      await api.consent();
      haptic.notify("success");
      await onChanged();
    } catch (e) {
      setHint(e instanceof ApiError ? e.message : "Не получилось, попробуйте ещё раз");
    } finally {
      setBusy(null);
    }
  };

  const sharePhone = async () => {
    setBusy("phone");
    setHint(null);
    try {
      if (!(await shareContact())) {
        setHint("Без номера участвовать нельзя: по нему администратор находит ваш приз.");
        return;
      }
      // Контакт уходит боту, бот проверяет и сохраняет номер — ждём, пока это отразится.
      for (let i = 0; i < 14; i++) {
        await sleep(1500);
        const fresh = await api.me();
        if (fresh.guest.has_phone) {
          haptic.notify("success");
          await onChanged();
          return;
        }
      }
      setHint("Номер пока не дошёл. Откройте чат с ботом и нажмите /start.");
    } catch (e) {
      setHint(e instanceof ApiError ? e.message : "Не получилось, попробуйте ещё раз");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="register">
      <h2>Пара шагов до колеса</h2>
      <p className="muted">Это бесплатно. Крутить колесо можно раз в неделю, приз забираете у администратора.</p>

      <section className={`step ${consent ? "step-done" : ""}`}>
        <div className="step-head">
          <span className="step-num">{consent ? "✓" : "1"}</span>
          <span className="step-title">Правила акции</span>
        </div>
        {!consent && (
          <>
            <p className="muted">
              Принимая правила, вы соглашаетесь на обработку номера телефона и данных Telegram клубом
              «{me.club.name}» для проведения акции и выдачи призов.
            </p>
            <div className="step-actions">
              <button className="button" disabled={busy !== null} onClick={accept}>
                {busy === "consent" ? "Сохраняем…" : "✅ Принимаю правила"}
              </button>
              <button className="button button-secondary" onClick={onShowRules}>Читать правила</button>
            </div>
          </>
        )}
      </section>

      <section className={`step ${phone ? "step-done" : ""}`}>
        <div className="step-head">
          <span className="step-num">{phone ? "✓" : "2"}</span>
          <span className="step-title">Номер телефона</span>
        </div>
        {!phone && (
          <>
            <p className="muted">По номеру администратор найдёт ваш приз. Номер видит только клуб.</p>
            {canShareContact() ? (
              <button className="button" disabled={busy !== null} onClick={sharePhone}>
                {busy === "phone" ? "Ждём номер…" : "📱 Поделиться номером"}
              </button>
            ) : (
              <>
                <p className="muted">В вашей версии Telegram номер можно отправить только в чате с ботом.</p>
                <button className="button" onClick={() => tg?.close()}>Вернуться в бот</button>
              </>
            )}
          </>
        )}
      </section>

      {hint && <p className="error-text">{hint}</p>}
    </div>
  );
}

// --- Колесо ------------------------------------------------------------------------------

function WheelTab({
  me,
  sectors,
  onChanged,
  onShowPrizes,
}: {
  me: Me;
  sectors: Sector[];
  onChanged: () => void;
  onShowPrizes: () => void;
}) {
  const wheelRef = useRef<WheelHandle>(null);
  const [spinning, setSpinning] = useState(false);
  const [result, setResult] = useState<SpinResult | null>(null);
  const [spinError, setSpinError] = useState<string | null>(null);
  const [nextSpinAt, setNextSpinAt] = useState<string | null>(me.wheel.next_spin_at);
  const now = useNow(nextSpinAt ? 1000 : null);
  const b = me.branding;

  useEffect(() => setNextSpinAt(me.wheel.next_spin_at), [me.wheel.next_spin_at]);

  const waitMs = nextSpinAt ? new Date(nextSpinAt).getTime() - now : 0;
  const blocked = !me.available || !me.wheel.enabled;
  const canSpin = !blocked && !spinning && !result && waitMs <= 0 && sectors.length > 0;

  const spin = async () => {
    if (!canSpin) return;
    unlockSound();
    setSpinError(null);
    setSpinning(true);
    haptic.impact("medium");
    try {
      const res = await api.spin();
      await wheelRef.current?.spinTo(res.sector.id);
      haptic.notify(res.sector.is_empty ? "warning" : "success");
      setResult(res);
      setNextSpinAt(res.next_spin_at);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err?.code === "cooldown" && err.nextSpinAt) {
        setNextSpinAt(err.nextSpinAt);
        onChanged();
      } else setSpinError(err?.message ?? "Что-то пошло не так, попробуйте позже");
      haptic.notify("error");
    } finally {
      setSpinning(false);
    }
  };

  // Главная кнопка Telegram внизу экрана вместо кнопки на странице.
  const spinRef = useRef(spin);
  spinRef.current = spin;
  useEffect(() => {
    const mb = tg?.MainButton;
    if (!inTelegram || !mb) return;
    const onClick = () => spinRef.current();
    if (spinning) {
      mb.setParams({ text: "Крутится…", is_visible: true, is_active: false });
      mb.showProgress();
    } else if (canSpin) {
      mb.hideProgress();
      mb.setParams({
        text: "🎡 Крутить колесо",
        is_visible: true,
        is_active: true,
        ...(b.customized ? { color: b.accent, text_color: "#ffffff" } : {}),
      });
    } else {
      mb.hideProgress();
      mb.hide();
    }
    mb.onClick(onClick);
    return () => mb.offClick(onClick);
  }, [canSpin, spinning, b.customized, b.accent]);
  useEffect(() => () => tg?.MainButton?.hide(), []);

  if (!me.available) {
    return (
      <Notice emoji="⏸️" title="Акция временно недоступна">
        <p>Уже выигранные призы можно получить по промокоду у администратора.</p>
        <button className="button button-secondary" onClick={onShowPrizes}>Мои призы</button>
      </Notice>
    );
  }
  if (!me.wheel.enabled) {
    return <Notice emoji="🛠️" title="Колесо сейчас выключено" />;
  }

  return (
    <div className="wheel-tab">
      <Wheel ref={wheelRef} sectors={sectors} colors={b} hubLabel={b.hub_label} idle={!spinning && !result} />

      {waitMs > 0 && !spinning ? (
        <div className="cooldown">
          <div className="cooldown-label">Следующий спин через</div>
          <div className="cooldown-timer">{formatCountdown(waitMs)}</div>
        </div>
      ) : (
        !inTelegram && (
          <button className="button button-spin" disabled={!canSpin} onClick={spin}>
            {spinning ? "Крутится…" : "Крутить колесо"}
          </button>
        )
      )}
      {spinError && <p className="error-text">{spinError}</p>}

      {result && (
        <ResultModal
          result={result}
          branding={b}
          timeZone={me.club.timezone}
          onClose={() => {
            setResult(null);
            onChanged();
          }}
          onShowPrizes={() => {
            setResult(null);
            onChanged();
            onShowPrizes();
          }}
        />
      )}
    </div>
  );
}

function ResultModal({
  result,
  branding,
  timeZone,
  onClose,
  onShowPrizes,
}: {
  result: SpinResult;
  branding: Branding;
  timeZone: string;
  onClose: () => void;
  onShowPrizes: () => void;
}) {
  const { sector, code } = result;
  const [showQr, setShowQr] = useState(false);
  if (showQr && code) {
    return <QrModal prize={{ code: code.code, title: sector.title, emoji: sector.emoji }} onClose={() => setShowQr(false)} />;
  }
  return (
    <div className="modal-backdrop" onClick={onClose}>
      {code && <Confetti colors={branding.wheel} />}
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-emoji">{sector.is_empty ? "😔" : "🎉"}</div>
        {code ? (
          <>
            <h2>Вы выиграли!</h2>
            <p className="modal-prize">{sector.emoji} {sector.title}</p>
            {branding.texts.win && <p className="modal-note">{branding.texts.win}</p>}
            <div className="code">{code.code}</div>
            <p className="muted">
              Действует до {formatDate(code.expires_at, timeZone)}. Промокод также отправлен в чат с ботом.
            </p>
            <button className="button" onClick={() => setShowQr(true)}>📱 Показать админу</button>
            <button className="button button-secondary" onClick={onShowPrizes}>Мои призы</button>
          </>
        ) : (
          <>
            <h2>{sector.title}</h2>
            <p className="muted">{branding.texts.lose ?? "В этот раз без приза. Возвращайтесь через неделю!"}</p>
            <button className="button" onClick={onClose}>Понятно</button>
          </>
        )}
      </div>
    </div>
  );
}

// --- Мои призы и правила -------------------------------------------------------------

function PrizesTab({ timeZone }: { timeZone: string }) {
  const [prizes, setPrizes] = useState<Prize[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.prizes().then(setPrizes, (e) => setError(e instanceof ApiError ? e.message : "Ошибка загрузки"));
  }, []);

  if (error) return <Notice emoji="⚠️" title={error} />;
  if (!prizes) return <ListSkeleton />;
  if (prizes.length === 0) {
    return (
      <Notice emoji="🎁" title="Призов пока нет">
        <p>Крутите колесо — выигранные промокоды появятся здесь.</p>
      </Notice>
    );
  }
  return <PrizeList prizes={prizes} timeZone={timeZone} />;
}

function RulesTab() {
  const [rules, setRules] = useState<{ club: string; rules: string | null } | null>(null);
  useEffect(() => {
    api.rules().then(setRules, () => setRules({ club: "", rules: null }));
  }, []);
  if (!rules) return <ListSkeleton />;
  return (
    <article className="rules">
      <h2>Правила акции</h2>
      <p className="rules-text">{rules.rules ?? "Правила акции уточняйте у администратора клуба."}</p>
      <p className="muted">Игра бесплатная. Призы — бонусы клуба, они не обмениваются на деньги.</p>
    </article>
  );
}

// --- Мелочи ----------------------------------------------------------------------------

function Screen({ children }: { children: React.ReactNode }) {
  return <div className="screen">{children}</div>;
}

function Skeleton() {
  return (
    <Screen>
      <div className="header"><div className="sk sk-title" /></div>
      <main className="content wheel-tab" aria-busy="true" aria-label="Загрузка">
        <div className="sk sk-wheel" />
        <div className="sk sk-button" />
      </main>
    </Screen>
  );
}

function ListSkeleton() {
  return (
    <div className="prizes" aria-busy="true" aria-label="Загрузка">
      {[0, 1, 2].map((i) => <div key={i} className="sk sk-card" />)}
    </div>
  );
}

function Notice({ emoji, title, children }: { emoji: string; title: string; children?: React.ReactNode }) {
  return (
    <div className="notice">
      <div className="notice-emoji">{emoji}</div>
      <h2>{title}</h2>
      {children}
    </div>
  );
}

function TabButton({ active, onClick, icon, label }: { active: boolean; onClick: () => void; icon: string; label: string }) {
  return (
    <button className={`tab ${active ? "tab-active" : ""}`} onClick={onClick}>
      <span className="tab-icon">{icon}</span>
      <span>{label}</span>
    </button>
  );
}

/** Текущее время, обновляемое с интервалом (для таймера). */
function useNow(intervalMs: number | null): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (intervalMs === null) return;
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}
