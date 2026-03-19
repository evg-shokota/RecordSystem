"""
core/backup.py — резервне копіювання БД

Стратегія ротації (grandfathered):
  - Сьогоднішні:  3 останніх копії за сьогодні
  - Щоденні:      1 копія за кожен з останніх 7 днів (крім сьогодні)
  - Недільні:     1 копія за кожну з останніх 4 неділь
                  (враховується дата ФАЙЛУ, не поточний день)
  - Місячні:      1 копія за кожен з останніх 12 місяців
  - Річні:        1 копія за кожен рік

Автозапуск: не більше 1 бекапу на сесію (перевіряємо чи вже є за останні N хвилин).
"""
import shutil
from datetime import datetime, timedelta, date
from pathlib import Path
from core.db import get_db_path
from core.settings import get_setting, set_setting


_SESSION_BACKUP_DONE = False   # Guard: один бекап за сесію запуску


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

    Захист від дублів: якщо вже є бекап за останні 30 хвилин — пропускаємо.
    Тип бекапу визначається за поточною датою:
      - 1 січня → yearly
      - 1 число місяця → monthly
      - неділя → weekly
      - інакше → daily
    Після бекапу — ротація за GFS-стратегією.
    """
    global _SESSION_BACKUP_DONE
    if _SESSION_BACKUP_DONE:
        return None

    now = datetime.now()
    backup_dir = get_backup_dir()

    # Перевірка: чи є вже бекап за останні 30 хвилин
    cutoff = now - timedelta(minutes=30)
    for f in backup_dir.glob("backup_*.db"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime >= cutoff:
                _SESSION_BACKUP_DONE = True
                return None
        except OSError:
            pass

    # Визначаємо тип за поточною датою
    if now.day == 1 and now.month == 1:
        label = "yearly"
    elif now.day == 1:
        label = "monthly"
    elif now.weekday() == 6:  # неділя
        label = "weekly"
    else:
        label = "daily"

    dest = do_backup(label)
    _rotate_backups(backup_dir)
    _SESSION_BACKUP_DONE = True
    return dest


def _file_date(f: Path) -> date:
    """Дата файлу бекапу — з назви (YYYYMMDD), fallback на mtime."""
    try:
        # backup_daily_20260319_125452.db → частина [2] = "20260319"
        parts = f.stem.split("_")
        date_part = next(p for p in parts if len(p) == 8 and p.isdigit())
        return date(int(date_part[:4]), int(date_part[4:6]), int(date_part[6:8]))
    except (StopIteration, ValueError):
        return date.fromtimestamp(f.stat().st_mtime)


def _rotate_backups(backup_dir: Path) -> None:
    """
    GFS-ротація (Grandfather-Father-Son).

    Правила (за датою файлу):
      - Сьогодні:  залишити 3 останніх
      - Щоденні:   за кожен з 7 попередніх днів — залишити по 1 (найсвіжішу)
      - Недільні:  за кожну з 4 останніх неділь — залишити по 1 (найсвіжішу)
                   Неділя = файл датований неділею (weekday == 6)
      - Місячні:   за кожен з останніх 12 місяців — залишити по 1 (найсвіжішу)
      - Річні:     за кожен рік — залишити по 1 (найсвіжішу)
      - manual/*   — не ротувати
    """
    today = date.today()

    # Збираємо всі автоматичні бекапи (не manual)
    all_files: list[Path] = [
        f for f in backup_dir.glob("backup_*.db")
        if "manual" not in f.name
    ]
    if not all_files:
        return

    # Сортуємо від найновіших до найстаріших
    all_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    keep: set[Path] = set()

    # ── Сьогоднішні (3 останніх) ──
    todays = [f for f in all_files if _file_date(f) == today]
    for f in todays[:3]:
        keep.add(f)

    # ── Щоденні: по 1 за кожен з 7 попередніх днів ──
    for delta in range(1, 8):
        day = today - timedelta(days=delta)
        days_files = [f for f in all_files if _file_date(f) == day]
        if days_files:
            keep.add(days_files[0])  # найсвіжіша за той день

    # ── Недільні: по 1 за кожну з 4 останніх неділь ──
    # Знаходимо останні 4 неділі відносно today
    last_sundays: list[date] = []
    d = today
    while len(last_sundays) < 4:
        if d.weekday() == 6:
            last_sundays.append(d)
        d -= timedelta(days=1)

    for sunday in last_sundays:
        # Файли, датовані цією неділею
        sunday_files = [f for f in all_files if _file_date(f) == sunday]
        if sunday_files:
            keep.add(sunday_files[0])
        else:
            # Немає файлу саме з неділі — шукаємо найближчий файл того тижня
            # (понеділок..субота того ж тижня) якщо немає точного
            week_start = sunday - timedelta(days=6)
            week_files = [
                f for f in all_files
                if week_start <= _file_date(f) <= sunday
            ]
            if week_files:
                keep.add(week_files[0])

    # ── Місячні: по 1 за кожен з останніх 12 місяців ──
    for months_ago in range(1, 13):
        # Перший день того місяця
        target_month = today.month - months_ago
        target_year = today.year + (target_month - 1) // 12
        target_month = ((target_month - 1) % 12) + 1
        month_files = [
            f for f in all_files
            if _file_date(f).year == target_year and _file_date(f).month == target_month
        ]
        if month_files:
            keep.add(month_files[0])

    # ── Річні: по 1 за кожен рік ──
    years_seen: dict[int, Path] = {}
    for f in all_files:
        y = _file_date(f).year
        if y not in years_seen:
            years_seen[y] = f
    keep.update(years_seen.values())

    # ── Видалити все що не в keep ──
    for f in all_files:
        if f not in keep:
            try:
                f.unlink()
            except OSError:
                pass


def shutdown_backup() -> Path | None:
    """
    Бекап при завершенні роботи.

    Логіка:
      1. Якщо БД не змінювалась з часу останнього бекапу за сьогодні — нічого не робимо.
      2. Якщо змінювалась — робимо бекап і замінюємо найстарішу копію за сьогодні
         (щоб загальна кількість сьогоднішніх копій не перевищувала 3).
    """
    db_path = Path(get_db_path())
    if not db_path.exists():
        return None

    backup_dir = get_backup_dir()
    today = date.today()

    # Всі бекапи за сьогодні (не manual), відсортовані від найновіших
    todays = sorted(
        [f for f in backup_dir.glob("backup_*.db")
         if "manual" not in f.name and _file_date(f) == today],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    db_mtime = datetime.fromtimestamp(db_path.stat().st_mtime)

    # Якщо вже є бекап новіший або рівний БД — зміни відсутні, не робимо
    if todays:
        newest_backup_mtime = datetime.fromtimestamp(todays[0].stat().st_mtime)
        if newest_backup_mtime >= db_mtime:
            return None

    # Є зміни — робимо новий бекап
    dest = do_backup("daily")
    _rotate_backups(backup_dir)

    # Додатково: якщо після ротації за сьогодні стало > 3 — видаляємо найстарішу
    todays_after = sorted(
        [f for f in backup_dir.glob("backup_*.db")
         if "manual" not in f.name and _file_date(f) == today],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for f in todays_after[3:]:
        try:
            f.unlink()
        except OSError:
            pass

    return dest


def manual_backup() -> Path:
    """Ручний бекап — завжди зберігається, не ротується."""
    return do_backup("manual")


def create_full_backup() -> Path:
    """ZIP-архів: database.db + вся папка storage. Для повного бекапу та міграції."""
    import zipfile
    from core.settings import get_storage_path

    db_path = Path(get_db_path())
    storage_path = get_storage_path()
    backup_dir = get_backup_dir()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = backup_dir / f"full_backup_{timestamp}.zip"

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_path, "database.db")
        if storage_path.exists():
            for f in storage_path.rglob("*"):
                if f.is_file():
                    zf.write(f, "storage/" + str(f.relative_to(storage_path)).replace("\\", "/"))
    return zip_path


def restore_full_backup(zip_path: Path) -> None:
    """Розпаковує повний бекап: БД + файли storage.
    УВАГА: перезаписує поточну БД та файли. Потрібен перезапуск після відновлення."""
    import zipfile
    from core.settings import get_storage_path

    db_path = Path(get_db_path())
    storage_path = get_storage_path()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        names = zf.namelist()
        if "database.db" not in names:
            raise ValueError("Архів не містить database.db — можливо це не повний бекап системи")

        tmp_db = db_path.parent / "database_restore_tmp.db"
        with zf.open("database.db") as src, open(tmp_db, "wb") as dst:
            dst.write(src.read())
        shutil.copy2(tmp_db, db_path)
        tmp_db.unlink(missing_ok=True)

        for name in names:
            if name.startswith("storage/") and not name.endswith("/"):
                rel = name[len("storage/"):]
                target = storage_path / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(target, "wb") as dst:
                    dst.write(src.read())


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
