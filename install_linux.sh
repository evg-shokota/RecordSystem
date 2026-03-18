#!/bin/bash
# install_linux.sh — Встановлення залежностей для Linux/macOS

set -e

echo "===================================================="
echo " Облік речового майна — встановлення залежностей"
echo "===================================================="
echo

# Перевірка Python 3
if ! command -v python3 &>/dev/null; then
    echo "[ПОМИЛКА] python3 не знайдено."
    echo "Встановіть Python 3.11+:"
    echo "  Ubuntu/Debian: sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora:        sudo dnf install python3 python3-pip"
    echo "  macOS:         brew install python3"
    exit 1
fi

PYVER=$(python3 --version)
echo "[OK] $PYVER"
echo

# pywebview на Linux потребує системних бібліотек
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "Для pywebview на Linux потрібні системні бібліотеки."
    echo "Встановіть їх якщо pywebview не запускається:"
    echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0 libgtk-3-dev"
    echo
fi

# Встановлення залежностей
echo "Встановлення залежностей з requirements.txt..."
python3 -m pip install -r requirements.txt --upgrade

echo
echo "===================================================="
echo " Встановлення завершено успішно!"
echo "===================================================="
echo
echo "Запуск застосунку:"
echo "  python3 run_desktop.py   -- гібридний режим (вікно + мережа)"
echo "  python3 run_webview.py   -- тільки вікно (без мережевого доступу)"
echo "  python3 run_server.py    -- тільки сервер (для браузера)"
echo "  python3 run_dev.py       -- режим розробки"
echo
