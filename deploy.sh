#!/bin/bash
cd /root/projects/myBarKeeperBot
git fetch origin
git reset --hard origin/release
source venv/bin/activate
pip install -r requirements.txt
deactivate
systemctl restart telegram-bot