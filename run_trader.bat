@echo off
chcp 65001 > nul
setlocal enabledelayedexpansion

:: Переходим в директорию, где лежит этот bat-файл
cd /d "%~dp0"

echo ==========================================================
echo 🤖 Bybit Fibonacci Dual Grid & Trailing Trader (Windows)
echo ==========================================================
echo.

:: 1. Проверяем, установлен ли менеджер uv
where uv >nul 2>nul
if %errorlevel% equ 0 (
    echo [OK] Найден менеджер окружения uv.
    echo Запуск через uv run...
    echo.
    uv run python scripts\bybit_trader.py --live -y %*
    goto :end
)

:: 2. Если uv нет, проверяем стандартное виртуальное окружение .venv
if not exist ".venv\Scripts\activate.bat" (
    echo [INFO] Виртуальное окружение .venv не найдено. Создаем новое...
    where python >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Python не найден в системе! Установите Python 3.11+ с галочкой 'Add to PATH'.
        pause
        exit /b 1
    )
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Не удалось создать виртуальное окружение .venv.
        pause
        exit /b 1
    )
    echo [OK] Окружение .venv создано.
    echo [INFO] Установка зависимостей из pyproject.toml...
    call .venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    python -m pip install -e .
    echo [OK] Все зависимости успешно установлены!
    echo.
) else (
    call .venv\Scripts\activate.bat
)

:: 3. Запуск торгового бота через Python внутри виртуального окружения
echo [INFO] Запуск торгового бота...
echo.
python scripts\bybit_trader.py --live -y %*

:end
if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Бот завершил работу с кодом ошибки %errorlevel%.
    pause
)
