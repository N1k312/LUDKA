import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  api,
  canShareContact,
  isMock,
  shareContact,
  type Branding,
  type Game,
  type GameType,
  type Me,
  type Prize,
  type Sector,
  type SpinResult,
} from "./api";
import { CaseGame, type CaseHandle } from "./CaseGame";
import { Confetti } from "./Confetti";
import { Cups } from "./Cups";
import { formatCountdown, formatDate, formatWaitShort } from "./format";
import { PrizeList, QrModal } from "./Prizes";
import { GAME_META, RARITY_LABEL, rarityOf } from "./rarity";
import { setSoundEnabled, soundEnabled, unlock as unlockSound } from "./sound";
import { goFullscreen, haptic, inTelegram, paintTelegram, tg } from "./telegram";
import { Wheel, type WheelHandle } from "./Wheel";

type Tab = "games" | "prizes" | "rules";

const params = new URLSearchParams(window.location.search);
const initialTab: Tab = params.get("tab") === "prizes" ? "prizes" : params.get("tab") === "rules" ? "rules" : "games";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const BG = "#090a10";

/** Акцент — цвет клуба (палитра по умолчанию тоже фиолетовая). */
function applyBranding(b: Branding | undefined) {
  const root = document.documentElement.style;
  if (b) root.setProperty("--accent", b.accent);
  else root.removeProperty("--accent");
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [tab, setTab] = useState<Tab>(initialTab);
  const [game, setGame] = useState<GameType | null>(null);
  const [sound, setSound] = useState(soundEnabled());

  const load = useCallback(async () => {
    try {
      setMe(await api.me());
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e : new ApiError(0, "error", "Что-то пошло не так"));
    }
  }, []);

  useEffect(() => {
    tg?.ready();
    tg?.expand();
    paintTelegram(BG);
    goFullscreen();
    load();
  }, [load]);

  useEffect(() => applyBranding(me?.branding), [me?.branding]);

  // Системная кнопка «Назад»: из игры — в лобби, с других вкладок — к играм.
  useEffect(() => {
    const button = tg?.BackButton;
    if (!inTelegram || !button) return;
    const back = () => (game ? setGame(null) : setTab("games"));
    if (tab === "games" && !game) button.hide();
    else button.show();
    button.onClick(back);
    return () => button.offClick(back);
  }, [tab, game]);

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
  const current = game ? me.games.find((g) => g.type === game) : undefined;
  const switchTab = (t: Tab) => {
    setTab(t);
    setGame(null);
  };

  return (
    <Screen>
      <header className="header">
        {b.logo_url && <img className="logo" src={b.logo_url} alt="" />}
        <div>
          <div className="club-name">{me.club.name}</div>
          <div className="header-sub">Игры клуба</div>
        </div>
        {isMock && <span className="badge">мок</span>}
        <button
          className="icon-button"
          aria-label={sound ? "Выключить звук" : "Включить звук"}
          onClick={() => {
            setSoundEnabled(!sound);
            setSound(!sound);
          }}
        >
          {sound ? "🔊" : "🔇"}
        </button>
      </header>

      <main className="content">
        {tab === "games" && !me.guest.registered && (
          <Registration me={me} onChanged={load} onShowRules={() => switchTab("rules")} />
        )}
        {tab === "games" && me.guest.registered && !current && <Lobby me={me} onOpen={setGame} />}
        {tab === "games" && me.guest.registered && current && (
          <GameScreen
            key={current.type}
            me={me}
            game={current}
            onBack={() => setGame(null)}
            onChanged={load}
            onShowPrizes={() => switchTab("prizes")}
          />
        )}
        {tab === "prizes" && <PrizesTab timeZone={me.club.timezone} />}
        {tab === "rules" && <RulesTab />}
      </main>

      <nav className="tabs">
        <TabButton active={tab === "games"} onClick={() => switchTab("games")} icon="🎮" label="Игры" />
        <TabButton active={tab === "prizes"} onClick={() => switchTab("prizes")} icon="🎁" label="Мои призы" />
        <TabButton active={tab === "rules"} onClick={() => switchTab("rules")} icon="📜" label="Правила" />
      </nav>
    </Screen>
  );
}

// --- Лобби ------------------------------------------------------------------------------

function Lobby({ me, onOpen }: { me: Me; onOpen: (game: GameType) => void }) {
  const games = me.games.filter((g) => g.enabled);
  const now = useNow(games.some((g) => g.next_play_at) ? 1000 : null);

  if (!me.available) {
    return (
      <Notice emoji="⏸️" title="Акция временно недоступна">
        <p>Уже выигранные призы можно получить по промокоду у администратора.</p>
      </Notice>
    );
  }
  if (games.length === 0) return <Notice emoji="🛠️" title="Игры сейчас выключены" />;

  return (
    <>
      <div className="hero">
        <h2>Привет, {me.guest.first_name}!</h2>
        <p className="muted">Выбирайте игру — у каждой свой таймер.</p>
      </div>
      <div className="games">
        {games.map((g) => {
          const meta = GAME_META[g.type];
          const wait = g.next_play_at ? new Date(g.next_play_at).getTime() - now : 0;
          return (
            <button
              key={g.type}
              className="game-card"
              style={{ "--game": meta.color } as React.CSSProperties}
              onClick={() => {
                haptic.impact("light");
                onOpen(g.type);
              }}
            >
              <span className="game-art" aria-hidden="true">{meta.icon}</span>
              <span className="game-main">
                <span className="game-title">{g.title}</span>
                <span className="game-sub">{meta.sub}</span>
              </span>
              {wait > 0 ? (
                <span className="pill pill-wait">⏳ {formatWaitShort(wait)}</span>
              ) : (
                <span className="pill pill-ready">Играть</span>
              )}
            </button>
          );
        })}
      </div>
      <div className="legend">
        <span className="rarity-legendary">очень редкий</span>
        <span className="rarity-rare">редкий</span>
        <span className="rarity-common">обычный</span>
      </div>
    </>
  );
}

// --- Экран игры -------------------------------------------------------------------------

function GameScreen({
  me,
  game,
  onBack,
  onChanged,
  onShowPrizes,
}: {
  me: Me;
  game: Game;
  onBack: () => void;
  onChanged: () => void;
  onShowPrizes: () => void;
}) {
  const meta = GAME_META[game.type];
  const wheelRef = useRef<WheelHandle>(null);
  const caseRef = useRef<CaseHandle>(null);
  const [prizes, setPrizes] = useState<Sector[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SpinResult | null>(null);
  const [playError, setPlayError] = useState<string | null>(null);
  const [nextAt, setNextAt] = useState<string | null>(game.next_play_at);
  const [round, setRound] = useState(0);
  const now = useNow(nextAt ? 1000 : null);
  const b = me.branding;

  useEffect(() => {
    api.prizesOf(game.type).then(setPrizes, () => setPrizes([]));
  }, [game.type]);

  const waitMs = nextAt ? new Date(nextAt).getTime() - now : 0;
  const ready = me.available && game.enabled && (prizes?.length ?? 0) > 0;
  const canPlay = ready && !busy && !result && waitMs <= 0;

  /** Ход на сервере. null — отказ: лимит или ошибка (сообщение уже показано). */
  const request = async (choice?: number): Promise<SpinResult | null> => {
    unlockSound();
    setPlayError(null);
    setBusy(true);
    haptic.impact("medium");
    try {
      return await api.play(game.type, choice);
    } catch (e) {
      const err = e instanceof ApiError ? e : null;
      if (err?.code === "cooldown" && err.nextSpinAt) {
        setNextAt(err.nextSpinAt);
        onChanged();
      } else setPlayError(err?.message ?? "Что-то пошло не так, попробуйте позже");
      haptic.notify("error");
      setBusy(false);
      return null;
    }
  };

  const finish = (res: SpinResult) => {
    haptic.notify(res.prize.is_empty ? "warning" : "success");
    setResult(res);
    setNextAt(res.next_spin_at);
    setBusy(false);
  };

  /** Колесо и кейс: кнопка «Играть». Стаканчики запускаются нажатием на стаканчик. */
  const start = async () => {
    if (!canPlay || game.type === "cups") return;
    const res = await request();
    if (!res) return;
    if (game.type === "wheel") await wheelRef.current?.spinTo(res.prize.id);
    else await caseRef.current?.roll(res.prize);
    finish(res);
  };

  // Главная кнопка Telegram внизу экрана.
  const startRef = useRef(start);
  startRef.current = start;
  useEffect(() => {
    const mb = tg?.MainButton;
    if (!inTelegram || !mb) return;
    const onClick = () => startRef.current();
    if (game.type !== "cups" && busy) {
      mb.setParams({ text: "Играем…", is_visible: true, is_active: false });
      mb.showProgress();
    } else if (game.type !== "cups" && canPlay) {
      mb.hideProgress();
      mb.setParams({ text: meta.action, is_visible: true, is_active: true, color: b.accent, text_color: "#ffffff" });
    } else {
      mb.hideProgress();
      mb.hide();
    }
    mb.onClick(onClick);
    return () => mb.offClick(onClick);
  }, [canPlay, busy, game.type, meta.action, b.accent]);
  useEffect(() => () => tg?.MainButton?.hide(), []);

  const close = () => {
    setResult(null);
    setRound((r) => r + 1);
    onChanged();
  };

  return (
    <div className="game-screen">
      {!inTelegram && <button className="back-link" onClick={onBack}>← Все игры</button>}
      <h2>{meta.icon} {game.title}</h2>
      {game.type === "cups" && <p className="muted game-hint">Под одним из стаканчиков может быть приз. Выберите один.</p>}

      {!prizes ? (
        <div className="sk sk-wheel" />
      ) : prizes.length === 0 ? (
        <Notice emoji="🛠️" title="Призы ещё не настроены" />
      ) : (
        <>
          {game.type === "wheel" && (
            <Wheel ref={wheelRef} sectors={prizes} colors={b} hubLabel={b.hub_label} idle={!busy && !result} />
          )}
          {game.type === "cups" && (
            <Cups
              key={round}
              disabled={!canPlay}
              onPick={(i) => request(i)}
              onRevealed={finish}
            />
          )}
          {game.type === "case" && <CaseGame key={round} ref={caseRef} prizes={prizes} />}
        </>
      )}

      {waitMs > 0 && !busy ? (
        <div className="cooldown">
          <div className="cooldown-label">Следующая игра через</div>
          <div className="cooldown-timer">{formatCountdown(waitMs)}</div>
        </div>
      ) : (
        !inTelegram && game.type !== "cups" && ready && (
          <button className="button button-spin" disabled={!canPlay} onClick={start}>
            {busy ? "Играем…" : meta.action}
          </button>
        )
      )}
      {playError && <p className="error-text">{playError}</p>}

      {result && (
        <ResultModal
          result={result}
          branding={b}
          timeZone={me.club.timezone}
          onClose={close}
          onShowPrizes={() => {
            close();
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
  const { prize, code } = result;
  const level = rarityOf(prize);
  const [showQr, setShowQr] = useState(false);
  if (showQr && code) {
    return <QrModal prize={{ code: code.code, title: prize.title, emoji: prize.emoji }} onClose={() => setShowQr(false)} />;
  }
  return (
    <div className="modal-backdrop" onClick={onClose}>
      {code && <Confetti colors={level === "legendary" ? ["#ff3d5a", "#ffd166", "#ffffff"] : branding.wheel} />}
      <div className={`modal rarity-${level}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-emoji">{prize.is_empty ? "😔" : prize.emoji ?? "🎉"}</div>
        {code ? (
          <>
            <span className="rarity-tag">{RARITY_LABEL[level]}</span>
            <h2>Вы выиграли!</h2>
            <p className="modal-prize">{prize.title}</p>
            {branding.texts.win && <p className="modal-note muted">{branding.texts.win}</p>}
            <div className="code">{code.code}</div>
            <p className="muted">
              Действует до {formatDate(code.expires_at, timeZone)}. Промокод также отправлен в чат с ботом.
            </p>
            <button className="button" onClick={() => setShowQr(true)}>📱 Показать админу</button>
            <button className="button button-secondary" onClick={onShowPrizes}>Мои призы</button>
          </>
        ) : (
          <>
            <h2>{prize.title}</h2>
            <p className="muted">{branding.texts.lose ?? "В этот раз без приза. Возвращайтесь позже!"}</p>
            <button className="button" onClick={onClose}>Понятно</button>
          </>
        )}
      </div>
    </div>
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
      <h2>Пара шагов до игр</h2>
      <p className="muted">Это бесплатно. Призы забираете у администратора клуба по промокоду.</p>

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
        <p>Играйте — выигранные промокоды появятся здесь.</p>
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
      <main className="content" aria-busy="true" aria-label="Загрузка">
        <div className="games">
          {[0, 1, 2].map((i) => <div key={i} className="sk sk-card" />)}
        </div>
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
