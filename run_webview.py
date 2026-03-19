"""
run_webview.py — Десктопний режим (WebView).
Сервер запускається тільки на 127.0.0.1 (без доступу з мережі).
Автоматично відкриває вікно pywebview.
Запуск: py run_webview.py
Author: White
"""
import traceback
import sys
import threading
import atexit
from pathlib import Path

from core.db import set_db_path, init_db
from core.backup import auto_backup, shutdown_backup
from main import app, register_plugins

# ── Конфігурація ──────────────────────────────────────────────────────
HOST = "127.0.0.1"   # тільки локально — без мережевого доступу
PORT = 5050
APP_TITLE = "Облік речового майна"

DB_PATH = str(Path(__file__).parent / "database.db")

set_db_path(DB_PATH)
init_db()

try:
    auto_backup()
except Exception as e:
    print(f"[WARN] backup: {e}")

atexit.register(lambda: shutdown_backup())

try:
    register_plugins(app)
except Exception:
    print("[ERROR] register_plugins failed:")
    traceback.print_exc()
    sys.exit(1)


def run_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


print("=" * 52)
print(f"  {APP_TITLE}")
print("=" * 52)
print(f"  Режим:   Десктопний (WebView)")
print(f"  Сервер:  http://{HOST}:{PORT}  (тільки локально)")
print(f"  Зупинити: закрити вікно застосунку")
print("=" * 52)

# Запускаємо Flask у фоні
flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

# Відкриваємо WebView
try:
    import webview
    window = webview.create_window(
        APP_TITLE,
        f"http://{HOST}:{PORT}",
        width=1400,
        height=900,
        resizable=True,
        min_size=(1024, 600),
    )
    webview.start(debug=False)
except ImportError:
    print("[ERROR] pywebview не встановлено.")
    print("  Встановіть: pip install pywebview")
    print(f"  Або відкрийте браузер: http://{HOST}:{PORT}")
    # Якщо WebView недоступний — тримаємо Flask живим
    flask_thread.join()
except Exception as e:
    print(f"[ERROR] WebView помилка: {e}")
    flask_thread.join()
