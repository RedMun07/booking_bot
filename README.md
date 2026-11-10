# Booking Bot — запись в студию «Атмосфера»

Telegram-бот на **aiogram 3**: запись на часовые слоты, расписание на 3 дня,
отмена по коду, админ-режим с просмотром занятых слотов и экспортом в CSV.

## Возможности

**Клиент**
- 📅 расписание свободных слотов на 3 дня;
- ✏️ запись: дата → время → имя → телефон;
- 🔑 код отмены (6 символов), ❌ отмена записи по коду.

**Администратор** (`ADMIN_ID`)
- 📋 занятые слоты за 3 дня с контактами и кодами;
- 📊 экспорт всех бронирований в CSV (разделитель `;`, UTF-8 BOM — открывается в Excel);
- 👤 переключение в клиентский режим и обратно;
- 🔔 уведомления о новых записях и отменах.

## Локальный запуск

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env             # Windows: copy .env.example .env
# вписать BOT_TOKEN и ADMIN_ID в .env

python app.py
```

Приложение само читает `.env` из своей папки, поэтому переменные окружения
выставлять вручную не обязательно.

Свой Telegram ID можно узнать у `@userinfobot` или командой `/id` в этом боте.

Переменные окружения:

| Переменная | Обязательна | Назначение |
|---|---|---|
| `BOT_TOKEN` | да | токен от `@BotFather` |
| `ADMIN_ID` | нет | Telegram ID администратора; без него админ-функции и уведомления отключены |
| `DB_PATH` | нет | путь к SQLite-файлу, по умолчанию `studio.db` |

## Деплой на Render.com

В репозитории есть `render.yaml` — Blueprint для Background Worker.

1. Войдите на [dashboard.render.com](https://dashboard.render.com) через GitHub.
2. **New → Blueprint** → выберите репозиторий `booking_bot` → **Apply**.
3. Render попросит значения `BOT_TOKEN` и `ADMIN_ID` (они помечены `sync: false`).
4. Дождитесь деплоя, логи — в разделе **Logs**.

Особенности:

- **Background Worker — платный**, бесплатного тарифа для воркеров у Render нет
  (порядка $7/мес, уточняйте в дашборде).
- **Диск обязателен**: SQLite на эфемерном диске обнуляется при каждом деплое.
  В Blueprint уже прописан диск 1 ГБ (`/var/data`).
- Регион `frankfurt` — ближайший к России.
- Для РФ есть юридический нюанс: бот хранит имена и телефоны клиентов, а это
  персональные данные по 152-ФЗ, которые должны обрабатываться в РФ.

## Деплой на VPS (Ubuntu 22.04/24.04)

```bash
ssh root@ВАШ_IP
git clone https://github.com/RedMun07/booking_bot.git /opt/booking_bot
cd /opt/booking_bot
bash deploy/deploy.sh
nano /opt/booking_bot/.env      # вписать BOT_TOKEN и ADMIN_ID
systemctl restart booking-bot
```

Скрипт сам: ставит python/venv/sqlite, создаёт окружение, поднимает
systemd-юнит `booking-bot` с автоперезапуском, включает автозапуск при загрузке.

Полезные команды:

```bash
systemctl status booking-bot     # состояние
journalctl -u booking-bot -f     # логи в реальном времени
systemctl restart booking-bot    # перезапуск
```

Обновление после `git push`:

```bash
cd /opt/booking_bot && git pull && systemctl restart booking-bot
```

## Бэкапы

```bash
crontab -e
# каждый день в 04:15
15 4 * * * /opt/booking_bot/deploy/backup.sh >> /var/log/booking-bot-backup.log 2>&1
```

Скрипт делает `sqlite3 .backup` (безопасно при работающем боте), сжимает
и удаляет копии старше 14 дней из `/var/backups/booking_bot`.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Проверяют слой хранения (создание слотов, атомарную запись, отмену,
уникальность кодов); токен и сеть не нужны.

## Структура

```
app.py                    — хендлеры aiogram, клавиатуры, форматирование
storage.py                — SQLite: слоты, бронирования, отмена
tests/test_storage.py     — тесты слоя хранения
deploy/deploy.sh          — установка на сервер
deploy/booking-bot.service— systemd-юнит (шаблон)
deploy/backup.sh          — бэкап базы
.env.example              — шаблон конфигурации
```

## Как это работает

- Слоты создаются на 3 дня вперёд (09:00–18:00), недостающие досоздаются при старте.
- Запись атомарна: `UPDATE ... WHERE is_booked = 0`; если слот уже занят,
  пользователь получает сообщение и может выбрать другой.
- Коды отмены уникальны (`UNIQUE` + повтор при коллизии).
- Прошедшие свободные слоты удаляются при старте, история записей сохраняется.
