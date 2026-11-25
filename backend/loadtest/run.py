"""Нагрузочный тест одного клуба: Mini App (все три игры), вебхуки бота, гонка, рост базы.

Запускает loadtest.server отдельным процессом, наполняет ОТДЕЛЬНУЮ базу клубом с тремя
играми и тысячами гостей и гоняет сценарии с разной одновременностью. Результат — JSON
(LOADTEST_OUT) и сводка в консоли. Рабочую базу и Telegram не трогает.

Перед запуском (из корня репозитория) — временная база:
  docker run -d --rm --name loadtest-db -e POSTGRES_USER=load -e POSTGRES_PASSWORD=load \
    -e POSTGRES_DB=load -p 127.0.0.1:55432:5432 postgres:17
Запуск (из backend/): PYTHONIOENCODING=utf-8 .venv/Scripts/python -m loadtest.run
После: docker stop loadtest-db
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlencode

BACKEND = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("LOADTEST_PORT", "8765"))
DB_URL = os.environ.get("LOADTEST_DB", "postgresql+asyncpg://load:load@127.0.0.1:55432/load")
DB_CONTAINER = os.environ.get("LOADTEST_DB_CONTAINER", "loadtest-db")
OUT = os.environ.get("LOADTEST_OUT", str(BACKEND / "loadtest" / "last-result.json"))
BASE = f"http://127.0.0.1:{PORT}"

os.environ.update(DATABASE_URL=DB_URL, PLATFORM_BOT_TOKEN="", PLATFORM_OWNER_IDS="", TUNNEL_METRICS_URL="",
                  TEST_BOT_TOKEN="", PUBLIC_BASE_URL="https://loadtest.invalid")

import httpx  # noqa: E402
from sqlalchemy import func, select, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402

from app.crypto import encrypt  # noqa: E402
from app.models import BotStatus, ClubBot, ClubStaff, GameType, Guest, PrizeCode, StaffRole  # noqa: E402
from app.services import clubs  # noqa: E402
from tests.botkit import callback_update, message_update  # noqa: E402

BOT_ID = 777000777
TOKEN = "777000777:AALoadTestFakeTokenNotReal_abcdefghij"
ADMIN = 900001
GUEST_BASE = 10_000_000
PRIZES = {
    GameType.WHEEL: [("🎮", "1 час игры", 30, False), ("🥤", "Энергетик", 25, False), ("💸", "Скидка 10%", 15, False),
                     ("🏆", "3 часа игры", 5, False), ("😔", "Повезёт в следующий раз", 25, True)],
    GameType.CUPS: [("🍟", "Чипсы", 35, False), ("🥤", "Кола", 25, False), ("🎮", "Час игры", 10, False),
                    (None, "Пусто", 30, True)],
    GameType.CASE: [("⚡", "Энергетик", 40, False), ("💸", "Скидка 20%", 25, False), ("🎮", "2 часа игры", 10, False),
                    ("👑", "Ночь в VIP", 3, False), (None, "Пусто", 22, True)],
}
GAMES = ["wheel", "cups", "case"]
# Одновременно играющих гостей → сколько «заходов» (открыл → посмотрел призы → сыграл → мои призы).
LEVELS = [(1, 60), (10, 300), (50, 600), (100, 800), (200, 1000)]
if os.environ.get("LOADTEST_LEVELS"):  # например «10:300,100:800»
    LEVELS = [tuple(int(x) for x in part.split(":")) for part in os.environ["LOADTEST_LEVELS"].split(",")]
EXTRAS = os.environ.get("LOADTEST_EXTRAS", "1") == "1"  # чтение, вебхуки, гонка, размер базы


def init_data(user_id: int) -> str:
    fields = {"auth_date": str(int(time.time())), "query_id": "AAH-load",
              "user": json.dumps({"id": user_id, "first_name": "Гость"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def api_headers(user_id: int) -> dict:
    return {"Authorization": f"tma {init_data(user_id)}", "X-Bot-Id": str(BOT_ID)}


# --- Замеры ресурсов -------------------------------------------------------------------------


def real_server_pid(pid: int) -> int:
    """python.exe из venv на Windows — лишь запускатель: настоящий сервер — его дочерний процесс."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}' | "
                          "Where-Object Name -like 'python*' | Select-Object -First 1).ProcessId"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else pid


