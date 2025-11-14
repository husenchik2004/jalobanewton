# handlers/callbacks.py
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from datetime import datetime
from google_sheets import GoogleSheetsClient
from aiogram.fsm.context import FSMContext
router = Router()

# -------------------------
# 📞 Нажали "Перезвонили родителю"
# -------------------------
@router.callback_query(F.data.startswith("called:"))
async def mark_called(callback: types.CallbackQuery):
    cid = callback.data.split(":", 1)[1]
    now = datetime.now().strftime("%d.%m.%Y %H:%M")

    # простая защита от повторной обработки одного ID
    if not hasattr(callback.bot, "_called_ids"):
        callback.bot._called_ids = set()
    if cid in callback.bot._called_ids:
        try:
            await callback.answer("Уже обработано ✅", show_alert=False)
        except:
            pass
        return
    callback.bot._called_ids.add(cid)

    # Попытка обновить Google Sheets (не падаем, если ошибка)
    try:
        gs = GoogleSheetsClient(callback.bot.config["SERVICE_ACCOUNT_FILE"], callback.bot.config["GOOGLE_SHEET_ID"])
        gs.update_by_id(cid, {"Статус": "Принята", "Время обзвона": now})
    except Exception as e:
        # информируем пользователя, но не останавливаем дальнейшие действия
        try:
            await callback.answer("⚠️ Не удалось обновить таблицу", show_alert=True)
        except:
            pass
        # можно уведомить лидеров, если настроено
        if callback.bot.config.get("GROUP_LEADERS_ID"):
            try:
                await callback.bot.send_message(callback.bot.config["GROUP_LEADERS_ID"], f"Error updating sheet for {cid}: {e}")
            except:
                pass

    # Попытка отредактировать исходное сообщение (которое в группе жалоб)
    try:
        original = callback.message.caption or callback.message.text or ""
        new_text = original + f"\n☎️ Перезвонили в {now}"
        # пробуем edit_caption, если не работает — edit_text
        try:
            await callback.message.edit_caption(new_text, parse_mode="HTML")
        except:
            await callback.message.edit_text(new_text, parse_mode="HTML")
    except Exception:
        # игнорируем ошибки редактирования
        pass

    # Пересылаем текст в группу РЕШЕНИЯ (без inline-кнопки)
    group_solutions = callback.bot.config.get("GROUP_SOLUTIONS_ID")
    try:
        forward_text = f"📤 Жалоба ID {cid} передана в «РЕШЕНИЯ».\n\n{callback.message.caption or callback.message.text or ''}\n\n🕒 {now}"
        if group_solutions:
            # отправляем без кнопок (по твоему запросу)
            await callback.bot.send_message(group_solutions, forward_text, parse_mode="HTML")
    except Exception:
        # если не получилось — уведомим лидеров (опционально)
        if callback.bot.config.get("GROUP_LEADERS_ID"):
            try:
                await callback.bot.send_message(callback.bot.config["GROUP_LEADERS_ID"], f"Не удалось переслать жалобу {cid} в РЕШЕНИЯ.")
            except:
                pass

    try:
        await callback.answer("✅ Жалоба передана в «РЕШЕНИЯ».")
    except:
        pass


# -------------------------
# 💬 Нажали "Добавить решение" — помечаем ожидание в bot.solution_waiting
# -------------------------
@router.callback_query(F.data.startswith("solution:"))
async def add_solution(callback: types.CallbackQuery):
    cid = callback.data.split(":", 1)[1]
    uid = callback.from_user.id

    # Инициализируем словарь ожиданий, если нет
    if not hasattr(callback.bot, "solution_waiting"):
        callback.bot.solution_waiting = {}

    # Защита от повторного нажатия одной и той же кнопки одним человеком
    existing = callback.bot.solution_waiting.get(uid)
    if existing and existing.get("cid") == cid:
        try:
            await callback.answer("Вы уже вводите решение для этой жалобы. Отправьте текст.", show_alert=False)
        except:
            pass
        return

    callback.bot.solution_waiting[uid] = {"cid": cid, "ts": datetime.now().isoformat()}
    try:
        await callback.message.answer(f"✍️ Введите текст решения по жалобе {cid}:")
    except:
        pass
    try:
        await callback.answer()
    except:
        pass


# -------------------------
# 📥 Получение текста решения от пользователя
# -------------------------
@router.message(F.text)
async def receive_solution(message: types.Message):
    bot = message.bot
    uid = message.from_user.id

    # Если не ожидаем решения от этого пользователя — просто игнорируем
    if not hasattr(bot, "solution_waiting") or uid not in bot.solution_waiting:
        return

    entry = bot.solution_waiting.pop(uid, None)
    if not entry:
        return

    cid = entry.get("cid")
    solution_text = message.text.strip()
    if not solution_text or len(solution_text) < 2:
        await message.answer("❌ Решение слишком короткое, напишите подробнее.")
        return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    responsible = message.from_user.full_name or "Без имени"
    username = f"@{message.from_user.username}" if message.from_user.username else ""

    # Обновляем Google Sheets
    try:
        gs = GoogleSheetsClient(bot.config["SERVICE_ACCOUNT_FILE"], bot.config["GOOGLE_SHEET_ID"])
        gs.update_by_id(cid, {
            "Решение": solution_text,
            "Статус": "Закрыта",
            "Ответственный": f"{responsible} {username}",
            "Время обзвона": now
        })
    except Exception as e:
        await message.answer(f"⚠️ Ошибка при сохранении решения: {e}")
        # оповестим лидеров, если настроены
        if bot.config.get("GROUP_LEADERS_ID"):
            try:
                await bot.send_message(bot.config["GROUP_LEADERS_ID"], f"Error saving solution for {cid}: {e}")
            except:
                pass
        return

    # Подтверждаем автору
    try:
        await message.answer(f"✅ Решение по жалобе {cid} сохранено и жалоба закрыта.")
    except:
        pass

    # Уведомляем группы (компактно)
    complaints_chat = bot.config.get("GROUP_COMPLAINTS_ID")
    solutions_chat = bot.config.get("GROUP_SOLUTIONS_ID")
    notify_text = (
        f"✅ Жалоба {cid} закрыта.\n"
        f"💬 Решение: {solution_text}\n"
        f"👤 Ответственный: {responsible} {username}\n"
        f"🕒 {now}"
    )

    if complaints_chat:
        try:
            await bot.send_message(complaints_chat, notify_text, parse_mode="HTML")
        except:
            pass
    if solutions_chat:
        try:
            await bot.send_message(solutions_chat, notify_text, parse_mode="HTML")
        except:
            pass
