@echo off
chcp 65001 >nul
setlocal

echo ====================================================
echo  Облік речового майна — встановлення залежностей
echo ====================================================
echo.

:: Перевірка наявності Python
where py >nul 2>&1
if %errorlevel% neq 0 (
    echo [ПОМИЛКА] Python не знайдено.
    echo Завантажте Python 3.11+ з https://python.org та встановіть.
    echo Обов'язково позначте "Add Python to PATH" при встановленні.
    pause
    exit /b 1
)

for /f "tokens=*" %%i in ('py --version 2^>^&1') do set PYVER=%%i
echo [OK] %PYVER%
echo.

:: Встановлення залежностей
echo Встановлення залежностей з requirements.txt...
py -m pip install -r requirements.txt --upgrade

if %errorlevel% neq 0 (
    echo.
    echo [ПОМИЛКА] Не вдалось встановити залежності.
    echo Перевірте підключення до мережі та спробуйте ще раз.
    pause
    exit /b 1
)

echo.
echo ====================================================
echo  Встановлення завершено успішно!
echo ====================================================
echo.
echo Запуск застосунку:
echo   py run_desktop.py   -- гібридний режим (вікно + мережа)
echo   py run_webview.py   -- тільки вікно (без мережевого доступу)
echo   py run_server.py    -- тільки сервер (для браузера)
echo   py run_dev.py       -- режим розробки
echo.
pause
