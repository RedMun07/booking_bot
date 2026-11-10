"""Telegram-бот записи в студию «Атмосфера».

Запуск:
    BOT_TOKEN=... ADMIN_ID=... python app.py
"""
from __future__ import annotations

import asyncio
import csv
import html
import io
import logging
import os
import sys
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("booking_bot")

# ========== КОНФИГУРАЦИЯ ==========
def _load_env(path: str = ".env") -> None:
    """Подхватывает переменные из .env рядом с app.py, если файл есть."""
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                os.environ.setdefault(
                    key.strip(), value.strip().strip('"').strip("'")
                )
    except OSError:
        pass


_load_env()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if not BOT_TOKEN:
    sys.exit(
        "Не задан BOT_TOKEN.\n"
        "Задайте переменную окружения, например:\n"
        "  set BOT_TOKEN=123456:ABC...  (Windows)\n"
        "  export BOT_TOKEN=123456:ABC...  (Linux/macOS)"
    )

try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
except ValueError:
    sys.exit("ADMIN_ID должен быть числом (ваш Telegram ID).")

if ADMIN_ID <= 0:
    log.warning("ADMIN_ID не задан — уведомления администратору отправляться не будут.")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# ========== РЕЖИМЫ АДМИНА ==========
user_modes: dict[int, str] = {}


def is_admin(user_id: int) -> bool:
    return ADMIN_ID > 0 and user_id == ADMIN_ID


def get_user_mode(user_id: int) -> str:
    if not is_admin(user_id):
        return "client"
    return user_modes.get(user_id, "admin")


def set_user_mode(user_id: int, mode: str) -> None:
    if is_admin(user_id):
        user_modes[user_id] = mode


# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    admin = is_admin(user_id)
    mode = get_user_mode(user_id)
    if admin and mode == "admin":
        kb = [
            [KeyboardButton(text="📋 Занятые слоты"), KeyboardButton(text="📊 Экспорт CSV")],
            [KeyboardButton(text="🏠 Главное меню"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="👤 Перейти в режим клиента")],
        ]
    else:
        kb = [
            [KeyboardButton(text="📅 Расписание"), KeyboardButton(text="✏️ Записаться")],
            [KeyboardButton(text="❌ Отмена"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="🏠 Главное меню")],
        ]
        if admin and mode == "client":
            kb.append([KeyboardButton(text="🛡️ Перейти в режим админа")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# ========== FSM ==========
class BookingForm(StatesGroup):
    choosing_date = State()
    choosing_slot = State()
    entering_name = State()
    entering_phone = State()


class CancelForm(StatesGroup):
    waiting_code = State()


# ========== ФОРМАТИРОВАНИЕ ==========
def esc(value) -> str:
    """Экранирование пользовательских данных для parse_mode=HTML."""
    return html.escape(str(value or ""), quote=False)


def format_free_slots(date_str: str, free_hours: list[int]) -> str:
    lines = [f"<b>📅 {esc(date_str)}</b>"]
    if not free_hours:
        lines.append("  Нет свободных слотов.")
        return "\n".join(lines)
    for hour in free_hours:
        lines.append(f"  • {hour:02d}:00-{hour + 1:02d}:00 — <b>свободно</b>")
    return "\n".join(lines)


def format_booked_slots(date_str: str, booked_slots) -> str:
    lines = [f"<b>📅 {esc(date_str)}</b>"]
    if not booked_slots:
        lines.append("  Нет занятых слотов.")
        return "\n".join(lines)
    for row in booked_slots:
        lines.append(
            f"  • {row['hour']:02d}:00-{row['hour'] + 1:02d}:00 — "
            f"<b>{esc(row['booked_by'])}</b> (тел: {esc(row['phone'])}, "
            f"код: <code>{esc(row['booking_code'])}</code>)"
        )
    return "\n".join(lines)


async def show_main_menu(message: types.Message, text: str | None = None) -> None:
    user_id = message.from_user.id
    admin = is_admin(user_id)
    mode = get_user_mode(user_id)

    if text is None:
        if not admin:
            text = (
                "<b>🎵 Добро пожаловать в студию «Атмосфера»!</b>\n\n"
                "Я помогу вам записаться на час в студии.\n"
                "Используйте кнопки ниже или вводите команды вручную.\n\n"
                "📅 <b>Расписание</b> – посмотреть свободные слоты на 3 дня\n"
                "✏️ <b>Записаться</b> – выбрать дату и время\n"
                "❌ <b>Отмена</b> – отменить запись по коду\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение"
            )
        elif mode == "admin":
            text = (
                "<b>🛡️ Режим администратора</b>\n\n"
                "Доступные функции:\n"
                "📋 <b>Занятые слоты</b> – просмотр всех бронирований\n"
                "📊 <b>Экспорт CSV</b> – выгрузка всех записей в файл\n"
                "👤 <b>Перейти в режим клиента</b> – посмотреть бот глазами пользователя\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение"
            )
        else:
            text = (
                "<b>🎵 Клиентский режим</b>\n\n"
                "Доступные функции:\n"
                "📅 <b>Расписание</b> – посмотреть свободные слоты на 3 дня\n"
                "✏️ <b>Записаться</b> – выбрать дату и время\n"
                "❌ <b>Отмена</b> – отменить запись по коду\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение\n"
                "🛡️ <b>Перейти в режим админа</b> – вернуться к админским функциям"
            )

    await message.answer(text, reply_markup=get_main_keyboard(user_id))


# ==================================================
#  НАВИГАЦИЯ И РЕЖИМЫ
# ==================================================
@dp.message(lambda msg: msg.text == "🏠 Главное меню")
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message, "🏠 Вы вернулись в главное меню.")


@dp.message(Command("id"))
async def cmd_id(message: types.Message):
    await message.answer(
        f"Ваш Telegram ID: <code>{message.from_user.id}</code>\n"
        f"ID этого чата: <code>{message.chat.id}</code>"
    )


@dp.message(Command("cancel_mode"), StateFilter(None))
async def cmd_cancel_mode(message: types.Message, state: FSMContext):
    """Сброс состояния, если бот «застрял» в диалоге."""
    await state.clear()
    await show_main_menu(message, "↩️ Действие отменено.")


@dp.message(lambda msg: msg.text == "👤 Перейти в режим клиента", StateFilter(None))
async def switch_to_client(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав.")
        return
    set_user_mode(message.from_user.id, "client")
    await show_main_menu(message, "🔄 Вы переключились в режим клиента.\nТеперь вы видите обычное меню.")


@dp.message(lambda msg: msg.text == "🛡️ Перейти в режим админа", StateFilter(None))
async def switch_to_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ У вас нет прав.")
        return
    set_user_mode(message.from_user.id, "admin")
    await show_main_menu(message, "🔄 Вы переключились в режим администратора.\nТеперь вам доступны админские функции.")


# ==================================================
#  КОМАНДЫ
# ==================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if is_admin(message.from_user.id):
        set_user_mode(message.from_user.id, "admin")
    await show_main_menu(message)


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await show_main_menu(message)


@dp.message(lambda msg: msg.text == "ℹ️ Помощь", StateFilter(None))
async def help_button(message: types.Message):
    await show_main_menu(message)


@dp.message(Command("schedule"))
@dp.message(lambda msg: msg.text == "📅 Расписание", StateFilter(None))
async def cmd_schedule(message: types.Message):
    now = datetime.now()
    parts = []
    for i in range(storage.BOOKING_DAYS):
        date_str = (now.date() + timedelta(days=i)).isoformat()
        hours = storage.free_hours(date_str, now)
        parts.append(format_free_slots(date_str, hours))
    await message.answer("\n\n".join(parts),
                         reply_markup=get_main_keyboard(message.from_user.id))


@dp.message(Command("booked"))
@dp.message(lambda msg: msg.text == "📋 Занятые слоты", StateFilter(None))
async def cmd_booked(message: types.Message):
    if not is_admin(message.from_user.id) or get_user_mode(message.from_user.id) != "admin":
        await message.answer("⛔ У вас нет прав на эту команду или вы не в режиме администратора.")
        return
    now = datetime.now()
    parts = []
    for i in range(storage.BOOKING_DAYS):
        date_str = (now.date() + timedelta(days=i)).isoformat()
        parts.append(format_booked_slots(date_str, storage.get_booked_slots_for_date(date_str)))
    await message.answer("\n\n".join(parts),
                         reply_markup=get_main_keyboard(message.from_user.id))


@dp.message(Command("export"))
@dp.message(lambda msg: msg.text == "📊 Экспорт CSV", StateFilter(None))
async def cmd_export(message: types.Message):
    if not is_admin(message.from_user.id) or get_user_mode(message.from_user.id) != "admin":
        await message.answer("⛔ У вас нет прав на эту команду или вы не в режиме администратора.")
        return

    rows = storage.all_bookings()
    if not rows:
        await message.answer("📭 Нет ни одной записи для экспорта.")
        return

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Дата", "Время", "Имя", "Телефон", "Код отмены"])
    for row in rows:
        writer.writerow([
            row["date"],
            f"{row['hour']:02d}:00-{row['hour'] + 1:02d}:00",
            row["booked_by"],
            row["phone"],
            row["booking_code"],
        ])

    data = output.getvalue().encode("utf-8-sig")
    filename = f"booking_export_{datetime.now():%Y%m%d_%H%M}.csv"
    await message.answer_document(
        document=BufferedInputFile(data, filename=filename),
        caption="📊 Отчёт по бронированиям",
    )


# ====== ЗАПИСЬ ======
@dp.message(Command("book"))
@dp.message(lambda msg: msg.text == "✏️ Записаться", StateFilter(None))
async def cmd_book(message: types.Message, state: FSMContext):
    dates = storage.upcoming_free_dates()
    if not dates:
        await message.answer("😕 Нет свободных слотов на ближайшие 3 дня.",
                             reply_markup=get_main_keyboard(message.from_user.id))
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=d, callback_data=f"date_{d}")] for d in dates
    ])
    await message.answer("Выберите дату для записи:", reply_markup=keyboard)
    await state.set_state(BookingForm.choosing_date)


