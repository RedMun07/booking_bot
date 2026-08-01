import asyncio
import sqlite3
import random
import string
import csv
import io
from datetime import datetime, timedelta
import aiohttp
import os

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)

# ========== КОНФИГУРАЦИЯ ==========
BOT_TOKEN = os.getenv('BOT_TOKEN')  # Замени на реальный!
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # ТВОЙ TELEGRAM ID

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ========== ХРАНЕНИЕ РЕЖИМОВ АДМИНА ==========
user_modes = {}  # {chat_id: 'admin' или 'client'}


def get_user_mode(chat_id):
    if chat_id != ADMIN_ID:
        return 'client'  # для не-админа всегда client, но это не используется для отображения
    return user_modes.get(chat_id, 'admin')


def set_user_mode(chat_id, mode):
    if chat_id == ADMIN_ID:
        user_modes[chat_id] = mode


# ========== КЛАВИАТУРЫ ==========
def get_main_keyboard(chat_id):
    is_admin = chat_id == ADMIN_ID
    mode = get_user_mode(chat_id) if is_admin else 'client'
    if is_admin and mode == 'admin':
        kb = [
            [KeyboardButton(text="📋 Занятые слоты"), KeyboardButton(text="📊 Экспорт CSV")],
            [KeyboardButton(text="🏠 Главное меню"), KeyboardButton(text="ℹ️ Помощь")],
            [KeyboardButton(text="👤 Перейти в режим клиента")]
        ]
    else:
        kb = [
            [KeyboardButton(text="📅 Расписание"), KeyboardButton(text="✏️ Записаться")],
            [KeyboardButton(text="❌ Отмена"), KeyboardButton(text="🐱 Факт о котах"), KeyboardButton(text="🐶 Собака")],
            [KeyboardButton(text="🏠 Главное меню"), KeyboardButton(text="ℹ️ Помощь")]
        ]
        if is_admin and mode == 'client':
            kb.append([KeyboardButton(text="🛡️ Перейти в режим админа")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)


# ========== БАЗА ДАННЫХ ==========
DB_NAME = 'studio.db'


def init_db():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS slots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            is_booked BOOLEAN DEFAULT 0,
            booked_by TEXT,
            phone TEXT,
            booking_code TEXT UNIQUE
        )
    ''')
    conn.commit()
    conn.close()


def generate_booking_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


def clear_old_slots():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    today = datetime.now().date().isoformat()
    cur.execute('DELETE FROM slots WHERE date < ?', (today,))
    deleted = cur.rowcount
    conn.commit()
    conn.close()
    if deleted:
        print(f"🗑️ Удалено старых слотов: {deleted}")
    return deleted


def ensure_slots_for_days():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    today = datetime.now().date()
    for i in range(3):
        date = today + timedelta(days=i)
        date_str = date.isoformat()
        cur.execute('SELECT COUNT(*) FROM slots WHERE date = ?', (date_str,))
        if cur.fetchone()[0] == 0:
            for hour in range(9, 18):
                cur.execute(
                    'INSERT INTO slots (date, hour, is_booked) VALUES (?, ?, 0)',
                    (date_str, hour)
                )
    conn.commit()
    conn.close()


def get_slots_for_date(date_str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT hour, is_booked, booked_by, booking_code FROM slots WHERE date = ? ORDER BY hour', (date_str,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_free_slots_for_date(date_str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT hour FROM slots WHERE date = ? AND is_booked = 0 ORDER BY hour', (date_str,))
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]


def get_booked_slots_for_date(date_str):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT hour, booked_by, phone, booking_code FROM slots WHERE date = ? AND is_booked = 1 ORDER BY hour',
                (date_str,))
    rows = cur.fetchall()
    conn.close()
    return rows


def book_slot(date_str, hour, name, phone):
    code = generate_booking_code()
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        'UPDATE slots SET is_booked = 1, booked_by = ?, phone = ?, booking_code = ? WHERE date = ? AND hour = ? AND is_booked = 0',
        (name, phone, code, date_str, hour)
    )
    if cur.rowcount == 0:
        conn.close()
        return None
    conn.commit()
    conn.close()
    return code


def cancel_booking(code):
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute(
        'UPDATE slots SET is_booked = 0, booked_by = NULL, phone = NULL, booking_code = NULL WHERE booking_code = ?',
        (code,)
    )
    if cur.rowcount == 0:
        conn.close()
        return False
    conn.commit()
    conn.close()
    return True


# ========== FSM ==========
class BookingForm(StatesGroup):
    choosing_date = State()
    choosing_slot = State()
    entering_name = State()
    entering_phone = State()


class CancelForm(StatesGroup):
    waiting_code = State()


# ========== ФУНКЦИИ ФОРМАТИРОВАНИЯ ==========
def format_free_slots(date_str, free_hours):
    if not free_hours:
        return f"<b>📅 {date_str}</b>\n  Нет свободных слотов."
    lines = [f"<b>📅 {date_str}</b>"]
    for hour in free_hours:
        time_str = f"{hour:02d}:00-{hour + 1:02d}:00"
        lines.append(f"  • {time_str} — <b>свободно</b>")
    return "\n".join(lines)


def format_booked_slots(date_str, booked_slots):
    if not booked_slots:
        return f"<b>📅 {date_str}</b>\n  Нет занятых слотов."
    lines = [f"<b>📅 {date_str}</b>"]
    for hour, name, phone, code in booked_slots:
        time_str = f"{hour:02d}:00-{hour + 1:02d}:00"
        lines.append(f"  • {time_str} — <b>{name}</b> (тел: {phone}, код: <code>{code}</code>)")
    return "\n".join(lines)


async def show_main_menu(message: types.Message, text: str = None):
    chat_id = message.chat.id
    is_admin = chat_id == ADMIN_ID
    mode = get_user_mode(chat_id) if is_admin else 'client'  # для не-админа режим не используется

    if text is None:
        if not is_admin:
            # Обычный пользователь – без упоминания режимов
            text = (
                "<b>🎵 Добро пожаловать в студию «Атмосфера»!</b>\n\n"
                "Я помогу вам записаться на час в студии.\n"
                "Используйте кнопки ниже или вводите команды вручную.\n\n"
                "📅 <b>Расписание</b> – посмотреть свободные слоты на 3 дня\n"
                "✏️ <b>Записаться</b> – выбрать дату и время\n"
                "❌ <b>Отмена</b> – отменить запись по коду\n"
                "🐱 <b>Факт о котах</b> – случайный факт (тест API)\n"
                "🐶 <b>Собака</b> – случайное фото собаки (тест API)\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение"
            )
        elif mode == 'admin':
            # Админ в админ-режиме
            text = (
                "<b>🛡️ Режим администратора</b>\n\n"
                "Доступные функции:\n"
                "📋 <b>Занятые слоты</b> – просмотр всех бронирований\n"
                "📊 <b>Экспорт CSV</b> – выгрузка всех записей в файл\n"
                "👤 <b>Перейти в режим клиента</b> – посмотреть бот глазами пользователя\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение"
            )
        else:  # Админ в клиентском режиме
            text = (
                "<b>🎵 Клиентский режим</b>\n\n"
                "Доступные функции:\n"
                "📅 <b>Расписание</b> – посмотреть свободные слоты на 3 дня\n"
                "✏️ <b>Записаться</b> – выбрать дату и время\n"
                "❌ <b>Отмена</b> – отменить запись по коду\n"
                "🐱 <b>Факт о котах</b> – случайный факт (тест API)\n"
                "🐶 <b>Собака</b> – случайное фото собаки (тест API)\n"
                "🏠 <b>Главное меню</b> – вернуться в это меню\n"
                "ℹ️ <b>Помощь</b> – показать это сообщение\n"
                "🛡️ <b>Перейти в режим админа</b> – вернуться к админским функциям"
            )
    # Если текст был передан явно (например, при возврате в меню), используем его

    await message.answer(text, parse_mode="HTML", reply_markup=get_main_keyboard(chat_id))


# ==================================================
#  ОБРАБОТЧИК КНОПКИ "ГЛАВНОЕ МЕНЮ" (РАБОТАЕТ В ЛЮБОМ СОСТОЯНИИ)
# ==================================================
@dp.message(lambda msg: msg.text == "🏠 Главное меню", StateFilter("*"))
async def back_to_menu(message: types.Message, state: FSMContext):
    await state.clear()
    await show_main_menu(message, "🏠 Вы вернулись в главное меню.")


# ==================================================
#  ПЕРЕКЛЮЧЕНИЕ РЕЖИМОВ АДМИНА
# ==================================================
@dp.message(lambda msg: msg.text == "👤 Перейти в режим клиента", StateFilter(None))
async def switch_to_client(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав.")
        return
    set_user_mode(message.chat.id, 'client')
    await show_main_menu(message, "🔄 Вы переключились в режим клиента.\nТеперь вы видите обычное меню.")


@dp.message(lambda msg: msg.text == "🛡️ Перейти в режим админа", StateFilter(None))
async def switch_to_admin(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав.")
        return
    set_user_mode(message.chat.id, 'admin')
    await show_main_menu(message, "🔄 Вы переключились в режим администратора.\nТеперь вам доступны админские функции.")


# ==================================================
#  ОБРАБОТЧИКИ КОМАНД И КНОПОК
# ==================================================
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        set_user_mode(message.chat.id, 'admin')
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
    today = datetime.now().date()
    current_hour = datetime.now().hour

    response_parts = []
    for i in range(3):
        date = today + timedelta(days=i)
        date_str = date.isoformat()
        all_slots = get_slots_for_date(date_str)
        free_hours = [hour for hour, is_booked, _, _ in all_slots if not is_booked]

        if date == today:
            free_hours = [h for h in free_hours if h > current_hour]

        if free_hours:
            response_parts.append(format_free_slots(date_str, free_hours))
        else:
            response_parts.append(f"<b>📅 {date_str}</b>\n  Нет свободных слотов.")

    response = "\n\n".join(response_parts)
    await message.answer(response, parse_mode="HTML", reply_markup=get_main_keyboard(message.chat.id))


@dp.message(Command("booked"))
@dp.message(lambda msg: msg.text == "📋 Занятые слоты", StateFilter(None))
async def cmd_booked(message: types.Message):
    if message.from_user.id != ADMIN_ID or get_user_mode(message.chat.id) != 'admin':
        await message.answer("⛔ У вас нет прав на эту команду или вы не в режиме администратора.")
        return

    today = datetime.now().date()
    response_parts = []
    for i in range(3):
        date = today + timedelta(days=i)
        date_str = date.isoformat()
        booked_slots = get_booked_slots_for_date(date_str)
        if booked_slots:
            response_parts.append(format_booked_slots(date_str, booked_slots))
        else:
            response_parts.append(f"<b>📅 {date_str}</b>\n  Нет занятых слотов.")

    response = "\n\n".join(response_parts)
    await message.answer(response, parse_mode="HTML", reply_markup=get_main_keyboard(message.chat.id))


# ====== ЭКСПОРТ CSV (только для админа в админ-режиме) ======
@dp.message(Command("export"))
@dp.message(lambda msg: msg.text == "📊 Экспорт CSV", StateFilter(None))
async def cmd_export(message: types.Message):
    if message.from_user.id != ADMIN_ID or get_user_mode(message.chat.id) != 'admin':
        await message.answer("⛔ У вас нет прав на эту команду или вы не в режиме администратора.")
        return

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('''
        SELECT date, hour, booked_by, phone, booking_code
        FROM slots
        WHERE is_booked = 1
        ORDER BY date, hour
    ''')
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await message.answer("📭 Нет ни одной записи для экспорта.")
        return

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Дата', 'Время', 'Имя', 'Телефон', 'Код отмены'])
    for row in rows:
        date_str = row[0]
        hour = row[1]
        time_str = f"{hour:02d}:00-{hour + 1:02d}:00"
        writer.writerow([date_str, time_str, row[2], row[3], row[4]])

    csv_data = output.getvalue().encode('utf-8-sig')
    file = io.BytesIO(csv_data)
    file.name = f"booking_export_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"

    await message.answer_document(
        document=BufferedInputFile(file.getvalue(), filename=file.name),
        caption="📊 Отчёт по бронированиям"
    )


# ==================================================
#  API ИНТЕГРАЦИЯ (CAT FACTS & DOG PICS)
# ==================================================

@dp.message(Command("catfact"))
@dp.message(lambda msg: msg.text == "🐱 Факт о котах", StateFilter(None))
async def cmd_catfact(message: types.Message):
    await message.answer("🐱 Ищу факт...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://catfact.ninja/fact') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    fact = data.get('fact', 'Не удалось получить факт :(')
                    await message.answer(f"🐱 {fact}")
                else:
                    await message.answer("😿 Не удалось получить факт. Попробуйте позже.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка API: {str(e)}")


@dp.message(Command("dog"))
@dp.message(lambda msg: msg.text == "🐶 Собака", StateFilter(None))
async def cmd_dog(message: types.Message):
    await message.answer("🐶 Ищу собаку...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get('https://dog.ceo/api/breeds/image/random') as resp:
                if resp.status == 200:
                    data = await resp.json()
                    image_url = data.get('message')
                    if image_url:
                        await message.answer_photo(photo=image_url, caption="🐶 Вот вам собачка!")
                    else:
                        await message.answer("🐕 Не удалось найти собаку.")
                else:
                    await message.answer("😢 Не удалось получить фото. Попробуйте позже.")
    except Exception as e:
        await message.answer(f"⚠️ Ошибка API: {str(e)}")


# ====== ЗАПИСЬ ======
@dp.message(Command("book"))
@dp.message(lambda msg: msg.text == "✏️ Записаться", StateFilter(None))
async def cmd_book(message: types.Message, state: FSMContext):
    today = datetime.now().date()
    dates = []
    for i in range(3):
        d = today + timedelta(days=i)
        free = get_free_slots_for_date(d.isoformat())
        if d == today:
            current_hour = datetime.now().hour
            free = [h for h in free if h > current_hour]
        if free:
            dates.append(d.isoformat())
    if not dates:
        await message.answer("😕 Нет свободных слотов на ближайшие 3 дня.",
                             reply_markup=get_main_keyboard(message.chat.id))
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=date, callback_data=f"date_{date}")]
        for date in dates
    ])
    await message.answer("Выберите дату для записи:", reply_markup=keyboard)
    await state.set_state(BookingForm.choosing_date)


@dp.callback_query(StateFilter(BookingForm.choosing_date))
async def process_date(callback: types.CallbackQuery, state: FSMContext):
    date_str = callback.data.split('_')[1]
    await state.update_data(date=date_str)
    free_hours = get_free_slots_for_date(date_str)
    if date_str == datetime.now().date().isoformat():
        current_hour = datetime.now().hour
        free_hours = [h for h in free_hours if h > current_hour]
    if not free_hours:
        await callback.message.edit_text("На эту дату нет свободных слотов. Попробуйте другую.")
        await state.clear()
        await callback.answer()
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{hour:02d}:00-{hour + 1:02d}:00", callback_data=f"slot_{hour}")]
        for hour in free_hours
    ])
    await callback.message.edit_text(f"Выберите время на {date_str}:", reply_markup=keyboard)
    await state.set_state(BookingForm.choosing_slot)
    await callback.answer()


@dp.callback_query(StateFilter(BookingForm.choosing_slot))
async def process_slot(callback: types.CallbackQuery, state: FSMContext):
    hour = int(callback.data.split('_')[1])
    await state.update_data(hour=hour)
    await callback.message.edit_text("Введите ваше имя (или nickname):")
    await state.set_state(BookingForm.entering_name)
    await callback.answer()


@dp.message(StateFilter(BookingForm.entering_name))
async def process_name(message: types.Message, state: FSMContext):
    name = message.text.strip()
    if not name:
        await message.answer("Имя не может быть пустым. Введите имя:", reply_markup=get_main_keyboard(message.chat.id))
        return
    await state.update_data(name=name)
    await message.answer("Введите ваш номер телефона (можно в свободном формате):")
    await state.set_state(BookingForm.entering_phone)


@dp.message(StateFilter(BookingForm.entering_phone))
async def process_phone(message: types.Message, state: FSMContext):
    phone = message.text.strip()
    if len(phone) != 11:
        await message.answer("❌ Номер должен содержать ровно 11 цифр (например, 89001234567). Попробуйте ещё раз.")
        return
    if not phone.isdigit():
        await message.answer("❌ Номер должен состоять только из цифр. Без пробелов, скобок и дефисов.")
        return
    if phone[0] != '8':
        await message.answer("❌ Номер должен начинаться с цифры 8.")
        return

    data = await state.get_data()
    date_str = data['date']
    hour = data['hour']
    name = data['name']
    code = book_slot(date_str, hour, name, phone)
    if code is None:
        await message.answer("❌ К сожалению, этот слот уже занят. Попробуйте выбрать другой.",
                             reply_markup=get_main_keyboard(message.chat.id))
    else:
        await message.answer(
            f"✅ Запись подтверждена!\n\n"
            f"Дата: {date_str}\n"
            f"Время: {hour:02d}:00-{hour + 1:02d}:00\n"
            f"Имя: {name}\n"
            f"Телефон: {phone}\n\n"
            f"<b>Код отмены:</b> <code>{code}</code>\n"
            f"Сохраните его, чтобы отменить запись.",
            parse_mode="HTML",
            reply_markup=get_main_keyboard(message.chat.id)
        )

        admin_text = (
            f"🔔 <b>Новая запись!</b>\n\n"
            f"Дата: {date_str}\n"
            f"Время: {hour:02d}:00-{hour + 1:02d}:00\n"
            f"Имя: {name}\n"
            f"Телефон: {phone}\n"
            f"Код: <code>{code}</code>"
        )
        try:
            await bot.send_message(ADMIN_ID, admin_text, parse_mode="HTML")
        except Exception as e:
            print(f"Не удалось отправить уведомление админу: {e}")
    await state.clear()


# ====== ОТМЕНА ======
@dp.message(Command("cancel"))
@dp.message(lambda msg: msg.text == "❌ Отмена", StateFilter(None))
async def cmd_cancel_start(message: types.Message, state: FSMContext):
    await message.answer("Введите код отмены (6 символов):", reply_markup=get_main_keyboard(message.chat.id))
    await state.set_state(CancelForm.waiting_code)


@dp.message(StateFilter(CancelForm.waiting_code))
async def process_cancel_code(message: types.Message, state: FSMContext):
    code = message.text.strip().upper()
    if len(code) != 6 or not code.isalnum():
        await message.answer("Код должен состоять из 6 букв и цифр. Попробуйте ещё раз.")
        return
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()
    cur.execute('SELECT date, hour, booked_by, phone FROM slots WHERE booking_code = ? AND is_booked = 1', (code,))
    row = cur.fetchone()
    conn.close()
    success = cancel_booking(code)
    if success:
        await message.answer("✅ Запись успешно отменена.", reply_markup=get_main_keyboard(message.chat.id))
        try:
            await bot.send_message(ADMIN_ID, f"🔕 Отменена запись по коду <code>{code}</code>", parse_mode="HTML")
        except:
            pass
    else:
        await message.answer("❌ Код не найден. Проверьте правильность ввода.",
                             reply_markup=get_main_keyboard(message.chat.id))
    await state.clear()


# ==================================================
#  ОБЩИЙ ОБРАБОТЧИК (для всего остального, когда нет состояния)
# ==================================================
@dp.message(StateFilter(None))
async def any_message(message: types.Message):
    await show_main_menu(message)


# ========== ЗАПУСК ==========
async def main():
    init_db()
    clear_old_slots()
    ensure_slots_for_days()
    print("✅ Бот запущен!")
    await dp.start_polling(bot, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    asyncio.run(main())