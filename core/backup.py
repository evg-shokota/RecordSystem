"""
core/backup.py — резервне копіювання БД
Ротація: 7 щоденних, 1 недільний, 1 місячний, 1 річний
"""
import shutil
import os
from datetime import datetime, timedelta
from pathlib import Path
from core.db import get_db_path
from core.settings import get_setting, set_setting


def get_backup_dir() -> Path:
    db_path = Path(get_db_path())
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir


def do_backup(label: str = "daily") -> Path:
    """
    Зробити резервну копію БД.
    label: daily | weekly | monthly | yearly | manual
    """
    db_path = Path(get_db_path())
    if not db_path.exists():
        raise FileNotFoundError(f"БД не знайдено: {db_path}")

    backup_dir = get_backup_dir()
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    filename = f"backup_{label}_{timestamp}.db"
    dest = backup_dir / filename
    shutil.copy2(db_path, dest)
    set_setting("last_backup_at", now.strftime("%Y-%m-%d %H:%M:%S"))
    return dest


def auto_backup() -> Path | None:
    """
    Виконати автоматичний бекап при запуску.
    Визначає тип бекапу (daily/weekly/monthly/yearly) і прибирає старі.
    """
    now = datetime.now()
    backup_dir = get_backup_dir()

    # Визначаємо тип
    if now.day == 1 and now.month == 1:
        label = "yearly"
    elif now.day == 1:
        label = "monthly"
    elif now.weekday() == 0:  # понеділок
        label = "weekly"
    else:
        label = "daily"

    dest = do_backup(label)
    _rotate_backups(backup_dir)
    return dest


def _rotate_backups(backup_dir: Path) -> None:
    """Прибрати зайві бекапи відповідно до ротації."""
    rules = {
        "daily": 7,
        "weekly": 1,
        "monthly": 1,
        "yearly": 1,
    }
    for label, keep in rules.items():
        files = sorted(
            backup_dir.glob(f"backup_{label}_*.db"),
            key=lambda f: f.stat().st_mtime,
            reverse=True
        )
        for old_file in files[keep:]:
            try:
                old_file.unlink()
            except OSError:
                pass


def manual_backup() -> Path:
    """Ручний бекап — завжди зберігається, не ротується."""
    return do_backup("manual")


def check_backup_reminder() -> bool:
    """
    Перевірити чи потрібне нагадування про бекап.
    Повертає True якщо останній бекап був більше N днів тому.
    """
    last = get_setting("last_backup_at", "")
    if not last:
        return True
    try:
        last_dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
        days = int(get_setting("backup_reminder_days", "3"))
        return (datetime.now() - last_dt) > timedelta(days=days)
    except ValueError:
        return True


def get_backup_list() -> list[dict]:
    """Список всіх бекапів."""
    backup_dir = get_backup_dir()
    result = []
    for f in sorted(backup_dir.glob("backup_*.db"), key=lambda x: x.stat().st_mtime, reverse=True):
        parts = f.stem.split("_")
        label = parts[1] if len(parts) > 1 else "unknown"
        result.append({
            "filename": f.name,
            "path": str(f),
            "label": label,
            "size_kb": round(f.stat().st_size / 1024, 1),
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d.%m.%Y %H:%M"),
        })
    return result
