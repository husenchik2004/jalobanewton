# handlers/complaints.py
import asyncio
import time
from aiogram import Router, types, F
from aiogram.filters import CommandStart, Filter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from google_sheets import GoogleSheetsClient
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from utils import uz_time
import re

router = Router()
from aiogram import Bot


# ==========================
# Фильтр: бот ждёт от пользователя текст решения.
# Нужен, чтобы receive_solution не перехватывал вообще любой текст в боте.
#
# 🔧 ФИКС БАГА «все решения пишутся в одну жалобу»:
# раньше ожидание решения НИКОГДА не снималось после первой записи — и все
# последующие сообщения пользователя улетали в ту же старую жалобу.
# Теперь у ожидания есть срок жизни (SOLUTION_WAIT_TIMEOUT), и оно снимается
# сразу после первой же успешной записи решения (см. receive_solution).
# ==========================
SOLUTION_WAIT_TIMEOUT = 15 * 60  # секунд на ввод текста решения


def _drop_stale_solution_wait(bot, user_id: int) -> bool:
    """Снимает протухшее ожидание решения. True — если было протухшее."""
    sw = getattr(bot, "solution_waiting", None)
    if not sw or user_id not in sw:
        return False
    entry = sw[user_id]
    ts = entry.get("ts", 0) if isinstance(entry, dict) else 0
    if ts and time.time() - ts > SOLUTION_WAIT_TIMEOUT:
        sw.pop(user_id, None)
        locks = getattr(bot, "solution_locks", None)
        if locks is not None:
            locks[user_id] = False
        return True
    return False


class AwaitingSolutionFilter(Filter):
    async def __call__(self, message: types.Message) -> bool:
        user = message.from_user
        if user is None:
            return False
        bot = message.bot
        # протухшее ожидание вычищаем и не матчимся
        _drop_stale_solution_wait(bot, user.id)
        sw = getattr(bot, "solution_waiting", None)
        return bool(sw and user.id in sw)

# Инициализация глобальных контейнеров для блокировок и ожиданий
def setup_bot_memory(bot: Bot):
    if not hasattr(bot, "solution_locks"):
        bot.solution_locks = {}
    if not hasattr(bot, "solution_waiting"):
        bot.solution_waiting = {}
    if not hasattr(bot, "solution_messages"):
        bot.solution_messages = {}
    if not hasattr(bot, "notify_messages"):
        bot.notify_messages = {}

# ==========================
# FSM состояния анкеты
# ==========================
class ComplaintForm(StatesGroup):
    branch = State()
    parent = State()
    student = State()
    phone = State()
    category = State()
    description = State()
    awaiting_media = State() 
    confirm = State()
    # временное поле для добавления решения
    editing_solution_for = State()

# ==========================
# Категории
# ==========================
CATEGORIES = {
    "teacher": "Учитель — поведение/качество",
    "schedule": "Расписание — занятия/замены",
    "payment": "Оплата — квитанции/возвраты",
    "infrastructure": "Инфраструктура — класс/оборудование",
    "safety": "Безопасность — инциденты",
    "administration": "Администрация — общие вопросы",
    "other": "Другое"
}

def main_menu_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="📋 Новая жалоба")],
            [KeyboardButton(text="📘 Инструкция по использованию")],
            [KeyboardButton(text="📊 Статистика")]
        ]
    )


