import pandas as pd
from aiogram import Router, types, F
from google_sheets import GoogleSheetsClient
from datetime import datetime

router = Router()

# ==============================
# ⚙️ Вспомогательные функции
# ==============================
def format_progress(closed, total):
    """Показывает процент выполнения"""
    if total == 0:
        return "0%"
    return f"{round((closed / total) * 100)}%"

def generate_summary(df):
    """Создаёт общий аналитический вывод"""
    if df.empty:
        return "\n⚠️ Нет данных для анализа."

    branch_counts = df["Филиал"].value_counts()
    max_branch = branch_counts.idxmax()
    min_branch = branch_counts.idxmin()
    last_date = pd.to_datetime(df["Дата"], errors="coerce").max().strftime("%d.%m.%Y")

    return (
        f"\n━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Больше всего жалоб:</b> {max_branch} ({branch_counts[max_branch]})\n"
        f"📉 <b>Меньше всего жалоб:</b> {min_branch} ({branch_counts[min_branch]})\n"
        f"📅 <b>Последняя активность:</b> {last_date}"
    )

# ==============================
# 🔒 Проверка, что пользователь — админ
# ==============================
async def is_admin(bot, user_id: int) -> bool:
    """Проверяет, является ли пользователь админом в группе жалоб"""
    try:
        group_id = bot.config["GROUP_SOLUTIONS_ID"]
        admins = await bot.get_chat_administrators(group_id)
        admin_ids = [admin.user.id for admin in admins]
        return user_id in admin_ids
    except Exception as e:
        print(f"⚠️ Ошибка проверки прав: {e}")
        return False

# ==============================
# 📊 Общая статистика
# ==============================
@router.message(F.text == "📊 Статистика")
async def show_main_statistics(message: types.Message):
    if message.chat.type != "private":
        await message.answer("📊 Статистику можно запросить только через личные сообщения с ботом.")
        return

    if not await is_admin(message.bot, message.from_user.id):
        await message.answer("⛔ У вас нет прав для просмотра статистики.")
        return

    try:
        gs = GoogleSheetsClient(message.bot.config["SERVICE_ACCOUNT_FILE"], message.bot.config["GOOGLE_SHEET_ID"])
        df = gs.get_all_data()
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при загрузке данных: {e}")
        return

    if df.empty:
        await message.answer("⚠️ Данных пока нет.")
        return

    total = len(df)
    waiting = (df["Статус"] == "Ожидает обзвона").sum()
    called = (df["Статус"] == "Принята").sum()
    solution = (df["Статус"] == "Ожидает уведомления").sum()
    notified = (df["Статус"] == "Закрыта").sum()
    progress = format_progress(notified, total)

    text = (
        "<b>📊 ОБЩАЯ СТАТИСТИКА</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Всего жалоб: {total}\n"
        f"📞 Ожидают перезвона: {waiting}\n"
        f"💬 Ожидают решения: {called}\n"
        f"🪪 Ожидают уведомления: {solution}\n"
        f"✅ Закрыто: {notified}\n"
        f"📈 Прогресс закрытия: {progress}"
    )

    text += generate_summary(df)

    kb = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🏫 По филиалам", callback_data="stats_by_branch")],
        [types.InlineKeyboardButton(text="📂 По категориям", callback_data="stats_by_category")],
        [types.InlineKeyboardButton(text="📅 По датам", callback_data="stats_by_date")],
        [types.InlineKeyboardButton(text="📥 Скачать Excel", callback_data="stats_download")]
    ])

    await message.answer(text, parse_mode="HTML", reply_markup=kb)

