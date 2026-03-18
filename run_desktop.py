"""
run_desktop.py — Гібридний режим: сервер + WebView.
Сервер слухає на всіх інтерфейсах (0.0.0.0) — доступний і з мережі,
і відкриває локальне вікно pywebview для роботи на самому ПК-сервері.
Запуск: py run_desktop.py
Author: White
"""
import traceback
import sys
import socket
import subprocess
import atexit
import threading
from pathlib import Path

from core.db import set_db_path, init_db
from core.backup import auto_backup
from main import app, register_plugins

# ── Конфігурація ──────────────────────────────────────────────────────
HOST = "0.0.0.0"
PORT = 5050
RULE_NAME = "RecordSystem"
APP_TITLE = "Облік речового майна"

DB_PATH = str(Path(__file__).parent / "database.db")

set_db_path(DB_PATH)
init_db()

try:
    auto_backup()
except Exception as e:
    print(f"[WARN] backup: {e}")

try:
    register_plugins(app)
except Exception:
    print("[ERROR] register_plugins failed:")
    traceback.print_exc()
    sys.exit(1)


# ── Локальні IP ───────────────────────────────────────────────────────
def get_local_ips():
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip.startswith("127.") or ":" in ip:
                continue
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return ips


# ── Брандмауер ────────────────────────────────────────────────────────
def open_firewall(port: int, rule_name: str) -> bool:
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
        capture_output=True
    )
    result = subprocess.run(
        [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}", "dir=in", "action=allow",
            "protocol=TCP", f"localport={port}", "profile=private,domain",
        ],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def close_firewall(rule_name: str):
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
        capture_output=True
    )
    print(f"\n  [OK] Правило брандмауера '{rule_name}' видалено.")


fw_ok = open_firewall(PORT, RULE_NAME)
atexit.register(close_firewall, RULE_NAME)

local_ips = get_local_ips()

print("=" * 52)
print(f"  {APP_TITLE}")
print("=" * 52)
print(f"  Режим:       Гібридний (сервер + WebView)")
print(f"  Локально:    http://127.0.0.1:{PORT}")
for ip in local_ips:
    print(f"  Мережа:      http://{ip}:{PORT}")
if fw_ok:
    print(f"  Брандмауер:  порт {PORT} відкрито (TCP, приватна мережа)")
else:
    print(f"  [!] Брандмауер: не вдалось відкрити порт {PORT}")
print("  Зупинити:    закрити вікно або Ctrl+C")
print("=" * 52)


def run_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


flask_thread = threading.Thread(target=run_flask, daemon=True)
flask_thread.start()

try:
    import webview
    window = webview.create_window(
        APP_TITLE,
        f"http://127.0.0.1:{PORT}",
        width=1400,
        height=900,
        resizable=True,
        min_size=(1024, 600),
    )
    webview.start(debug=False)
except ImportError:
    print("[ERROR] pywebview не встановлено.")
    print("  Встановіть: pip install pywebview")
    print(f"  Або відкрийте браузер: http://127.0.0.1:{PORT}")
    flask_thread.join()
except Exception as e:
    print(f"[ERROR] WebView помилка: {e}")
    flask_thread.join()