def make_categories_keyboard():
    keyboard = [
        [InlineKeyboardButton(text=title, callback_data=f"cat:{code}")]
        for code, title in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

# ==========================
# Генерация "красивого" ID A-1, A-2...
# ==========================
def generate_pretty_id(gs_client: GoogleSheetsClient) -> str:
    """Генерирует новый ID без get_all_records()"""
    try:
        values = gs_client.sheet.col_values(1)  # читаем только первый столбец
        ids = [v for v in values if v.startswith("A-")]
        if not ids:
            return "A-1"
        last_num = max(int(i.split("-")[1]) for i in ids if i.split("-")[1].isdigit())
        return f"A-{last_num + 1}"
    except Exception as e:
        print(f"⚠️ generate_pretty_id error: {e}")
        return f"A-{uz_time().strftime('%y%m%d%H%M%S')}"


# ==========================
# Показ предпросмотра (определён ДО обработчиков, чтобы не было NameError)
# ==========================
async def show_complaint_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()

    # Получаем новый ID через gs (чтобы не коллизировало)
    try:
        gs_client = GoogleSheetsClient(message.bot.config["SERVICE_ACCOUNT_FILE"], message.bot.config["GOOGLE_SHEET_ID"])
        complaint_id = generate_pretty_id(gs_client)
    except Exception:
        # fallback
        complaint_id = f"A-{uz_time().strftime('%y%m%d%H%M%S')}"

    await state.update_data(id=complaint_id)

    branch = data.get("branch", "-")
    parent = data.get("parent", "-")
    student = data.get("student", "-")
    phone = data.get("phone", "-")
    category = data.get("category", "-")
    description = data.get("description", "-")
    media_type = data.get("media_type")
    media_id = data.get("media_id")

    phone_display = phone or "—"

    preview = (
        "<b>📋 Проверьте данные жалобы:</b>\n\n"
        f"🏫 Филиал: {branch}\n"
        f"👤 Родитель: {parent or '—'}\n"
        f"🧒 Ученик: {student or '—'}\n"
        f"☎️ Телефон: {phone_display}\n"
        f"📂 Категория: {category}\n"
        f"✍️ Жалоба: {description}"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send")],
        [InlineKeyboardButton(text="✏️ Изменить анкету", callback_data="edit_form")]
    ])

    # Попытка убрать клавиатуру у предыдущего сообщения с клавиатурой (если это было наше сообщение)
    try:
        # если callback message имеет reply_markup - можно очистить (best-effort)
        await message.edit_reply_markup(reply_markup=None)
    except:
        pass

    try:
        if media_type == "photo":
            await message.answer_photo(media_id, caption=preview, parse_mode="HTML", reply_markup=kb)
        elif media_type == "video":
            await message.answer_video(media_id, caption=preview, parse_mode="HTML", reply_markup=kb)
        else:
            await message.answer(preview, parse_mode="HTML", reply_markup=kb)
    except Exception:
        await message.answer(preview, parse_mode="HTML", reply_markup=kb)

    await state.set_state(ComplaintForm.confirm)

# ==========================
# Хендлеры — стартовая логика
# ==========================
from aiogram.types import FSInputFile

@router.message(F.text == "📘 Инструкция по использованию")
async def send_instruction(message: types.Message):

    pdf_path = "Инструкция_по_использованию.pdf"

    try:
        file = FSInputFile(pdf_path)
        await message.answer_document(
            document=file,
            caption="📘 Пожалуйста, ознакомьтесь с инструкцией перед использованием бота."
        )
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при отправке файла: {e}")

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Это бот фиксации жалоб.\nНажми «📋 Новая жалоба», чтобы начать.",
        reply_markup=main_menu_kb()
    )

@router.message(F.text == "📋 Новая жалоба")
async def start_form(message: types.Message, state: FSMContext):
    branches = ["Ракат", "Ганга", "Паркент", "Чиланзар", "Сергели"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b, callback_data=f"branch:{b}")] for b in branches
    ])
    await message.answer("🏫 Выберите филиал:", reply_markup=kb)
    await state.set_state(ComplaintForm.branch)

@router.callback_query(F.data.startswith("branch:"))
async def branch_selected(callback: types.CallbackQuery, state: FSMContext):
    branch = callback.data.split(":", 1)[1]
    await state.update_data(branch=branch)
    await callback.message.answer("👩‍👦 Введите ФИО родителя (или оставьте '-' если нет):")
    await state.set_state(ComplaintForm.parent)
    try:
        await callback.answer()
    except:
        pass

