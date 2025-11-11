#!/bin/bash
set -e  # прекращаем выполнение при любой ошибке

PROJECT_DIR="/root/projects/myBarKeeperBot"
VENV_DIR="$PROJECT_DIR/venv"
BRANCH="main"
SERVICE_NAME="telegram-bot"

echo "🚀 Starting deployment for $SERVICE_NAME..."

cd "$PROJECT_DIR"

echo "📦 Updating repository..."
git fetch origin
git reset --hard "origin/$BRANCH"

echo "🐍 Setting up virtual environment..."
# Если виртуальное окружение не существует, создаем чистое
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 Virtual environment not found — creating..."
    python3 -m venv "$VENV_DIR"
fi

# Активируем окружение
source "$VENV_DIR/bin/activate"

# Обновляем pip, setuptools и wheel внутри виртуального окружения
echo "📥 Upgrading pip, setuptools, wheel..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel

# Устанавливаем зависимости проекта внутри виртуального окружения
echo "📥 Installing project dependencies..."
"$VENV_DIR/bin/pip" install -r requirements.txt

# Деактивируем виртуальное окружение
deactivate

echo "🔁 Restarting systemd service: $SERVICE_NAME"
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

echo "✅ Deployment complete!"