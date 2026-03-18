"""
run_server.py — Серверний режим.
Система запускається як сервер: слухає на всіх інтерфейсах (0.0.0.0).
Клієнти підключаються з локальної мережі або VPN.
Запуск: py run_server.py
Author: White
"""
import traceback
import sys
import socket
import subprocess
import atexit
from pathlib import Path

from core.db import set_db_path, init_db
from core.backup import auto_backup
from main import app, register_plugins

# ── Конфігурація ──────────────────────────────────────────────────────
HOST = "0.0.0.0"   # слухати на всіх інтерфейсах
PORT = 5050
RULE_NAME = "RecordSystem"

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


# ── Визначити локальні IP ─────────────────────────────────────────────
def get_local_ips():
    ips = []
    try:
        # Отримати всі адреси хоста
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None):
            ip = info[4][0]
            if ip.startswith("127.") or ":" in ip:
                continue
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    # Fallback через з'єднання
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return ips


# ── Відкрити порт у Windows Firewall ─────────────────────────────────
def open_firewall(port: int, rule_name: str) -> bool:
    """Додає правило інбаунд для TCP порту. Потребує прав адміністратора."""
    # Спочатку видалити старе правило якщо є (щоб не дублювати)
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
        capture_output=True
    )
    result = subprocess.run(
        [
            "netsh", "advfirewall", "firewall", "add", "rule",
            f"name={rule_name}",
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={port}",
            "profile=private,domain",
        ],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


# ── Закрити порт у Windows Firewall ──────────────────────────────────
def close_firewall(rule_name: str):
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule_name}"],
        capture_output=True
    )
    print(f"\n  [OK] Правило брандмауера '{rule_name}' видалено.")


# ── Відкрити порт і зареєструвати закриття при виході ────────────────
fw_ok = open_firewall(PORT, RULE_NAME)
atexit.register(close_firewall, RULE_NAME)

# ── Вивести адреси ────────────────────────────────────────────────────
local_ips = get_local_ips()

print("=" * 52)
print("  Система обліку речового майна")
print("=" * 52)
print(f"  Локально:    http://127.0.0.1:{PORT}")
for ip in local_ips:
    print(f"  Мережа:      http://{ip}:{PORT}")
if fw_ok:
    print(f"  Брандмауер:  порт {PORT} відкрито (TCP, приватна мережа)")
else:
    print(f"  [!] Брандмауер: не вдалось відкрити порт {PORT}")
    print(f"      Запустіть від імені адміністратора або відкрийте вручну.")
print("  Зупинити:    Ctrl+C  (порт закриється автоматично)")
print("=" * 52)

try:
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)
except KeyboardInterrupt:
    pass
