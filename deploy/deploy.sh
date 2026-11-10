#!/usr/bin/env bash
# Установка бота на чистый сервер Ubuntu/Debian.
# Запуск:  sudo bash deploy/deploy.sh
set -euo pipefail

APP_DIR="/opt/booking_bot"
REPO_URL="https://github.com/RedMun07/booking_bot.git"
SERVICE="booking-bot"
RUN_USER="${SUDO_USER:-$USER}"

echo "==> Обновление пакетов"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git sqlite3 curl

echo "==> Пользователь: $RUN_USER"

echo "==> Код в $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" pull --ff-only
else
    mkdir -p "$APP_DIR"
    git clone "$REPO_URL" "$APP_DIR"
fi
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

echo "==> Виртуальное окружение"
sudo -u "$RUN_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$RUN_USER" "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
    echo "==> Создаю .env из шаблона"
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    chown "$RUN_USER":"$RUN_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    echo
    echo "!!! Впишите в $APP_DIR/.env свой BOT_TOKEN и ADMIN_ID:"
    echo "    nano $APP_DIR/.env"
    echo
fi

echo "==> systemd-юнит"
sed "s/__USER__/$RUN_USER/g" "$APP_DIR/deploy/booking-bot.service" \
    > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE"
systemctl restart "$SERVICE"

sleep 3
systemctl --no-pager status "$SERVICE" | head -n 12 || true

echo
echo "Готово. Логи:  journalctl -u $SERVICE -f"
echo "Если в .env ещё нет токена — бот будет перезапускаться с ошибкой, это нормально."
