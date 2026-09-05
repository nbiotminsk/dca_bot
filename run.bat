@echo off
chcp 65001 >nul
title Fibonacci Dual Grid & Bybit DCA Bot

:: Определение команды запуска (uv или стандартный python)
where uv >nul 2>nul
if %errorlevel% equ 0 (
    set "RUNNER=uv run python"
    set "TEST_RUNNER=uv run pytest"
) else (
    set "RUNNER=python"
    set "TEST_RUNNER=pytest"
)

:MENU
cls
echo ===============================================================================
echo          FIBONACCI DUAL GRID ^& DCA BOT — ПАНЕЛЬ УПРАВЛЕНИЯ (WINDOWS)
echo ===============================================================================
echo.
echo   [1] 🚀 Запустить торгового бота на Bybit (БОЕВОЙ РЕЖИМ / LIVE)
echo   [2] 🔍 Безопасный предпросмотр сетапов (DRY-RUN / Без ордеров)
echo   [3] 📊 Интерактивный бэктест стратегии (Backtest)
echo   [4] 🌊 Исследовательский бэктест ATR и таймаута (90 дней)
echo   [5] 🕯️ Исследование характера 2-й свечи (Тело vs Фитиль)
echo   [6] 🧪 Запустить системные тесты (Pytest)
echo.
echo   [0] ❌ Выход
echo.
echo ===============================================================================
set /p opt="Выберите пункт [0-6] и нажмите Enter: "

if "%opt%"=="1" goto LIVE_BOT
if "%opt%"=="2" goto DRY_RUN
if "%opt%"=="3" goto BACKTEST
if "%opt%"=="4" goto ATR_RESEARCH
if "%opt%"=="5" goto WICK_RESEARCH
if "%opt%"=="6" goto TESTS
if "%opt%"=="0" goto EXIT

echo.
echo [!] Неверный выбор, попробуйте снова.
timeout /t 2 >nul
goto MENU

:LIVE_BOT
cls
echo ===============================================================================
echo [!] ВНИМАНИЕ: Запуск боевого режима торговли на Bybit!
echo     Бот будет выставлять реальные ордера согласно config/trade_config.yaml
echo ===============================================================================
echo.
%RUNNER% scripts/bybit_trader.py --live -y
echo.
echo Бот остановлен.
pause
goto MENU

:DRY_RUN
cls
echo ===============================================================================
echo 🔍 Запуск в режиме Dry-Run (безопасный просмотр без реальных сделок)...
echo ===============================================================================
echo.
%RUNNER% scripts/bybit_trader.py
echo.
pause
goto MENU

:BACKTEST
cls
echo ===============================================================================
echo 📊 Запуск интерактивного бэктестера стратегии...
echo ===============================================================================
echo.
%RUNNER% scripts/backtest_strategy_interactive.py
echo.
pause
goto MENU

:ATR_RESEARCH
cls
echo ===============================================================================
echo 🌊 Запуск теста волатильности ATR и таймаута свежести (90 дней, NEAR/ZEC/ARB)...
echo ===============================================================================
echo.
%RUNNER% scripts/backtest_filters_research.py
echo.
pause
goto MENU

:WICK_RESEARCH
cls
echo ===============================================================================
echo 🕯️ Запуск исследования пробоя 2-й свечи (Телом vs Фитилем)...
echo ===============================================================================
echo.
%RUNNER% scripts/research_wick_vs_body.py
echo.
pause
goto MENU

:TESTS
cls
echo ===============================================================================
echo 🧪 Запуск набора автотестов (Pytest)...
echo ===============================================================================
echo.
%TEST_RUNNER%
echo.
pause
goto MENU

:EXIT
exit /b 0