@router.message(ComplaintForm.parent)
async def get_parent(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    if txt == "-" or txt.lower() in ("не указывать", "нет"):
        txt = ""
    await state.update_data(parent=txt)

    data = await state.get_data()
    # Если мы в режиме редактирования — сразу показываем предпросмотр и уходим
    if data.get("editing"):
        await state.update_data(editing=False)
        await show_complaint_preview(message, state)
        return

    await message.answer("🧒 Введите ФИО ученика и класс (или оставьте '-' если нет):")
    await state.set_state(ComplaintForm.student)


@router.message(ComplaintForm.student)
async def get_student(message: types.Message, state: FSMContext):
    txt = message.text.strip()
    if txt == "-" or txt.lower() in ("не указывать", "нет"):
        txt = ""
    await state.update_data(student=txt)

    data = await state.get_data()
    if data.get("editing"):
        await state.update_data(editing=False)
        await show_complaint_preview(message, state)
        return

    await message.answer("📞 Введите номер телефона родителя:")
    await state.set_state(ComplaintForm.phone)


@router.message(ComplaintForm.phone)
async def get_phone(message: types.Message, state: FSMContext):
    raw = message.text.strip()
    digits = re.sub(r"\D", "", raw)

    if len(digits) == 9:
        phone_norm = f"+998{digits}"
    elif len(digits) == 12 and digits.startswith("998"):
        phone_norm = f"+{digits}"
    elif raw.startswith("+998") and len(digits) == 12:
        phone_norm = f"+{digits}"
    else:
        await message.answer("❌ Неправильный номер. Введите корректный телефон (например: 91 123 4567 или +998911234567).")
        return

    if not re.match(r"^\+998\d{9}$", phone_norm):
        await message.answer("❌ Неправильный номер. Введите корректный телефон (например: 91 123 4567 или +998911234567).")
        return

    await state.update_data(phone=phone_norm)

    data = await state.get_data()
    if data.get("editing"):
        await state.update_data(editing=False)
        await show_complaint_preview(message, state)
        return

    await message.answer("📂 Выберите категорию жалобы:", reply_markup=make_categories_keyboard())
    await state.set_state(ComplaintForm.category)


@router.callback_query(F.data.startswith("cat:"))
async def select_category(callback: types.CallbackQuery, state: FSMContext):
    code = callback.data.split(":", 1)[1]
    category_text = CATEGORIES.get(code, "Другое")
    await state.update_data(category=category_text)
    await callback.message.answer("📝 Опишите суть жалобы (минимум 3 символа):")
    await state.set_state(ComplaintForm.description)
    try:
        await callback.answer()
    except:
        pass

# ==========================
# Описание жалобы + предложение медиа
# — защищаем от случайного перезаписи, если бот уже ждет медиа
# ==========================
@router.message(ComplaintForm.description, F.text)
async def get_text_description(message: types.Message, state: FSMContext):
    data = await state.get_data()

    # Если уже есть description и мы уже предложили медиа — предупреждаем
    if data.get("description") and data.get("awaiting_media"):
        await message.answer("⚠️ Сейчас бот ждёт прикрепления медиа (фото/видео) или нажмите «⏭ Пропустить».")
        return

    text = message.text.strip()
    if len(text) < 3:
        await message.answer("❌ Пожалуйста, опишите жалобу подробнее (минимум 3 символа).")
        return

    await state.update_data(description=text)

    # Если мы в режиме редактирования — сразу покажем предпросмотр и выйдем
    if data.get("editing"):
        await state.update_data(editing=False)
        await show_complaint_preview(message, state)
        return

    # спросим про медиа и переведём в отдельное состояние awaiting_media
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📸 Добавить фото", callback_data="add_photo"),
            InlineKeyboardButton(text="🎥 Добавить видео", callback_data="add_video")
        ],
        [InlineKeyboardButton(text="⏭ Пропустить", callback_data="skip_media")]
    ])
    # пометим в data, что предложение сделано
    await state.update_data(awaiting_media=True)
    await message.answer("📎 Хотите прикрепить фото или видео к жалобе?", reply_markup=kb)
    await state.set_state(ComplaintForm.awaiting_media)

