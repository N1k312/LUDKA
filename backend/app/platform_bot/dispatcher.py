from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from app.platform_bot.handlers import router, strangers

# Отдельный диспетчер для бота платформы. Состояния многошаговых действий хранятся
# в памяти: при перезапуске API незаконченный ввод просто начинается заново.
platform_dp = Dispatcher(storage=MemoryStorage())
platform_dp.include_router(router)
platform_dp.include_router(strangers)
