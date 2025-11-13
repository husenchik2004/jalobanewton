import pandas as pd
from datetime import datetime
from google_sheets import GoogleSheetsClient
import os


# ============================
# 📊 Формирование отчёта
# ============================
def generate_summary(df: pd.DataFrame):
    """Создаёт агрегированный отчёт по филиалам."""
    if df.empty:
        return pd.DataFrame(columns=["Филиал", "Всего", "Решено", "В работе", "Эффективность %"])

    # Определяем нужные колонки
    branch_col, status_col = None, None
    for c in df.columns:
        cl = c.lower()
        if "branch" in cl or "филиал" in cl:
            branch_col = c
        elif "status" in cl or "статус" in cl:
            status_col = c

    if not branch_col or not status_col:
        return pd.DataFrame(columns=["Филиал", "Всего", "Решено", "В работе", "Эффективность %"])

    df[branch_col] = df[branch_col].fillna("Без филиала")
    df[status_col] = df[status_col].fillna("")

    # Группировка и подсчёт
    grouped = df.groupby(branch_col)[status_col].apply(list).reset_index()

    summary = []
    for _, row in grouped.iterrows():
        branch = row[branch_col]
        statuses = [str(s).strip().lower() for s in row[status_col]]
        total = len(statuses)
        closed = sum(1 for s in statuses if "закрыт" in s or "решен" in s or "resolved" in s)
        in_progress = total - closed
        eff = round((closed / total) * 100, 1) if total > 0 else 0
        summary.append({
            "Филиал": branch,
            "Всего": total,
            "Решено": closed,
            "В работе": in_progress,
            "Эффективность %": eff
        })

    return pd.DataFrame(summary)


# ============================
# 📝 Текст отчёта для Telegram
# ============================
def build_text_report(df: pd.DataFrame, date_from: str, date_to: str) -> str:
    """Создаёт короткий текст отчёта для Telegram."""
    summary = generate_summary(df)
    text = f"📅 Отчёт по жалобам ({date_from} — {date_to})\n\n"

    if summary.empty:
        text += "Нет жалоб за указанный период."
        return text

    for _, row in summary.iterrows():
        text += (
            f"🏫 {row['Филиал']}: {row['Всего']} жалоб | "
            f"✅ Решено: {row['Решено']} | ⏳ В работе: {row['В работе']} | "
            f"📈 Эффективность: {row['Эффективность %']}%\n"
        )

    avg_eff = round(summary["Эффективность %"].mean(), 1)
    total = int(summary["Всего"].sum())
    closed = int(summary["Решено"].sum())
    text += f"\n📊 Итого: {total} жалоб, решено {closed} ({avg_eff}% эффективности)"
    return text


# ============================
# 💾 Экспорт в Excel
# ============================
def export_to_excel(df: pd.DataFrame, filepath: str):
    """Сохраняет отчёт в Excel."""
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Данные")
        summary = generate_summary(df)
        summary.to_excel(writer, index=False, sheet_name="Сводка")
    return filepath


# ============================
# 📤 Отправка отчёта
# ============================
async def send_reports(bot, date_from: str, date_to: str, chat_id: int):
    """Создаёт и отправляет отчёт за указанный период."""
    cfg = bot.config
    gs = GoogleSheetsClient(cfg["SERVICE_ACCOUNT_FILE"], cfg["GOOGLE_SHEET_ID"])

    try:
        df = gs.get_by_date_range(date_from, date_to)
    except Exception as e:
        await bot.send_message(chat_id, f"⚠️ Ошибка при получении данных: {e}")
        return

    text = build_text_report(df, date_from, date_to)
    await bot.send_message(chat_id, text)

    # если есть данные — прикладываем Excel
    if not df.empty:
        fname = f"report_{date_from}_to_{date_to}.xlsx"
        path = os.path.join(os.getcwd(), fname)
        export_to_excel(df, path)
        try:
            await bot.send_document(chat_id, open(path, "rb"))
        finally:
            try:
                os.remove(path)
            except:
                pass