# ==============================
# 🏫 По филиалам
# ==============================
@router.callback_query(F.data == "stats_by_branch")
async def stats_by_branch(callback: types.CallbackQuery):
    if not await is_admin(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
    df = gs.get_all_data()
    if df.empty:
        await callback.message.answer("⚠️ Данных нет.")
        return

    text = "<b>🏫 СТАТИСТИКА ПО ФИЛИАЛАМ</b>\n━━━━━━━━━━━━━━━━━━━"
    for branch, b_df in df.groupby("Филиал"):
        total = len(b_df)
        waiting = (b_df["Статус"] == "Ожидает обзвона").sum()
        called = (b_df["Статус"] == "Принята").sum()
        solution = (b_df["Статус"] == "Ожидает уведомления").sum()
        notified = (b_df["Статус"] == "Закрыта").sum()
        progress = format_progress(notified, total)

        text += (
            f"\n\n🏫 <b>{branch}</b>\n"
            f"📋 Всего: {total}\n"
            f"📞 Перезвон: {waiting}\n"
            f"💬 Решение: {called}\n"
            f"🪪 Уведомление: {solution}\n"
            f"✅ Закрыто: {notified}\n"
            f"📈 Прогресс закрытия: {progress}"
        )

    text += generate_summary(df)
    await callback.message.answer(text, parse_mode="HTML")

# ==============================
# 📂 По категориям (Учитель, Расписание, и т.д.)
# ==============================
@router.callback_query(F.data == "stats_by_category")
async def stats_by_category(callback: types.CallbackQuery):
    if not await is_admin(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    try:
        gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
        df = gs.get_all_data()
    except Exception as e:
        await callback.message.answer(f"⚠️ Ошибка загрузки данных: {e}")
        return

    if df.empty or "Категория" not in df.columns:
        await callback.message.answer("⚠️ Нет данных по категориям.")
        return

    categories_order = [
        "Учитель — поведение/качество",
        "Расписание — занятия/замены",
        "Оплата — квитанции/возвраты",
        "Инфраструктура — класс/оборудование",
        "Безопасность — инциденты",
        "Администрация — общие вопросы",
        "Другое"
    ]

    text = "<b>📂 СТАТИСТИКА ПО КАТЕГОРИЯМ</b>\n━━━━━━━━━━━━━━━━━━━"
    cat_summary = {}

    for cat in categories_order:
        c_df = df[df["Категория"] == cat]
        if c_df.empty:
            continue

        total = len(c_df)
        waiting = (c_df["Статус"] == "Ожидает обзвона").sum()
        called = (c_df["Статус"] == "Принята").sum()
        solution = (c_df["Статус"] == "Ожидает уведомления").sum()
        notified = (c_df["Статус"] == "Закрыта").sum()
        progress = format_progress(notified, total)

        cat_summary[cat] = total

        text += (
            f"\n\n📂 <b>{cat}</b>\n"
            f"📋 Всего жалоб: {total}\n"
            f"📞 Ожидают перезвона: {waiting}\n"
            f"💬 Ожидают решения: {called}\n"
            f"🪪 Ожидают уведомления: {solution}\n"
            f"✅ Закрыто: {notified}\n"
            f"📈 Прогресс закрытия: {progress}"
        )

    if not cat_summary:
        await callback.message.answer("⚠️ Нет данных по категориям.")
        return

    most_complaints_cat = max(cat_summary, key=cat_summary.get)
    least_complaints_cat = min(cat_summary, key=cat_summary.get)
    most_count = cat_summary[most_complaints_cat]
    least_count = cat_summary[least_complaints_cat]

    df["Дата"] = pd.to_datetime(df["Дата"], errors="coerce")
    last_date = df["Дата"].max().strftime("%d.%m.%Y")

    text += (
        "\n━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>Больше всего жалоб:</b> {most_complaints_cat} ({most_count})\n"
        f"📉 <b>Меньше всего жалоб:</b> {least_complaints_cat} ({least_count})\n"
        f"📅 <b>Последняя активность:</b> {last_date}"
    )

    await callback.message.answer(text, parse_mode="HTML")

# ==============================
# 📅 По датам
# ==============================
@router.callback_query(F.data == "stats_by_date")
async def stats_by_date(callback: types.CallbackQuery):
    if not await is_admin(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
    df = gs.get_all_data()
    if df.empty or "Дата" not in df.columns:
        await callback.message.answer("⚠️ Нет данных по датам.")
        return

    df["Дата"] = pd.to_datetime(df["Дата"], errors="coerce")
    last_7 = df[df["Дата"] >= datetime.now() - pd.Timedelta(days=7)]

    total = len(last_7)
    waiting = (last_7["Статус"] == "Ожидает обзвона").sum()
    called = (last_7["Статус"] == "Принята").sum()
    solution = (last_7["Статус"] == "Ожидает уведомления").sum()
    notified = (last_7["Статус"] == "Закрыта").sum()
    progress = format_progress(notified, total)

    text = (
        "<b>📅 СТАТИСТИКА ЗА 7 ДНЕЙ</b>\n━━━━━━━━━━━━━━━━━━━\n"
        f"📋 Всего жалоб: {total}\n"
        f"📞 Перезвон: {waiting}\n"
        f"💬 Решение: {called}\n"
        f"🪪 Уведомление: {solution}\n"
        f"✅ Закрыто: {notified}\n"
        f"📈 Прогресс закрытия: {progress}"
    )

    text += generate_summary(last_7)
    await callback.message.answer(text, parse_mode="HTML")

# ==============================
# 📥 Скачать Excel
# ==============================
@router.callback_query(F.data == "stats_download")
async def stats_download(callback: types.CallbackQuery):
    if not await is_admin(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Нет доступа.", show_alert=True)
        return

    gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
    df = gs.get_all_data()
    if df.empty:
        await callback.message.answer("⚠️ Нет данных для выгрузки.")
        return

    file_path = "/tmp/statistics.xlsx"
    df.to_excel(file_path, index=False)

    await callback.message.answer_document(
        document=types.FSInputFile(file_path),
        caption="📊 Полный отчёт по жалобам."
    )