def api_process(pid: int) -> tuple[float, float]:
    """(секунды CPU, память МБ) процесса сервера — через PowerShell (Windows)."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          f"$p=Get-Process -Id {pid}; \"$($p.CPU) $($p.WorkingSet64)\""],
                         capture_output=True, text=True).stdout.split()
    return float(out[0].replace(",", ".")), int(out[1]) / 2**20


def container_cpu_seconds(container: str) -> float:
    out = subprocess.run(["docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat"],
                         capture_output=True, text=True).stdout
    usage = next(line for line in out.splitlines() if line.startswith("usage_usec"))
    return int(usage.split()[1]) / 1e6


def container_memory_mb(container: str) -> float:
    out = subprocess.run(["docker", "exec", container, "cat", "/sys/fs/cgroup/memory.current"],
                         capture_output=True, text=True).stdout
    return int(out.strip()) / 2**20


def db_cpu_seconds() -> float:
    return container_cpu_seconds(DB_CONTAINER)


def db_memory_mb() -> float:
    return container_memory_mb(DB_CONTAINER)


# Сервер в Linux-контейнере из рабочего образа (точнее для оценки): см. docs/testing.md.
API_CONTAINER = os.environ.get("LOADTEST_API_CONTAINER")


def api_usage(pid: int) -> tuple[float, float]:
    if API_CONTAINER:
        return container_cpu_seconds(API_CONTAINER), container_memory_mb(API_CONTAINER)
    return api_process(pid)


class Meter:
    def __init__(self, pid: int):
        self.pid = pid

    def __enter__(self):
        self.t0, (self.api0, _), self.db0 = time.perf_counter(), api_usage(self.pid), db_cpu_seconds()
        self.client0 = time.process_time()
        return self

    def __exit__(self, *exc):
        self.wall = time.perf_counter() - self.t0
        self.client_cpu = (time.process_time() - self.client0) / self.wall * 100  # сам генератор нагрузки
        api1, self.api_mem = api_usage(self.pid)
        self.api_cpu = (api1 - self.api0) / self.wall * 100   # % одного ядра
        self.db_cpu = (db_cpu_seconds() - self.db0) / self.wall * 100
        self.db_mem = db_memory_mb()


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


class Stats:
    def __init__(self):
        self.lat = defaultdict(list)
        self.codes = Counter()

    async def call(self, client: httpx.AsyncClient, name: str, method: str, url: str, **kw) -> httpx.Response | None:
        t0 = time.perf_counter()
        try:
            r = await client.request(method, url, **kw)
        except httpx.HTTPError as e:
            self.codes[f"{name} {type(e).__name__}"] += 1
            return None
        self.lat[name].append((time.perf_counter() - t0) * 1000)
        self.codes[f"{name} {r.status_code}"] += 1
        return r

    def summary(self) -> dict:
        return {name: {"n": len(v), "p50": round(pct(v, 50)), "p95": round(pct(v, 95)), "p99": round(pct(v, 99)),
                       "max": round(max(v))} for name, v in self.lat.items()}

    def errors(self) -> dict:
        return {k: n for k, n in self.codes.items() if not k.endswith(" 200")}


# --- Подготовка клуба ------------------------------------------------------------------------


async def seed(engine, guests: int) -> str:
    async with AsyncSession(engine) as db:
        club = await clubs.create_club(db, "Нагрузочный клуб")
        for game, rows in PRIZES.items():
            for emoji, title, weight, empty in rows:
                await clubs.add_prize(db, club, title, weight, emoji, is_empty=empty, game=game)
            await clubs.set_game_enabled(db, club, game, True)
        secret = secrets.token_hex(16)
        db.add(ClubBot(bot_id=BOT_ID, club_id=club.id, username="loadtest_bot", token_encrypted=encrypt(TOKEN),
                       webhook_secret=secret, status=BotStatus.ACTIVE))
        db.add(ClubStaff(club_id=club.id, telegram_id=ADMIN, role=StaffRole.ADMIN, first_name="Админ"))
        now = datetime.now(UTC)
        db.add_all(Guest(club_id=club.id, telegram_id=GUEST_BASE + i, first_name=f"Гость {i}",
                         phone=f"+7700{i:07d}", consent_at=now) for i in range(guests))
        await db.commit()
    return secret


# --- Сценарии --------------------------------------------------------------------------------


async def journey(client, stats: Stats, uid: int, game: str) -> None:
    h = api_headers(uid)
    await stats.call(client, "GET /api/me", "GET", "/api/me", headers=h)
    await stats.call(client, "GET /api/games/{game}", "GET", f"/api/games/{game}", headers=h)
    body = {"choice": uid % 6} if game == "cups" else None
    await stats.call(client, "POST /api/play/{game}", "POST", f"/api/play/{game}", headers=h, json=body)
    await stats.call(client, "GET /api/prizes", "GET", "/api/prizes", headers=h)


async def run_bounded(concurrency: int, jobs) -> None:
    sem = asyncio.Semaphore(concurrency)

    async def guarded(job):
        async with sem:
            await job

    await asyncio.gather(*(guarded(j) for j in jobs))


# Генератор нагрузки в одном процессе Python упирается в своё ядро раньше сервера
# (проверено: 97% CPU при 50 одновременных), поэтому задания раздаются нескольким процессам.
WORKERS = int(os.environ.get("LOADTEST_WORKERS", "6"))


async def _worker_async(jobs: list, concurrency: int, secret: str) -> Stats:
    stats = Stats()
    limits = httpx.Limits(max_connections=concurrency + 5, max_keepalive_connections=concurrency + 5)
    async with httpx.AsyncClient(base_url=BASE, timeout=120, limits=limits) as client:
        async def do(job):
            if job[0] == "journey":
                await journey(client, stats, job[1], job[2])
            elif job[0] == "me":
                await stats.call(client, "GET /api/me", "GET", "/api/me", headers=api_headers(job[1]))
            else:
                await stats.call(client, "POST /tg/{bot}", "POST", f"/tg/{BOT_ID}",
                                 headers={"X-Telegram-Bot-Api-Secret-Token": secret}, json=job[1])

        await run_bounded(concurrency, [do(j) for j in jobs])
    return stats


def worker(jobs: list, concurrency: int, secret: str) -> tuple[dict, dict, float]:
    t0 = time.process_time()
    stats = asyncio.run(_worker_async(jobs, concurrency, secret))
    return dict(stats.lat), dict(stats.codes), time.process_time() - t0


def warm_up(_: int) -> None:
    time.sleep(0.5)


async def run_parallel(pool, jobs: list, concurrency: int, secret: str) -> tuple[Stats, float]:
    """Задания делятся между процессами; (общая статистика, средняя загрузка ядра одного генератора, с)."""
    n = max(1, min(WORKERS, concurrency, len(jobs)))
    per = [concurrency // n + (1 if i < concurrency % n else 0) for i in range(n)]
    loop = asyncio.get_running_loop()
    parts = await asyncio.gather(*(loop.run_in_executor(pool, worker, jobs[i::n], per[i], secret) for i in range(n)))
    stats, cpu = Stats(), 0.0
    for lat, codes, used in parts:
        for name, values in lat.items():
            stats.lat[name].extend(values)
        stats.codes.update(codes)
        cpu += used
    return stats, cpu / n


def level_report(name: str, m: Meter, stats: Stats, units: int, unit: str) -> dict:
    requests = sum(s["n"] for s in stats.summary().values())
    return {
        "scenario": name, unit: units, "wall_s": round(m.wall, 2),
        f"{unit}_per_s": round(units / m.wall, 1), "req_per_s": round(requests / m.wall, 1),
        "endpoints": stats.summary(), "errors": stats.errors(),
        "api_cpu_pct_of_core": round(m.api_cpu), "api_mem_mb": round(m.api_mem),
        "db_cpu_pct_of_core": round(m.db_cpu), "db_mem_mb": round(m.db_mem),
        "loadgen_cpu_pct_of_core": round(m.client_cpu),
    }


async def main() -> None:
    total = sum(n for _, n in LEVELS)
    # Каждый прогон — с чистой временной базой (только контейнер нагрузки, не рабочая база).
    subprocess.run(["docker", "exec", DB_CONTAINER, "psql", "-q", "-U", "load", "-d", "load", "-c",
                    "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"], check=True, capture_output=True)
    log = open(BACKEND / "loadtest" / "server.log", "w", encoding="utf-8")
    if API_CONTAINER:  # сервер уже запущен в контейнере — перезапуск применит миграции к чистой базе
        subprocess.run(["docker", "restart", API_CONTAINER], check=True, capture_output=True)
        server = None
    else:
        server = subprocess.Popen([sys.executable, "-m", "loadtest.server"], cwd=BACKEND, stdout=log, stderr=log,
                                  env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    results: dict = {"started": datetime.now(UTC).isoformat(), "levels": [], "webhooks": [],
                     "server": f"контейнер {API_CONTAINER} (Linux, рабочий образ)" if API_CONTAINER else "Windows, venv"}
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=60,
                                     limits=httpx.Limits(max_connections=400, max_keepalive_connections=400)) as client:
            for _ in range(120):
                try:
                    if (await client.get("/health")).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
            engine = create_async_engine(DB_URL)
            secret = await seed(engine, total + 1000)
            pid = real_server_pid(server.pid) if server else 0
            results["pool"] = os.environ.get("LOADTEST_POOL", "по умолчанию (5 + 10)")
            print(f"Клуб готов: 3 игры, {total + 1000} гостей. Сервер PID {pid}, пул {results['pool']}.", flush=True)

            pool = ProcessPoolExecutor(max_workers=WORKERS)
            list(pool.map(warm_up, range(WORKERS)))  # процессы запущены заранее, не во время замера

            async def measured(name: str, jobs: list, concurrency: int, units: int, unit: str) -> dict:
                with Meter(pid) as m:
                    stats, loadgen_cpu = await run_parallel(pool, jobs, concurrency, secret)
                m.client_cpu = loadgen_cpu / m.wall * 100
                rep = level_report(name, m, stats, units, unit)
                rep["concurrency"] = concurrency
                print(json.dumps(rep, ensure_ascii=False), flush=True)
                return rep

            # 1. Гости играют: открыть → призы игры → сыграть → мои призы.
            next_guest = 0
            for concurrency, journeys in LEVELS:
                jobs = [("journey", GUEST_BASE + next_guest + k, GAMES[k % 3]) for k in range(journeys)]
                next_guest += journeys
                results["levels"].append(await measured(
                    f"гости играют, одновременно {concurrency}", jobs, concurrency, journeys, "journeys"))

            if not EXTRAS:
                pool.shutdown()
                await engine.dispose()
                return

            # 2. Только открытие Mini App (чтение) — потолок по запросам.
            jobs = [("me", GUEST_BASE + i) for i in range(3000)]
            results["read"] = await measured("открытие Mini App, одновременно 100", jobs, 100, 3000, "requests")

            # 3. Вебхуки бота: админ проверяет коды, гасит, гости жмут /start.
            async with AsyncSession(engine) as db:
                codes = list(await db.scalars(select(PrizeCode.code).limit(800)))
            scenarios = [
                ("админ проверяет промокод", [message_update(c, user_id=ADMIN) for c in codes[:400]]),
                ("админ гасит промокод", [callback_update("redeem:" + c, user_id=ADMIN) for c in codes[400:700]]),
                ("гость пишет /start", [message_update("/start", user_id=GUEST_BASE + i) for i in range(300)]),
            ]
            for name, updates in scenarios:
                results["webhooks"].append(await measured(
                    f"{name}, одновременно 20", [("webhook", u) for u in updates], 20, len(updates), "updates"))
            pool.shutdown()

            # 4. Гонка: 30 одновременных нажатий одного гостя.
            uid = GUEST_BASE + total + 999
            h = api_headers(uid)
            responses = await asyncio.gather(*(client.post("/api/play/case", headers=h) for _ in range(30)))
            results["race"] = dict(Counter(r.status_code for r in responses))
            print("Гонка (30 нажатий одного гостя):", results["race"], flush=True)

            # 5. Сколько места занимают данные.
            async with AsyncSession(engine) as db:
                size = {}
                for table in ("guests", "plays", "prize_codes", "redemption_log"):
                    rows = await db.scalar(text(f"SELECT count(*) FROM {table}"))
                    total_bytes = await db.scalar(text(f"SELECT pg_total_relation_size('{table}')"))
                    size[table] = {"rows": rows, "bytes": total_bytes,
                                   "bytes_per_row": round(total_bytes / rows) if rows else None}
                size["database_bytes"] = await db.scalar(text("SELECT pg_database_size(current_database())"))
                size["codes_unique"] = await db.scalar(select(func.count(func.distinct(PrizeCode.code))))
                size["codes_total"] = await db.scalar(select(func.count()).select_from(PrizeCode))
            results["storage"] = size
            print(json.dumps(size, ensure_ascii=False), flush=True)
            await engine.dispose()
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        else:
            log.write(subprocess.run(["docker", "logs", API_CONTAINER], capture_output=True, text=True,
                                     encoding="utf-8", errors="replace").stderr)
        log.close()
        Path(OUT).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        server_log = (BACKEND / "loadtest" / "server.log").read_text(encoding="utf-8", errors="replace")
        print(f"Ошибок в логе сервера: {server_log.count('Traceback')}. Результат: {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
