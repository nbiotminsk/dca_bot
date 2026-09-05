# ===============================================================================
# Fibonacci Dual Grid & DCA Bot — Меню запуска для Windows (PowerShell)
# ===============================================================================

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$Host.UI.RawUI.WindowTitle = "Fibonacci Dual Grid & DCA Bot"

# Проверка наличия uv или стандартного python
if (Get-Command uv -ErrorAction SilentlyContinue) {
    $Runner = "uv run python"
    $TestRunner = "uv run pytest"
} else {
    $Runner = "python"
    $TestRunner = "pytest"
}

function Show-Menu {
    Clear-Host
    Write-Host "===============================================================================" -ForegroundColor Cyan
    Write-Host "         FIBONACCI DUAL GRID & DCA BOT — ПАНЕЛЬ УПРАВЛЕНИЯ" -ForegroundColor Yellow
    Write-Host "===============================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  [1] 🚀 Запустить торгового бота на Bybit (БОЕВОЙ РЕЖИМ / LIVE)" -ForegroundColor Green
    Write-Host "  [2] 🔍 Безопасный предпросмотр сетапов (DRY-RUN / Без ордеров)" -ForegroundColor White
    Write-Host "  [3] 📊 Интерактивный бэктест стратегии (Backtest)" -ForegroundColor Cyan
    Write-Host "  [4] 🌊 Исследовательский бэктест ATR и таймаута (90 дней)" -ForegroundColor Magenta
    Write-Host "  [5] 🕯️ Исследование характера 2-й свечи (Тело vs Фитиль)" -ForegroundColor Yellow
    Write-Host "  [6] 🧪 Запустить системные тесты (Pytest)" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  [0] ❌ Выход" -ForegroundColor Red
    Write-Host ""
    Write-Host "===============================================================================" -ForegroundColor Cyan
}

do {
    Show-Menu
    $choice = Read-Host "Выберите пункт [0-6]"

    switch ($choice) {
        "1" {
            Clear-Host
            Write-Host "===============================================================================" -ForegroundColor Yellow
            Write-Host "[!] ВНИМАНИЕ: Запуск боевого режима торговли на Bybit!" -ForegroundColor Red
            Write-Host "    Бот будет выставлять реальные ордера согласно config/trade_config.yaml" -ForegroundColor Gray
            Write-Host "===============================================================================" -ForegroundColor Yellow
            Write-Host ""
            Invoke-Expression "$Runner scripts/bybit_trader.py --live -y"
            Write-Host ""
            Write-Host "Бот остановлен." -ForegroundColor Yellow
            Pause
        }
        "2" {
            Clear-Host
            Write-Host "🔍 Запуск в режиме Dry-Run (безопасный просмотр)..." -ForegroundColor Cyan
            Write-Host ""
            Invoke-Expression "$Runner scripts/bybit_trader.py"
            Write-Host ""
            Pause
        }
        "3" {
            Clear-Host
            Write-Host "📊 Запуск интерактивного бэктестера стратегии..." -ForegroundColor Cyan
            Write-Host ""
            Invoke-Expression "$Runner scripts/backtest_strategy_interactive.py"
            Write-Host ""
            Pause
        }
        "4" {
            Clear-Host
            Write-Host "🌊 Запуск теста волатильности ATR и таймаута (90 дней)..." -ForegroundColor Magenta
            Write-Host ""
            Invoke-Expression "$Runner scripts/backtest_filters_research.py"
            Write-Host ""
            Pause
        }
        "5" {
            Clear-Host
            Write-Host "🕯️ Запуск исследования пробоя 2-й свечи (Телом vs Фитилем)..." -ForegroundColor Yellow
            Write-Host ""
            Invoke-Expression "$Runner scripts/research_wick_vs_body.py"
            Write-Host ""
            Pause
        }
        "6" {
            Clear-Host
            Write-Host "🧪 Запуск набора автотестов..." -ForegroundColor Green
            Write-Host ""
            Invoke-Expression "$TestRunner"
            Write-Host ""
            Pause
        }
        "0" {
            Write-Host "Выход." -ForegroundColor Gray
            break
        }
        default {
            Write-Host "Неверный выбор. Повторите попытку..." -ForegroundColor Red
            Start-Sleep -Seconds 1
        }
    }
} while ($choice -ne "0")
