from aiogram import Dispatcher

from app.bots.admin import router as admin_router
from app.bots.handlers import router as guest_router
from app.bots.prize_editor import router as prize_router

# Один диспетчер на все боты: клуб определяется в вебхуке и передаётся в обработчики.
# Редактор призов первым: пока идёт ввод, текст — это название приза, а не промокод.
# Затем админский роутер: сотрудник клуба получает админ-обработку промокодов,
# всё остальное (включая его собственную игру как гостя) уходит в гостевой роутер.
dp = Dispatcher()
dp.include_router(prize_router)
dp.include_router(admin_router)
dp.include_router(guest_router)