# ==========================
# add photo / add video
# ==========================
@router.callback_query(F.data == "add_photo")
async def add_photo_request(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(awaiting_media="photo")
    await callback.message.answer("📸 Отправьте фото, которое нужно прикрепить к жалобе:")
    await state.set_state(ComplaintForm.awaiting_media)
    try:
        await callback.answer()
    except:
        pass

@router.callback_query(F.data == "add_video")
async def add_video_request(callback: types.CallbackQuery, state: FSMContext):
    await state.update_data(awaiting_media="video")
    await callback.message.answer("🎥 Отправьте видео, которое нужно прикрепить к жалобе:")
    await state.set_state(ComplaintForm.awaiting_media)
    try:
        await callback.answer()
    except:
        pass

@router.callback_query(F.data == "skip_media")
async def skip_media_request(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    # защита от двойного нажатия — в памяти бота
    if not hasattr(callback.bot, "_skip_cache"):
        callback.bot._skip_cache = set()
    key = f"skip:{user_id}"
    if key in callback.bot._skip_cache:
        try:
            await callback.answer("Уже обрабатывается.")
        except:
            pass
        return
    callback.bot._skip_cache.add(key)

    # снимаем ожидание и показываем предпросмотр
    await state.update_data(awaiting_media=None)
    await show_complaint_preview(callback.message, state)

    # убираем флаг через небольшую задержку (чтобы защитить от быстрого повторного нажатия)
    async def _clear():
        await asyncio.sleep(2)
        try:
            callback.bot._skip_cache.discard(key)
        except:
            pass
    asyncio.create_task(_clear())

    try:
        await callback.answer()
    except:
        pass


# ==========================
# Получаем фото/видео
# ==========================
@router.message(ComplaintForm.awaiting_media, F.photo | F.video)
async def handle_media(message: types.Message, state: FSMContext):
    data = await state.get_data()
    awaiting = data.get("awaiting_media")

    # Если мы не ожидали медиа — подсказать
    if not awaiting:
        await message.answer("⚠️ Чтобы прикрепить медиа к жалобе, нажмите соответствующую кнопку.")
        return

    if message.photo:
        file_id = message.photo[-1].file_id
        await state.update_data(media_type="photo", media_id=file_id)
    elif message.video:
        file_id = message.video.file_id
        await state.update_data(media_type="video", media_id=file_id)

    # сброс ожидания медиа
    await state.update_data(awaiting_media=None)

    # Если редактирование — показать предпросмотр и выйти
    data = await state.get_data()
    if data.get("editing"):
        await state.update_data(editing=False)
        await show_complaint_preview(message, state)
        return

    await message.answer("✅ Медиа добавлено.")
    await show_complaint_preview(message, state)

@router.message(ComplaintForm.awaiting_media, F.text)
async def awaiting_media_text(message: types.Message, state: FSMContext):
    txt = message.text.strip().lower()
    # если пользователь пишет явное "пропустить" — считаем как skip
    if txt in ("пропустить", "skip", "⏭", "нет", "-"):
        # симулируем нажатие skip: просто покажем предпросмотр
        await state.update_data(awaiting_media=None)
        await show_complaint_preview(message, state)
        return

    # иначе подсказка: не перезаписываем описание в этом состоянии
    await message.answer("⚠️ Сейчас бот ждёт медиа (фото/видео) или нажмите «⏭ Пропустить». Чтобы изменить текст жалобы — сначала нажмите «✏️ Изменить анкету».")

# ==========================
# Изменить анкету — начать заново
# ==========================
@router.callback_query(F.data == "edit_form")
async def edit_form(callback: types.CallbackQuery, state: FSMContext):
    """
    Теперь кнопка «Изменить анкету» полностью перезапускает процесс заполнения
    заново, а не предлагает выбор полей.
    """
    await state.clear()
    await callback.message.answer(
        "🔁 Хорошо, начнём заполнение анкеты заново.\n\n🏫 Выберите филиал:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=b, callback_data=f"branch:{b}")]
                for b in ["Ракат", "Ганга", "Паркент", "Чиланзар", "Сергели"]
            ]
        )
    )
    await state.set_state(ComplaintForm.branch)
    try:
        await callback.answer()
    except:
        pass




