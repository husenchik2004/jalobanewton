import re
from datetime import datetime, timedelta


# ============================
# ⏰ Узбекистанское время (UTC+5)
# ============================
def uz_time() -> datetime:
    """
    Текущее время Узбекистана как naive datetime.
    Используется ВСЮДУ в боте, чтобы не зависеть от часового пояса сервера (Railway = UTC).
    """
    return datetime.utcnow() + timedelta(hours=5)


# ============================
# 📞 Нормализация телефона
# ============================
def normalize_phone(raw: str) -> str:
    """
    Приводит номер телефона к стандартному формату +998XXXXXXXXX.
    Допускает ввод: 911234567, 998911234567, +998911234567, 0911234567, 8-91-123-4567.
    Возвращает "" если не удалось определить.
    """
    if not raw:
        return ""

    digits = re.sub(r"\D", "", raw)

    if len(digits) == 9:
        return f"+998{digits}"
    elif len(digits) == 12 and digits.startswith("998"):
        return f"+{digits}"
    elif len(digits) == 10 and digits.startswith("0"):
        return f"+998{digits[1:]}"
    elif len(digits) >= 11 and raw.strip().startswith("+"):
        return f"+{digits}"
    else:
        # fallback — возвращаем только цифры, если ничего не подошло
        return f"+998{digits[-9:]}" if len(digits) >= 9 else ""


# ============================
# 🆔 Генерация ID жалобы
# ============================
def generate_complaint_id(prefix: str = "J") -> str:
    """
    Создаёт уникальный ID жалобы вида J-250108143501.
    Можно задать префикс (например, "A" для другой таблицы).
    """
    return f"{prefix}-{uz_time().strftime('%y%m%d%H%M%S')}"


# ============================
# 👤 Проверка разрешённых пользователей
# ============================
def is_allowed_user(user_id: int, allowed_users: list[int]) -> bool:
    """Проверяет, есть ли пользователь в списке разрешённых."""
    if not allowed_users:
        return True  # если список пустой — разрешить всем
    return user_id in allowed_users


# ============================
# 🕒 Форматирование даты и времени
# ============================
def format_time(ts: datetime | None = None) -> str:
    """Возвращает время в формате 'дд.мм.гггг чч:мм' (по умолчанию — текущее UZ)."""
    ts = ts or uz_time()
    return ts.strftime("%d.%m.%Y %H:%M")
