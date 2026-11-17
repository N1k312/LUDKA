import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, isMock, type Me, type Prize, type Sector, type SpinResult } from "./api";
import { formatCountdown, formatDate, formatDateTime } from "./format";
import { PrizeList } from "./Prizes";
import { haptic, tg } from "./telegram";
import { Wheel, type WheelHandle } from "./Wheel";

type Tab = "wheel" | "prizes" | "rules";

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [sectors, setSectors] = useState<Sector[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [tab, setTab] = useState<Tab>("wheel");

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
    load();
  }, [load]);

  if (error) {
    return (
      <Screen>
        <Notice emoji={error.status === 401 ? "🔒" : "⚠️"} title={error.message}>
          {error.status !== 401 && (
            <button className="button" onClick={load}>Повторить</button>
          )}
        </Notice>
      </Screen>
    );
  }
  if (!me) {
    return (
      <Screen>
        <div className="loader" aria-label="Загрузка" />
      </Screen>
    );
  }

  return (
    <Screen>
      <header className="header">
        <div className="club-name">{me.club.name}</div>
        {isMock && <span className="badge">мок</span>}
      </header>

      <main className="content">
        {tab === "wheel" && <WheelTab me={me} sectors={sectors} onChanged={load} onShowPrizes={() => setTab("prizes")} />}
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

// --- Колесо ----------------------------------------------------------------------------

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

  useEffect(() => setNextSpinAt(me.wheel.next_spin_at), [me.wheel.next_spin_at]);

  const waitMs = nextSpinAt ? new Date(nextSpinAt).getTime() - now : 0;
  const canSpin = !spinning && waitMs <= 0 && sectors.length > 0;

  if (!me.guest.registered) {
    return (
      <Notice emoji="📱" title="Сначала зарегистрируйтесь">
        <p>Вернитесь в чат с ботом, поделитесь номером телефона и примите правила акции — это займёт минуту.</p>
        <button className="button" onClick={() => tg?.close()}>Вернуться в бот</button>
      </Notice>
    );
  }
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

  const spin = async () => {
    if (!canSpin) return;
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
      }
      else setSpinError(err?.message ?? "Что-то пошло не так, попробуйте позже");
      haptic.notify("error");
    } finally {
      setSpinning(false);
    }
  };

  return (
    <div className="wheel-tab">
      <Wheel ref={wheelRef} sectors={sectors} />

      {waitMs > 0 && !spinning ? (
        <div className="cooldown">
          <div className="cooldown-label">Следующий спин через</div>
          <div className="cooldown-timer">{formatCountdown(waitMs)}</div>
        </div>
      ) : (
        <button className="button button-spin" disabled={!canSpin} onClick={spin}>
          {spinning ? "Крутится…" : "Крутить колесо"}
        </button>
      )}
      {spinError && <p className="error-text">{spinError}</p>}

      {result && (
        <ResultModal
          result={result}
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
  timeZone,
  onClose,
  onShowPrizes,
}: {
  result: SpinResult;
  timeZone: string;
  onClose: () => void;
  onShowPrizes: () => void;
}) {
  const { sector, code } = result;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-emoji">{sector.is_empty ? "😔" : "🎉"}</div>
        {code ? (
          <>
            <h2>Вы выиграли!</h2>
            <p className="modal-prize">{sector.emoji} {sector.title}</p>
            <div className="code">{code.code}</div>
            <p className="muted">
              Действует до {formatDate(code.expires_at, timeZone)}. Промокод также отправлен в чат с ботом —
              покажите его администратору.
            </p>
            <button className="button" onClick={onShowPrizes}>Мои призы</button>
          </>
        ) : (
          <>
            <h2>{sector.title}</h2>
            <p className="muted">В этот раз без приза. Возвращайтесь через неделю!</p>
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
  if (!prizes) return <div className="loader" />;
  if (prizes.length === 0) {
    return <Notice emoji="🎁" title="Призов пока нет">
      <p>Крутите колесо — выигранные промокоды появятся здесь.</p>
    </Notice>;
  }
  return <PrizeList prizes={prizes} timeZone={timeZone} formatDate={formatDate} formatDateTime={formatDateTime} />;
}

function RulesTab() {
  const [rules, setRules] = useState<{ club: string; rules: string | null } | null>(null);
  useEffect(() => {
    api.rules().then(setRules, () => setRules({ club: "", rules: null }));
  }, []);
  if (!rules) return <div className="loader" />;
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
