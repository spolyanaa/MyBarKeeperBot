#!/bin/bash
set -e  # если что-то падает — скрипт сразу завершится с ошибкой

PROJECT_DIR="/root/projects/myBarKeeperBot"
VENV_DIR="$PROJECT_DIR/venv"
BRANCH="main"
SERVICE_NAME="MyBarKeeper-bot"

echo "🚀 Starting deployment for $SERVICE_NAME..."

cd "$PROJECT_DIR"

echo "📦 Updating repository..."
git fetch origin
git reset --hard "origin/$BRANCH"

echo "🐍 Activating virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    echo "🔧 Virtual environment not found — creating..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

echo "📥 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

deactivate

echo "🔁 Restarting systemd service: $SERVICE_NAME"
systemctl daemon-reload
systemctl restart "$SERVICE_NAME"

echo "✅ Deployment complete!"