@dp.callback_query(StateFilter(BookingForm.choosing_date))
async def process_date(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split("_", 1)
    if len(parts) != 2:
        await callback.answer("Некорректные данные", show_alert=True)
        return
    date_str = parts[1]

    hours = storage.free_hours(date_str)
    if not hours:
        await callback.message.edit_text("На эту дату нет свободных слотов. Попробуйте другую.")
        await state.clear()
        await callback.answer()
        return

    await state.update_data(date=date_str)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{h:02d}:00-{h + 1:02d}:00", callback_data=f"slot_{h}")]
        for h in hours
    ])
    await callback.message.edit_text(f"Выберите время на {date_str}:", reply_markup=keyboard)
    await state.set_state(BookingForm.choosing_slot)
    await callback.answer()


@dp.callback_query(StateFilter(BookingForm.choosing_slot))
async def process_slot(callback: types.CallbackQuery, state: FSMContext):
    parts = (callback.data or "").split("_", 1)
    try:
        hour = int(parts[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные", show_alert=True)
        return
    await state.update_data(hour=hour)
    await callback.message.edit_text("Введите ваше имя (или nickname):")
    await state.set_state(BookingForm.entering_name)
    await callback.answer()


@dp.message(StateFilter(BookingForm.entering_name))
async def process_name(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте имя текстом.")
        return
    name = message.text.strip()
    if not name:
        await message.answer("Имя не может быть пустым. Введите имя:",
                             reply_markup=get_main_keyboard(message.from_user.id))
        return
    if len(name) > 100:
        await message.answer("Слишком длинное имя (максимум 100 символов).")
        return
    await state.update_data(name=name)
    await message.answer("Введите ваш номер телефона (можно в свободном формате):")
    await state.set_state(BookingForm.entering_phone)


@dp.message(StateFilter(BookingForm.entering_phone))
async def process_phone(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте номер текстом.")
        return
    phone = message.text.strip()
    if len(phone) != 11:
        await message.answer("❌ Номер должен содержать ровно 11 цифр (например, 89001234567). Попробуйте ещё раз.")
        return
    if not phone.isdigit():
        await message.answer("❌ Номер должен состоять только из цифр. Без пробелов, скобок и дефисов.")
        return
    if phone[0] != "8":
        await message.answer("❌ Номер должен начинаться с цифры 8.")
        return

    data = await state.get_data()
    date_str = data.get("date")
    hour = data.get("hour")
    name = data.get("name")
    if not date_str or hour is None or not name:
        await state.clear()
        await message.answer("Что-то пошло не так, начните запись заново.",
                             reply_markup=get_main_keyboard(message.from_user.id))
        return

    code = storage.book_slot(date_str, hour, name, phone)
    if code is None:
        await message.answer("❌ К сожалению, этот слот уже занят. Попробуйте выбрать другой.",
                             reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    await message.answer(
        "✅ Запись подтверждена!\n\n"
        f"Дата: {esc(date_str)}\n"
        f"Время: {hour:02d}:00-{hour + 1:02d}:00\n"
        f"Имя: {esc(name)}\n"
        f"Телефон: {esc(phone)}\n\n"
        f"<b>Код отмены:</b> <code>{esc(code)}</code>\n"
        "Сохраните его, чтобы отменить запись.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )

    if ADMIN_ID > 0:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🔔 <b>Новая запись!</b>\n\n"
                f"Дата: {esc(date_str)}\n"
                f"Время: {hour:02d}:00-{hour + 1:02d}:00\n"
                f"Имя: {esc(name)}\n"
                f"Телефон: {esc(phone)}\n"
                f"Код: <code>{esc(code)}</code>",
            )
        except Exception as e:
            log.warning("Не удалось отправить уведомление админу: %s", e)
    await state.clear()


# ====== ОТМЕНА ======
@dp.message(Command("cancel"))
@dp.message(lambda msg: msg.text == "❌ Отмена", StateFilter(None))
async def cmd_cancel_start(message: types.Message, state: FSMContext):
    await message.answer("Введите код отмены (6 символов):",
                         reply_markup=get_main_keyboard(message.from_user.id))
    await state.set_state(CancelForm.waiting_code)


@dp.message(StateFilter(CancelForm.waiting_code))
async def process_cancel_code(message: types.Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправьте код текстом.")
        return
    code = message.text.strip().upper()
    if len(code) != 6 or not code.isascii() or not code.isalnum():
        await message.answer("Код должен состоять из 6 латинских букв и цифр. Попробуйте ещё раз.")
        return

    row = storage.get_booking_by_code(code)
    if row is None or not storage.cancel_booking(code):
        await message.answer("❌ Код не найден. Проверьте правильность ввода.",
                             reply_markup=get_main_keyboard(message.from_user.id))
        await state.clear()
        return

    await message.answer("✅ Запись успешно отменена.",
                         reply_markup=get_main_keyboard(message.from_user.id))
    if ADMIN_ID > 0:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🔕 <b>Отмена записи</b>\n\n"
                f"Код: <code>{esc(code)}</code>\n"
                f"Дата: {esc(row['date'])}, "
                f"{row['hour']:02d}:00-{row['hour'] + 1:02d}:00\n"
                f"Клиент: {esc(row['booked_by'])}, {esc(row['phone'])}",
            )
        except Exception as e:
            log.warning("Не удалось отправить уведомление админу: %s", e)
    await state.clear()


@dp.message(StateFilter(None))
async def any_message(message: types.Message):
    """Любое сообщение вне диалога возвращает в меню."""
    await show_main_menu(message)


# ========== ЗАПУСК ==========
async def main() -> None:
    storage.init_db()
    deleted = storage.clear_old_slots()
    if deleted:
        log.info("Удалено старых свободных слотов: %s", deleted)
    storage.ensure_slots_for_days()
    log.info("Бот запущен")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"],
                               drop_pending_updates=True)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass

