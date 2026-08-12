import asyncio
from datetime import datetime, timedelta, time
from google_sheets import GoogleSheetsClient
from reports import send_reports
from utils import uz_time
import traceback

# ================================
# 🚀 Запуск планировщика
# ================================
def start_scheduler(bot):
    """
    Запускает фоновые задачи:
      - check_pending_calls (каждые 10 минут)
      - weekly_report (каждый понедельник в 09:00)
      - monthly_report (каждое 1-е число в 09:00)
    """
    asyncio.create_task(_run_check_pending_calls_periodically(bot))
    asyncio.create_task(_run_weekly_report_task(bot))
    asyncio.create_task(_run_monthly_report_task(bot))
    print("🕒 Планировщик запущен.")


# ------------------------------
# 🔔 Проверка необзвоненных жалоб
# ------------------------------
async def _run_check_pending_calls_periodically(bot):
    """
    Каждые 10 минут проверяет жалобы со статусом 'Ожидает обзвона'
    старше 2 часов. Отправляет напоминание только один раз.
    """
    cfg = bot.config
    group_complaints = cfg["GROUP_COMPLAINTS_ID"]

    notified_ids = set()

    while True:
        try:
            gs = GoogleSheetsClient(cfg["SERVICE_ACCOUNT_FILE"], cfg["GOOGLE_SHEET_ID"])
            df = gs.get_all_data()
            if df is None or df.empty:
                await asyncio.sleep(600)
                continue

            # Поиск нужных колонок
            status_col = next((c for c in df.columns if "статус" in c.lower()), None)
            date_col = next((c for c in df.columns if "дата" in c.lower()), None)
            id_col = next((c for c in df.columns if c.lower() == "id"), None)

            if not all([status_col, date_col, id_col]):
                print("⚠️ В таблице не найдены нужные колонки (ID / Статус / Дата).")
                await asyncio.sleep(600)
                continue

            now = uz_time()
            for _, row in df.iterrows():
                try:
                    status = str(row.get(status_col, "")).strip().lower()
                    if status not in ("ожидает обзвона", "ожидает", "awaiting call", "new"):
                        continue

                    raw_date = str(row.get(date_col, ""))
                    parsed = None
                    for fmt in ("%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                        try:
                            parsed = datetime.strptime(raw_date, fmt)
                            break
                        except Exception:
                            continue
                    if not parsed:
                        continue

                    diff = now - parsed
                    cid = str(row.get(id_col, "")).strip()

                    # Пропускаем старые (более 3 дней) и уже уведомлённые
                    if diff.days > 3 or cid in notified_ids:
                        continue

                    if diff.total_seconds() > 2 * 3600:
                        text = (
                            f"🔔 Напоминание:\n"
                            f"Жалоба <b>{cid}</b> ожидает обзвона более 2 часов.\n"
                            f"🕓 Создана: {parsed.strftime('%d.%m.%Y %H:%M')}"
                        )
                        await bot.send_message(group_complaints, text)
                        notified_ids.add(cid)
                        print(f"📢 Напоминание отправлено для {cid}")

                except Exception:
                    traceback.print_exc()

        except Exception:
            traceback.print_exc()

        await asyncio.sleep(600)  # 10 минут


# ------------------------------
# 📅 Еженедельный отчёт
# ------------------------------
async def _run_weekly_report_task(bot):
    """
    Каждую неделю (понедельник 09:00) отправляет отчёт за последние 7 дней.
    """
    cfg = bot.config
    leaders = cfg["GROUP_LEADERS_ID"]

    while True:
        try:
            now = uz_time()
            # до следующего понедельника 09:00
            days_ahead = (0 - now.weekday() + 7) % 7
            if days_ahead == 0 and now.time() >= time(hour=9):
                days_ahead = 7
            next_monday = (now + timedelta(days=days_ahead)).replace(hour=9, minute=0, second=0)
            wait_seconds = (next_monday - now).total_seconds()
            print(f"🗓 Ожидание до еженедельного отчёта: {wait_seconds/3600:.1f} часов")
            await asyncio.sleep(wait_seconds)

            date_to = (next_monday - timedelta(days=1)).date()
            date_from = date_to - timedelta(days=6)
            await send_reports(bot, str(date_from), str(date_to), leaders)
            print(f"✅ Еженедельный отчёт отправлен: {date_from}–{date_to}")

        except Exception:
            traceback.print_exc()
            await asyncio.sleep(60)


# ------------------------------
# 🗓 Месячный отчёт
# ------------------------------
async def _run_monthly_report_task(bot):
    """
    Каждое 1-е число месяца в 09:00 отправляет отчёт за прошлый месяц.
    """
    cfg = bot.config
    leaders = cfg["GROUP_LEADERS_ID"]

    while True:
        try:
            now = uz_time()
            # Находим первое число следующего месяца 09:00
            year, month = now.year, now.month
            if month == 12:
                next_month = datetime(year + 1, 1, 1, 9, 0, 0)
            else:
                next_month = datetime(year, month + 1, 1, 9, 0, 0)
            wait_seconds = (next_month - now).total_seconds()
            print(f"🗓 Ожидание до месячного отчёта: {wait_seconds/3600:.1f} часов")
            await asyncio.sleep(wait_seconds)

            # предыдущий месяц
            last_day_prev = (next_month - timedelta(days=1)).date()
            first_day_prev = last_day_prev.replace(day=1)
            await send_reports(bot, str(first_day_prev), str(last_day_prev), leaders)
            print(f"✅ Месячный отчёт отправлен: {first_day_prev}–{last_day_prev}")

        except Exception:
            traceback.print_exc()
            await asyncio.sleep(60)