# ==========================
# Подтверждение и отправка жалобы
# — защита от повторной отправки: _sent_ids
# ==========================
@router.callback_query(F.data == "confirm_send")
async def confirm_send(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer("⏳ Отправляю жалобу...")
    except:
        pass

    data = await state.get_data()
    if data.get("sending_in_progress"):
        await callback.message.answer("⚠️ Жалоба уже отправляется, подождите пару секунд.")
        return

    await state.update_data(sending_in_progress=True)
    complaint_id = data.get("id") or f"A-{uz_time().strftime('%y%m%d%H%M%S')}"
    date_str = uz_time().strftime("%d.%m.%Y %H:%M")


    branch = data.get("branch", "-")
    parent = data.get("parent", "-")
    student = data.get("student", "-")
    phone = data.get("phone", "-")
    category = data.get("category", "-")
    description = data.get("description", "-")
    media_type = data.get("media_type")
    media_id = data.get("media_id")

    sender_name = callback.from_user.full_name or ""
    sender_username = f"@{callback.from_user.username}" if callback.from_user.username else ""
    sender_id = callback.from_user.id

    msg = (
        "<b>📋 Новая жалоба</b>\n"
        f"<b>ID:</b> {complaint_id}\n\n"
        f"🏫 <b>Филиал:</b> {branch}\n"
        f"👩‍👦 <b>Родитель:</b> {parent}\n"
        f"🧒 <b>Ученик:</b> {student}\n"
        f"☎️ <b>Телефон:</b> {phone}\n"
        f"📂 <b>Категория:</b> {category}\n"
        f"✍️ <b>Жалоба:</b> {description}\n\n"
        f"👤 <b>Отправитель:</b> {sender_name} {sender_username}\n"
        f"🆔 <code>{sender_id}</code>"
    )

    try:
        gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
        gs.add_complaint({
            "ID": complaint_id,
            "Дата": date_str,
            "Филиал": branch,
            "Родитель": parent,
            "Ученик": student,
            "Телефон": phone,
            "Категория": category,
            "Жалоба": description,
            "Статус": "Ожидает обзвона",
            "Решение": "",
            "Ответственный": "",
            "Отправитель": sender_name,
            "User ID": str(sender_id)
        })
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при сохранении в таблицу: {e}")
        await state.update_data(sending_in_progress=False)
        return

    group_id = callback.bot.config["GROUP_COMPLAINTS_ID"]
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📞 Перезвонили родителю", callback_data=f"called:{complaint_id}")]
    ])

    try:
        if media_type == "photo":
            await callback.bot.send_photo(group_id, media_id, caption=msg, parse_mode="HTML", reply_markup=kb)
        elif media_type == "video":
            await callback.bot.send_video(group_id, media_id, caption=msg, parse_mode="HTML", reply_markup=kb)
        else:
            await callback.bot.send_message(group_id, msg, parse_mode="HTML", reply_markup=kb)

        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer("✅ Жалоба успешно отправлена и сохранена.", reply_markup=main_menu_kb())
        await state.clear()
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при отправке в группу: {e}")
        await state.update_data(sending_in_progress=False)

