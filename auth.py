# auth.py
"""
Авторизация для бота жалоб.

Схема:
  1. При /start в личке бот просит пароль.
  2. Кто ввёл верный пароль — навсегда попадает в «белый список»
     (хранится в Google Sheets, лист «Users», переживает рестарты Railway).
  3. В группах доступ не ограничивается — рассчитываем на членство в группе.
  4. ADMINS проходят всегда без пароля.

Переменные окружения:
  ACCESS_PASSWORD — пароль для входа (если не задан, войти могут только ADMINS)
  ADMINS          — id через запятую, например "1450296021,420533161"
"""
import os
import hmac
import logging

import gspread
from google.oauth2.service_account import Credentials
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery
from utils import uz_time

log = logging.getLogger(__name__)


def _uz_now_str() -> str:
    return uz_time().strftime("%d.%m.%Y %H:%M")


class AuthManager:
    """Пароль + сохраняемый в Google Sheets белый список пользователей."""

    def __init__(self, service_file: str, sheet_id: str, password: str, admins):
        self.service_file = service_file
        self.sheet_id = sheet_id
        self.password = password or ""
        self.admins = set(admins or [])
        self._authorized: set[int] = set()
        self._awaiting: set[int] = set()
        self._ws = None  # кэшируем worksheet «Users»
        if not self.password:
            log.warning(
                "ACCESS_PASSWORD не задан — АВТОРИЗАЦИЯ ОТКЛЮЧЕНА (бот открыт для всех). "
                "Задайте ACCESS_PASSWORD в Variables Railway, чтобы включить проверку пароля."
            )

    # ---------------- Google Sheets ----------------
    def _spreadsheet(self):
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(self.service_file, scopes=scopes)
        return gspread.authorize(creds).open_by_key(self.sheet_id)

    def _users_worksheet(self):
        if self._ws is not None:
            return self._ws
        sh = self._spreadsheet()
        try:
            ws = sh.worksheet("Users")
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet("Users", rows=1000, cols=10)
            ws.append_row(["User ID", "Username", "Name", "Added At"])
        self._ws = ws
        return ws

    def load_authorized(self):
        """Загрузить список уже авторизованных из таблицы (при старте)."""
        try:
            ws = self._users_worksheet()
            records = ws.get_all_records()
            ids = {
                int(r["User ID"])
                for r in records
                if str(r.get("User ID", "")).strip().isdigit()
            }
            self._authorized |= ids
            log.info(f"🔐 Авторизованных пользователей загружено: {len(self._authorized)}")
        except Exception as e:
            log.warning(f"Не удалось загрузить лист «Users»: {e}")

    # ---------------- Проверки ----------------
    @property
    def enabled(self) -> bool:
        """True, если пароль задан и гейт активен."""
        return bool(self.password)

    def is_authorized(self, uid: int) -> bool:
        # Пароль не задан → бот открыт, чтобы случайно не закрыть коллектив.
        # Как только ACCESS_PASSWORD появляется в env — проверка включается.
        if not self.enabled:
            return True
        return uid in self._authorized or uid in self.admins

    async def authorize(self, user) -> bool:
        uid = user.id
        if uid in self._authorized:
            return True
        username = user.username or ""
        name = (user.full_name or "").strip()
        try:
            ws = self._users_worksheet()
            ws.append_row([str(uid), username, name, _uz_now_str()])
        except Exception as e:
            log.warning(f"Не удалось записать пользователя {uid} в «Users»: {e}")
        self._authorized.add(uid)
        return True

    def check_password(self, text: str) -> bool:
        if not self.password:
            return False
        return hmac.compare_digest((text or "").strip(), self.password)

    # ---------------- Ожидание ввода пароля (in-memory) ----------------
    def set_awaiting(self, uid: int):
        self._awaiting.add(uid)

    def clear_awaiting(self, uid: int):
        self._awaiting.discard(uid)

    def is_awaiting(self, uid: int) -> bool:
        return uid in self._awaiting


class AuthMiddleware(BaseMiddleware):
    """
    Пропускает всех в группах.
    В личке пускает только авторизованных/админов; остальных — на ввод пароля.
    """

    def __init__(self, auth: AuthManager, main_menu_kb):
        super().__init__()
        self.auth = auth
        self.main_menu_kb = main_menu_kb

    async def _send(self, event: Message, text: str, reply_markup=None):
        try:
            await event.answer(text, reply_markup=reply_markup)
        except Exception:
            pass

    async def __call__(self, handler, event, data):
        user = event.from_user
        if user is None:
            return await handler(event, data)

        # определяем тип чата
        if isinstance(event, Message):
            chat_type = event.chat.type
        elif isinstance(event, CallbackQuery):
            chat_type = event.message.chat.type if event.message else "private"
        else:
            return await handler(event, data)

        # в группах — доверяем членству
        if chat_type in ("group", "supergroup"):
            return await handler(event, data)

        uid = user.id

        # уже авторизован или админ
        if self.auth.is_authorized(uid):
            return await handler(event, data)

        # ---------- неавторизованный в личке ----------
        # CallbackQuery: короткое всплывающее окно
        if isinstance(event, CallbackQuery):
            try:
                await event.answer("🔒 Введите пароль в личных сообщениях бота.", show_alert=True)
            except Exception:
                pass
            self.auth.set_awaiting(uid)
            return

        text = (event.text or "").strip()

        # /start или пустое сообщение → приветствие + запрос пароля
        if text.startswith("/start") or text == "":
            await self._send(
                event,
                "👋 Это внутренний бот жалоб Newton Academy.\n"
                "🔒 Доступ только для сотрудников коллектива.\n\n"
                "Введите пароль для доступа:"
            )
            self.auth.set_awaiting(uid)
            return

        # ждём пароль → проверяем
        if self.auth.is_awaiting(uid):
            if self.auth.check_password(text):
                await self.auth.authorize(user)
                self.auth.clear_awaiting(uid)
                await self._send(
                    event,
                    "✅ Пароль верный. Добро пожаловать!",
                    reply_markup=self.main_menu_kb(),
                )
            else:
                await self._send(
                    event,
                    "❌ Неверный пароль. Попробуйте ещё раз или обратитесь к администратору."
                )
            return

        # прочее — просим пароль
        await self._send(event, "🔒 Бот только для сотрудников. Введите пароль:")
        self.auth.set_awaiting(uid)
        return
