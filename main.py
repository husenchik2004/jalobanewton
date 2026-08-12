import json
import os
# Создаем файл service_account.json на Railway
if os.getenv("SERVICE_ACCOUNT_JSON"):
    with open("service_account.json", "w") as f:
        f.write(os.getenv("SERVICE_ACCOUNT_JSON"))

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from scheduler import start_scheduler
from auth import AuthManager, AuthMiddleware

# ======================================
# 🔧 НАСТРОЙКИ (из переменных окружения)
# ======================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    # Временный fallback, чтобы бот не падал, пока вы не зададите env на Railway.
    # ВАЖНО: задайте BOT_TOKEN в Variables и перевыпустите токен в @BotFather (Revoke).
    BOT_TOKEN = "8383092549:AAE3UGGknaeylE-bd9RxVuTsFc2bIWPVQiE"
    print("⚠️ ВНИМАНИЕ: BOT_TOKEN берётся из кода! Задайте env BOT_TOKEN и перевыпустите токен в BotFather.")

# Пароль для входа сотрудников (fail-closed: если пусто — пускает только ADMINS)
ACCESS_PASSWORD = os.getenv("ACCESS_PASSWORD", "")

# Админы: из env ADMINS ("id1,id2") + базовые владельцы
_DEFAULT_ADMINS = [1450296021, 420533161]
def _parse_admins():
    raw = os.getenv("ADMINS", "")
    out = set(_DEFAULT_ADMINS)
    if raw.strip():
        for part in raw.replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
    return out

ADMINS = _parse_admins()

GROUP_COMPLAINTS_ID = -1003211230484     # группа "ЖАЛОБЫ"
GROUP_SOLUTIONS_ID = -1003284967767      # группа "РЕШЕНИЯ"
GROUP_LEADERS_ID = -1003284967767        # группа "РУКОВОДСТВО"

GOOGLE_SHEET_ID = "1XP4m-yo3_-Y2QPP49af2VmNFcvwXxB9ig1wVWV2gujk"
SERVICE_ACCOUNT_FILE = "service_account.json"

TIMEZONE = "Asia/Tashkent"

# ======================================
# 🔇 ЛОГИ (чистый вывод)
# ======================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("aiogram.event").setLevel(logging.ERROR)
logging.getLogger("aiogram.dispatcher").setLevel(logging.ERROR)
logging.getLogger("aiogram").setLevel(logging.INFO)

# ======================================
# ⚙️ ИНИЦИАЛИЗАЦИЯ БОТА
# ======================================
bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)

# ======================================
# 🔒 Менеджер блокировок
# ======================================
class LockManager:
    def __init__(self):
        self._locks = {}

    async def acquire(self, user_id: int) -> bool:
        if user_id in self._locks:
            return False
        lock = asyncio.Lock()
        self._locks[user_id] = lock
        await lock.acquire()
        return True

    def release(self, user_id: int):
        lock = self._locks.get(user_id)
        if lock and lock.locked():
            lock.release()
        if user_id in self._locks:
            del self._locks[user_id]

bot.lock_manager = LockManager()

# ======================================
# FSM и диспетчер
# ======================================
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Импорт хендлеров (ПОСЛЕ инициализации dp!)
from handlers import complaints, statistics

if statistics.router.parent_router is None:
    dp.include_router(statistics.router)


# --------------------------------------
# 🔹 Храним вспомогательные данные прямо в bot
# --------------------------------------
bot.data = {"cancelled": {}}
bot.solution_waiting = {}
bot._sent_ids = set()
bot._called_ids = set()
bot.solution_messages = {}
# --------------------------------------
# 🔹 Общая конфигурация (для всех модулей)
# --------------------------------------
bot.config = {
    "GROUP_COMPLAINTS_ID": GROUP_COMPLAINTS_ID,
    "GROUP_SOLUTIONS_ID": GROUP_SOLUTIONS_ID,
    "GROUP_LEADERS_ID": GROUP_LEADERS_ID,
    "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
    "SERVICE_ACCOUNT_FILE": SERVICE_ACCOUNT_FILE,
    "TIMEZONE": TIMEZONE,
    "ADMINS": list(ADMINS)
}

# ======================================
# 🔐 Авторизация (пароль + белый список в Google Sheets)
# ======================================
auth = AuthManager(SERVICE_ACCOUNT_FILE, GOOGLE_SHEET_ID, ACCESS_PASSWORD, ADMINS)

# middleware ставится ПОСЛЕ импорта хендлеров (нужна complaints.main_menu_kb)
dp.message.outer_middleware(AuthMiddleware(auth, complaints.main_menu_kb))
dp.callback_query.outer_middleware(AuthMiddleware(auth, complaints.main_menu_kb))

# ======================================
# 🚀 ОСНОВНОЙ ЗАПУСК
# ======================================
async def main():
    # подключаем router'ы безопасно
    if complaints.router.parent_router is None:
        dp.include_router(complaints.router)
    if statistics.router.parent_router is None:
        dp.include_router(statistics.router)

    # глобальные обработчики ошибок
    try:
        dp.errors.register(complaints.errors_handler)
    except AttributeError:
        pass

    # загружаем список уже авторизованных пользователей
    try:
        auth.load_authorized()
    except Exception as e:
        logging.warning(f"⚠️ Не удалось загрузить список пользователей: {e}")

    # запуск планировщика
    try:
        start_scheduler(bot)
    except Exception as e:
        logging.warning(f"⚠️ Планировщик не запущен: {e}")

    print("🚀 Бот запущен и готов к работе!")
    await dp.start_polling(bot)

# ======================================
# ▶️ ТОЧКА ВХОДА
# ======================================
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Бот остановлен вручную")
