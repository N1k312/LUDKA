from aiogram import Dispatcher

from app.bots.handlers import router as guest_router

# Один диспетчер на все боты: клуб определяется в вебхуке и передаётся в обработчики.
dp = Dispatcher()
dp.include_router(guest_router)
