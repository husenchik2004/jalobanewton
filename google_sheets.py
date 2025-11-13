import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from datetime import datetime
class GoogleSheetsClient:
    def __init__(self, service_file: str, sheet_id: str):
        """Подключение к Google Sheets"""
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(service_file, scopes=scopes)
        self.client = gspread.authorize(creds)
        self.sheet = self.client.open_by_key(sheet_id).worksheet("Complaints")

        # ✅ Проверяем заголовки при запуске
        self.ensure_headers()

    # ======================================================
    # ✅ Проверка и выравнивание заголовков
    # ======================================================
    def ensure_headers(self):
        """Проверяет, что заголовки совпадают с нужными, иначе исправляет"""
        expected_headers = [
            "ID", "Дата", "Филиал", "Родитель", "Ученик", "Телефон", "Категория", "Жалоба",
            "Статус", "Время обзвона", "Решение", "Ответственный",
            "Время решения", "Время уведомления", "Кто уведомил родителя",
            "Отправитель", "User ID"
        ]

        current_headers = self.sheet.row_values(1)
        if current_headers[:len(expected_headers)] != expected_headers:
            print("⚠️ Заголовки не совпадают с ожидаемыми — обновляю строку 1.")
            self.sheet.delete_rows(1)
            self.sheet.insert_row(expected_headers, 1)
            print("✅ Заголовки синхронизированы.")

    # ======================================================
    # ✅ Добавление жалобы (строго по колонкам)
    # ======================================================
    def add_complaint(self, complaint: dict):
        """Добавляет новую жалобу строго в нужные колонки"""
        headers = [
            "ID", "Дата", "Филиал", "Родитель", "Ученик", "Телефон", "Категория", "Жалоба",
            "Статус", "Время обзвона", "Решение", "Ответственный",
            "Время решения", "Время уведомления", "Кто уведомил родителя",
            "Отправитель", "User ID"
        ]

        try:
            row = [complaint.get(h, "") for h in headers]
            self.sheet.append_row(row, value_input_option="USER_ENTERED")
            print(f"✅ Добавлена жалоба ID: {complaint.get('ID', '?')}")
            return True
        except Exception as e:
            print(f"❌ Ошибка при добавлении жалобы: {e}")
            return False

    # ======================================================
    # ✅ Поиск по ID
    # ======================================================
    def get_row_by_id(self, complaint_id: str):
        """Возвращает (индекс строки, словарь данных) по ID"""
        try:
            all_values = self.sheet.get_all_values()
            if not all_values:
                return None, {}

            headers = all_values[0]
            for i, row in enumerate(all_values[1:], start=2):
                if not row or not row[0]:
                    continue
                if str(row[0]).strip() == str(complaint_id).strip():
                    data = {headers[j]: (row[j] if j < len(row) else "") for j in range(len(headers))}
                    return i, data
            return None, {}
        except Exception as e:
            print(f"⚠️ Ошибка при поиске ID {complaint_id}: {e}")
            return None, {}

    # ======================================================
    # ✅ Обновление по ID
    # ======================================================
    def update_by_id(self, complaint_id: str, updates: dict):
        """Обновляет значения по ID (все ключи должны совпадать с заголовками)"""
        try:
            row_index, _ = self.get_row_by_id(complaint_id)
            if not row_index:
                print(f"⚠️ Жалоба с ID {complaint_id} не найдена.")
                return False

            headers = self.sheet.row_values(1)
            if not headers:
                print("⚠️ Нет заголовков в таблице.")
                return False

            # собираем диапазон обновления
            cell_list = self.sheet.range(row_index, 1, row_index, len(headers))
            for cell in cell_list:
                header = headers[cell.col - 1]
                if header in updates:
                    cell.value = updates[header]

            self.sheet.update_cells(cell_list, value_input_option="USER_ENTERED")
            print(f"✅ Жалоба {complaint_id} успешно обновлена.")
            return True

        except Exception as e:
            print(f"❌ Ошибка при обновлении жалобы {complaint_id}: {e}")
            return False

    # ======================================================
    # ✅ Получение всех данных (для отчётов)
    # ======================================================
    def get_all_data(self):
        """Возвращает все строки таблицы в виде DataFrame"""
        try:
            data = self.sheet.get_all_values()
            if not data or len(data) < 2:
                return pd.DataFrame()
            df = pd.DataFrame(data[1:], columns=data[0])
            return df
        except Exception as e:
            print(f"⚠️ Ошибка при получении всех данных: {e}")
            return pd.DataFrame()

    # ======================================================
    # ✅ Фильтрация по диапазону дат
    # ======================================================
    def get_by_date_range(self, start_date: str, end_date: str):
        """Возвращает жалобы за выбранный диапазон дат"""
        df = self.get_all_data()
        if df.empty or "Дата" not in df.columns:
            return pd.DataFrame()

        df["Дата"] = pd.to_datetime(df["Дата"], errors="coerce", format="%d.%m.%Y %H:%M")
        mask = (df["Дата"] >= pd.to_datetime(start_date)) & (df["Дата"] <= pd.to_datetime(end_date))
        return df.loc[mask]