# ==========================
# Перезвонили — обновление и пересылка в РЕШЕНИЯ (с кнопкой "Добавить решение")
# ==========================
@router.callback_query(F.data.startswith("called:"))
async def called_handler(callback: types.CallbackQuery):
    try:
        await callback.answer("⏳ Обрабатываю...")
    except:
        pass

    cid = callback.data.split(":", 1)[1]
    now = uz_time().strftime("%d.%m.%Y %H:%M")


    # защита от двойного нажатия
    if not hasattr(callback.bot, "_called_ids"):
        callback.bot._called_ids = set()
    if cid in callback.bot._called_ids:
        await callback.answer("Уже обработано.")
        return
    callback.bot._called_ids.add(cid)

    # обновляем таблицу
    try:
        gs = GoogleSheetsClient(
            callback.bot.config["SERVICE_ACCOUNT_FILE"],
            callback.bot.config["GOOGLE_SHEET_ID"]
        )
        gs.update_by_id(cid, {"Статус": "Принята", "Время обзвона": now})
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка обновления таблицы: {e}")
        return

    # удаляем клавиатуру
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except:
        pass

    # обновляем подпись
    complaint_text = callback.message.caption or callback.message.text or ""
    new_text = complaint_text + f"\n☎️ <b>Перезвонили:</b> {now}"

    try:
        if callback.message.caption:
            await callback.message.edit_caption(new_text, parse_mode="HTML")
        elif callback.message.text:
            await callback.message.edit_text(new_text, parse_mode="HTML")
    except:
        pass

    reply_markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Добавить решение", callback_data=f"solution:{cid}")]
    ])

    group_solutions = callback.bot.config.get("GROUP_SOLUTIONS_ID")
    msg_to_send = f"📤 Жалоба ID {cid} передана в «РЕШЕНИЯ».\n\n{new_text}"

    sent_msg = None
    try:
        if callback.message.photo:
            sent_msg = await callback.bot.send_photo(
                group_solutions,
                callback.message.photo[-1].file_id,
                caption=msg_to_send,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        elif getattr(callback.message, "video", None):
            sent_msg = await callback.bot.send_video(
                group_solutions,
                callback.message.video.file_id,
                caption=msg_to_send,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
        else:
            sent_msg = await callback.bot.send_message(
                group_solutions,
                msg_to_send,
                parse_mode="HTML",
                reply_markup=reply_markup
            )

        # сохраняем ID отправленного сообщения
        if not hasattr(callback.bot, "solution_messages"):
            callback.bot.solution_messages = {}
        callback.bot.solution_messages[cid] = {
            "chat_id": group_solutions,
            "message_id": sent_msg.message_id
        }

    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка при пересылке в группу решений: {e}")

    await callback.answer("✅ Жалоба передана в «РЕШЕНИЯ».")


# ==========================
# Нажали "Добавить решение" — ждем ввод текста решения
# ==========================
@router.callback_query(F.data.startswith("solution:"))
async def add_solution(callback: types.CallbackQuery, state: FSMContext = None):
    cid = callback.data.split(":")[1]
    user_id = callback.from_user.id

    # создаем общий контейнер, если нет
    if not hasattr(callback.bot, "solution_locks"):
        callback.bot.solution_locks = {}

    # 🔧 чистим протухшее ожидание (15 мин не вводили текст — освобождаем)
    _drop_stale_solution_wait(callback.bot, user_id)

    # 🚫 Если пользователь уже нажал кнопку и бот ждет от него текст — игнорируем
    if callback.bot.solution_locks.get(user_id):
        await callback.answer("⏳ Вы уже добавляете решение. Завершите ввод или подождите.", show_alert=True)
        return

    # 🔐 Устанавливаем блокировку
    callback.bot.solution_locks[user_id] = True

    # ✉️ Просим текст решения
    await callback.message.answer(f"✍️ Введите текст решения по жалобе ID {cid}:")

    # 🧠 Сохраняем в состояние, кого ждем (+ метка времени для авто-очистки)
    callback.bot.solution_waiting[user_id] = {"cid": cid, "ts": time.time()}

    # ❌ Удаляем inline-кнопку "Добавить решение", чтобы её нельзя было нажать повторно
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # безопасно закрываем callback
    try:
        await callback.answer()
    except:
        pass
# ==========================
# Обработка текста решения — отправка в РЕШЕНИЯ и ЖАЛОБЫ
# ==========================
@router.message(AwaitingSolutionFilter(), F.text)
async def receive_solution(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    bot = message.bot

    cid = bot.solution_waiting[user_id]["cid"]
    solution_text = message.text.strip()
    if len(solution_text) < 3:
        await message.answer("❌ Решение слишком короткое, напишите подробнее.")
        return

    now = uz_time().strftime("%d.%m.%Y %H:%M")

    responsible_name = message.from_user.full_name or "Без имени"
    username = f"@{message.from_user.username}" if message.from_user.username else ""
    responsible_display = f"{responsible_name} {username}".strip()

    # === Обновляем данные в Google Sheets ===
    gs = GoogleSheetsClient(bot.config["SERVICE_ACCOUNT_FILE"], bot.config["GOOGLE_SHEET_ID"])
    row_index, complaint = gs.get_row_by_id(cid)
    if not complaint:
        await message.answer(f"⚠️ Жалоба с ID {cid} не найдена в таблице.")
        bot.solution_locks[user_id] = False
        bot.solution_waiting.pop(user_id, None)
        return

    gs.update_by_id(cid, {
        "Решение": solution_text,
        "Ответственный": responsible_display,
        "Время решения": now,
        "Статус": "Ожидает уведомления"
    })

    # Берём время обзвона, если есть
    call_time = complaint.get("Время обзвона", "—")

    # === Сообщение в группу РЕШЕНИЯ (без кнопки, оформлено красиво) ===
    msg_text_full = (
        f"📤 <b>Жалоба ID {cid}</b> передана в <b>«РЕШЕНИЯ»</b>\n\n"
        f"📋 <b>Новая жалоба</b>\n\n"
        f"🏫 <b>Филиал:</b> {complaint.get('Филиал', '-')}\n"
        f"👩‍👦 <b>Родитель:</b> {complaint.get('Родитель', '-')}\n"
        f"🧒 <b>Ученик:</b> {complaint.get('Ученик', '-')}\n"
        f"☎️ <b>Телефон:</b> {complaint.get('Телефон', '-')}\n"
        f"📂 <b>Категория:</b> {complaint.get('Категория', '-')}\n"
        f"✍️ <b>Жалоба:</b> {complaint.get('Жалоба', '-')}\n\n"
        f"👤 <b>Отправитель:</b> {complaint.get('Отправитель', '-')}\n"
        f"🆔 {complaint.get('User ID', '-')}\n"
        f"☎️ <b>Перезвонили:</b> {call_time}\n\n"
        f"💬 <b>Решение:</b> {solution_text}\n"
        f"👤 <b>Ответственный:</b> {responsible_display}\n"
        f"🕒 <b>Время решения:</b> {now}\n\n"
        f"✅ Жалоба передана обратно в группу обзвона для уведомления родителя."
    )

    group_solutions = bot.config["GROUP_SOLUTIONS_ID"]

    # Удаляем старое сообщение, если оно было
    if hasattr(bot, "solution_messages") and cid in bot.solution_messages:
        old_msg = bot.solution_messages[cid]
        try:
            await bot.delete_message(old_msg["chat_id"], old_msg["message_id"])
        except Exception:
            pass

    sent_msg = await bot.send_message(group_solutions, msg_text_full, parse_mode="HTML")
    bot.solution_messages[cid] = {"chat_id": group_solutions, "message_id": sent_msg.message_id}

    # === Сообщение в группу ЖАЛОБЫ (с кнопкой и коротко) ===
    msg_text_short = (
        f"📋 <b>Жалоба ID {cid}</b>\n"
        f"💬 <b>Решение:</b> {solution_text}\n"
        f"👤 <b>Ответственный:</b> {responsible_display}\n"
        f"🕒 <b>Время решения:</b> {now}\n\n"
        f"☎️ Необходимо сообщить родителю о решении жалобы."
    )

    notify_button = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📨 Сообщили родителю о решении!", callback_data=f"notify_parent:{cid}")]
    ])

    group_complaints = bot.config["GROUP_COMPLAINTS_ID"]

    # ✅ КЛЮЧЕВЫЙ ФИКС: снимаем ожидание и блокировку СРАЗУ после записи.
    # Раньше они не снимались никогда — из-за этого все последующие сообщения
    # пользователя записывались в ту же самую жалобу и «рассылались» по группам.
    try:
        sent_complaint = await bot.send_message(group_complaints, msg_text_short, parse_mode="HTML", reply_markup=notify_button)
        if not hasattr(bot, "notify_messages"):
            bot.notify_messages = {}
        bot.notify_messages[cid] = {"chat_id": group_complaints, "message_id": sent_complaint.message_id}
        await message.answer(f"✅ Решение по жалобе {cid} сохранено.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при отправке решения в группы: {e}")
    finally:
        bot.solution_waiting.pop(user_id, None)
        bot.solution_locks[user_id] = False


# ==========================
# Сообщить родителю о решении — обновление сообщения
# ==========================
@router.callback_query(F.data.startswith("notify_parent:"))
async def notify_parent(callback: types.CallbackQuery):
    cid = callback.data.split(":")[1]
    now = uz_time().strftime("%d.%m.%Y %H:%M")

    user_name = callback.from_user.full_name or "Без имени"
    username = f"@{callback.from_user.username}" if callback.from_user.username else ""
    display_name = f"{user_name} {username}".strip()

    gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
    gs.update_by_id(cid, {
        "Статус": "Закрыта",
        "Время уведомления": now,
        "Кто уведомил родителя": display_name
    })

    # Обновляем сообщение в группе ЖАЛОБЫ
    text = callback.message.text or callback.message.caption or ""
    text += (
        f"\n\n✅ <b>Родитель уведомлён:</b> {now}\n"
        f"👤 <b>Уведомил:</b> {display_name}\n"
        f"💚 <b>Жалоба полностью закрыта.</b>"
    )

    try:
        if callback.message.caption:
            await callback.message.edit_caption(text, parse_mode="HTML", reply_markup=None)
        else:
            await callback.message.edit_text(text, parse_mode="HTML", reply_markup=None)
        await callback.answer("✅ Родителю сообщили. Жалоба закрыта.")
    except Exception as e:
        await callback.answer(f"⚠️ Ошибка при обновлении сообщения: {e}")


# ==========================
# Fallback в ЛС: любой непонятный текст — подсказка про меню.
# Срабатывает только в приватных чатах и только если ни один хендлер выше не подошёл.
# ==========================
@router.message(F.text, F.chat.type == "private")
async def fallback_private(message: types.Message):
    await message.answer(
        "🤖 Не понимаю это сообщение. Воспользуйтесь кнопками меню 👇\n"
        "Если меню пропало — нажмите /start.",
        reply_markup=main_menu_kb()
    )
