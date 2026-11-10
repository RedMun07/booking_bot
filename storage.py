"""Работа с базой данных (SQLite).

Вынесено из app.py, чтобы слой хранения можно было тестировать
без запуска Telegram-бота.
"""
from __future__ import annotations

import os
import random
import sqlite3
import string
from contextlib import closing
from datetime import date, datetime, timedelta

DB_NAME = os.getenv("DB_PATH", "studio.db")

WORKDAY_START = 9
WORKDAY_END = 18      # последний слот 17:00-18:00
BOOKING_DAYS = 3


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with closing(connect()) as conn, conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                hour INTEGER NOT NULL,
                is_booked BOOLEAN DEFAULT 0,
                booked_by TEXT,
                phone TEXT,
                booking_code TEXT UNIQUE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_slots_date ON slots(date)")


def generate_booking_code(length: int = 6) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choices(alphabet, k=length))


def clear_old_slots() -> int:
    """Удаляет прошедшие свободные слоты, сохраняя историю записей."""
    today = datetime.now().date().isoformat()
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "DELETE FROM slots WHERE date < ? AND is_booked = 0", (today,)
        )
        return cur.rowcount or 0


def ensure_slots_for_days(days: int = BOOKING_DAYS) -> None:
    """Досоздаёт недостающие слоты на ближайшие дни (идемпотентно)."""
    today = datetime.now().date()
    with closing(connect()) as conn, conn:
        for i in range(days):
            date_str = (today + timedelta(days=i)).isoformat()
            existing = {
                row["hour"]
                for row in conn.execute(
                    "SELECT hour FROM slots WHERE date = ?", (date_str,)
                )
            }
            for hour in range(WORKDAY_START, WORKDAY_END):
                if hour not in existing:
                    conn.execute(
                        "INSERT INTO slots (date, hour, is_booked) VALUES (?, ?, 0)",
                        (date_str, hour),
                    )


def get_slots_for_date(date_str: str) -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        return list(
            conn.execute(
                "SELECT hour, is_booked, booked_by, booking_code "
                "FROM slots WHERE date = ? ORDER BY hour",
                (date_str,),
            )
        )


def get_free_slots_for_date(date_str: str) -> list[int]:
    with closing(connect()) as conn:
        return [
            row["hour"]
            for row in conn.execute(
                "SELECT hour FROM slots WHERE date = ? AND is_booked = 0 ORDER BY hour",
                (date_str,),
            )
        ]


def get_booked_slots_for_date(date_str: str) -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        return list(
            conn.execute(
                "SELECT hour, booked_by, phone, booking_code FROM slots "
                "WHERE date = ? AND is_booked = 1 ORDER BY hour",
                (date_str,),
            )
        )


def book_slot(date_str: str, hour: int, name: str, phone: str | None = None) -> str | None:
    """Атомарно занимает слот. Возвращает код отмены или None, если занято."""
    for _ in range(5):
        code = generate_booking_code()
        try:
            with closing(connect()) as conn, conn:
                cur = conn.execute(
                    "UPDATE slots SET is_booked = 1, booked_by = ?, phone = ?, "
                    "booking_code = ? WHERE date = ? AND hour = ? AND is_booked = 0",
                    (name, phone, code, date_str, hour),
                )
                if cur.rowcount == 0:
                    return None
            return code
        except sqlite3.IntegrityError:
            # крайне маловероятная коллизия кода — пробуем ещё раз
            continue
    return None


def cancel_booking(code: str) -> bool:
    with closing(connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE slots SET is_booked = 0, booked_by = NULL, phone = NULL, "
            "booking_code = NULL WHERE booking_code = ? AND is_booked = 1",
            (code,),
        )
        return cur.rowcount > 0


def get_booking_by_code(code: str) -> sqlite3.Row | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT date, hour, booked_by, phone FROM slots "
            "WHERE booking_code = ? AND is_booked = 1",
            (code,),
        ).fetchone()
        return row


def all_bookings() -> list[sqlite3.Row]:
    with closing(connect()) as conn:
        return list(
            conn.execute(
                "SELECT date, hour, booked_by, phone, booking_code FROM slots "
                "WHERE is_booked = 1 ORDER BY date, hour"
            )
        )


def upcoming_free_dates(days: int = BOOKING_DAYS, now: datetime | None = None) -> list[str]:
    """Даты из ближайших дней, где ещё есть свободные слоты впереди."""
    now = now or datetime.now()
    today = now.date()
    result = []
    for i in range(days):
        d = today + timedelta(days=i)
        free = get_free_slots_for_date(d.isoformat())
        if d == today:
            free = [h for h in free if h > now.hour]
        if free:
            result.append(d.isoformat())
    return result


def free_hours(date_str: str, now: datetime | None = None) -> list[int]:
    """Свободные часы на дату; для сегодняшнего дня прошедшие отбрасываются."""
    now = now or datetime.now()
    hours = get_free_slots_for_date(date_str)
    if date_str == now.date().isoformat():
        hours = [h for h in hours if h > now.hour]
    return hours

