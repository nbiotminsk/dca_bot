#!/usr/bin/env python3
"""
Бэктест стратегии Dual Grid на ТОП-30 монетах за 6 месяцев (180 дней).
Сравнение: Без фильтра vs Фильтр MACD (12, 26, 9).
Параметры: 1h, размах >= 2.0%, риск $10/ордер, allow_internal=True.
"""
import sys
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses
from scripts.strategy_engine import GridConfig, simulate_grid, summarize

COINS_30 = [
    ('BTC',    'BTCUSDT'),
    ('ETH',    'ETHUSDT'),
    ('SOL',    'SOLUSDT'),
    ('BNB',    'BNBUSDT'),
    ('XRP',    'XRPUSDT'),
    ('DOGE',   'DOGEUSDT'),
    ('ADA',    'ADAUSDT'),
    ('AVAX',   'AVAXUSDT'),
    ('LINK',   'LINKUSDT'),
    ('UNI',    'UNIUSDT'),
    ('SUI',    'SUIUSDT'),
    ('NEAR',   'NEARUSDT'),
    ('APT',    'APTUSDT'),
    ('LTC',    'LTCUSDT'),
    ('BCH',    'BCHUSDT'),
    ('DOT',    'DOTUSDT'),
    ('ICP',    'ICPUSDT'),
    ('TIA',    'TIAUSDT'),
    ('INJ',    'INJUSDT'),
    ('SEI',    'SEIUSDT'),
    ('RENDER', 'RENDERUSDT'),
    ('ATOM',   'ATOMUSDT'),
    ('FIL',    'FILUSDT'),
    ('AAVE',   'AAVEUSDT'),
    ('CRV',    'CRVUSDT'),
    ('ARB',    'ARBUSDT'),
    ('OP',     'OPUSDT'),
    ('GALA',   'GALAUSDT'),
    ('DYDX',   'DYDXUSDT'),
    ('CAKE',   'CAKEUSDT'),
]

CFG = GridConfig(
    entry_fib_1=0.500, tp_fib_1=0.236,
    entry_fib_2=0.618, tp_fib_2=0.382,
    sl_fib=1.000, basket_tp=0.382, risk_per_order=10.0,
)

def macd_ind(series, fast=12, slow=26, signal=9):
    ml = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    sig = ml.ewm(span=signal, adjust=False).mean()
    return ml, sig

def run_test():
    days = 180
    print(f'=== Запуск бэктеста: ТОП-30 монет | 1h | {days} дней (6 месяцев) ===')
    print('Конфигурация: Входы 0.500/0.618, Тейки 0.236/0.382, Стоп 1.000, Риск $10/ордер')
    print('Поиск импульсов: min_pct=2.0%, max_bars=6, allow_internal=True\n')

    results = []

    for idx, (name, symbol) in enumerate(COINS_30, 1):
        try:
            df = fetch_ohlcv(symbol, timeframe='1h', days=days)
        except Exception as e:
            print(f'[{idx:02d}/30] {name:6s} | ОШИБКА загрузки: {e}')
            continue

        n_bars = len(df)
        all_impulses = detect_impulses(df, min_pct=2.0, max_bars=6, max_lookback=35, allow_internal=True)

        ml, sig = macd_ind(df['close'])
        df['macd_line'] = ml
        df['macd_signal'] = sig

        macd_impulses = []
        for imp in all_impulses:
            end_i = imp.end_idx
            if end_i < len(df):
                row = df.iloc[end_i]
                if imp.is_long and row['macd_line'] > row['macd_signal']:
                    macd_impulses.append(imp)
                elif (not imp.is_long) and row['macd_line'] < row['macd_signal']:
                    macd_impulses.append(imp)

        trades_base = simulate_grid(df, all_impulses, CFG, log_scale=True)
        sum_base = summarize(trades_base)

        trades_macd = simulate_grid(df, macd_impulses, CFG, log_scale=True)
        sum_macd = summarize(trades_macd)

        pnl_base = sum_base.get('total_pnl', 0.0)
        pnl_macd = sum_macd.get('total_pnl', 0.0)
        wr_base  = sum_base.get('win_rate', 0.0)
        wr_macd  = sum_macd.get('win_rate', 0.0)
        tr_base  = sum_base.get('total_trades', 0)
        tr_macd  = sum_macd.get('total_trades', 0)
        sl_base  = sum_base.get('sl_count', 0)
        sl_macd  = sum_macd.get('sl_count', 0)

        delta_pnl = pnl_macd - pnl_base
        delta_sl  = sl_macd - sl_base

        results.append({
            'name': name,
            'symbol': symbol,
            'bars': n_bars,
            'tr_base': tr_base,
            'wr_base': wr_base,
            'sl_base': sl_base,
            'pnl_base': pnl_base,
            'tr_macd': tr_macd,
            'wr_macd': wr_macd,
            'sl_macd': sl_macd,
            'pnl_macd': pnl_macd,
            'delta_pnl': delta_pnl,
            'delta_sl': delta_sl,
        })

        status_flag = '🟢 +' if delta_pnl >= 0 else '🔴 '
        print(f'[{idx:02d}/30] {name:6s} ({n_bars} св) | Base: {tr_base:3d} сд, WR {wr_base:5.1f}%, SL {sl_base:2d}, PnL ${pnl_base:7.2f} '
              f'| MACD: {tr_macd:3d} сд, WR {wr_macd:5.1f}%, SL {sl_macd:2d}, PnL ${pnl_macd:7.2f} | dPnL: {status_flag}${delta_pnl:6.2f}')

    res_df = pd.DataFrame(results)

    tot_tr_base = res_df['tr_base'].sum()
    tot_tr_macd = res_df['tr_macd'].sum()
    tot_sl_base = res_df['sl_base'].sum()
    tot_sl_macd = res_df['sl_macd'].sum()
    tot_pnl_base = res_df['pnl_base'].sum()
    tot_pnl_macd = res_df['pnl_macd'].sum()
    avg_wr_base = (res_df['tr_base'] * res_df['wr_base']).sum() / tot_tr_base if tot_tr_base else 0.0
    avg_wr_macd = (res_df['tr_macd'] * res_df['wr_macd']).sum() / tot_tr_macd if tot_tr_macd else 0.0

    print('\n' + '='*92)
    print('ИТОГИ БЭКТЕСТА ЗА 6 МЕСЯЦЕВ (ТОП-30 МОНЕТ)')
    print('='*92)
    print(f'Всего монет протестировано: {len(res_df)}')
    print(f'{"Параметр":<30} | {"Без фильтра (Base)":<20} | {"С фильтром MACD":<20} | {"Разница (Дельта)":<15}')
    print('-' * 92)
    delta_pnl_str = ('+$' if tot_pnl_macd >= tot_pnl_base else '-$') + str(abs(round(tot_pnl_macd - tot_pnl_base, 2)))
    print(f'{"Общий PnL ($)":<30} | ${tot_pnl_base:<19.2f} | ${tot_pnl_macd:<19.2f} | {delta_pnl_str:<15}')
    print(f'{"Количество сделок":<30} | {tot_tr_base:<20d} | {tot_tr_macd:<20d} | {tot_tr_macd - tot_tr_base:<15d}')
    print(f'{"Количество Стоп-Лоссов":<30} | {tot_sl_base:<20d} | {tot_sl_macd:<20d} | {tot_sl_macd - tot_sl_base:<15d} (срезано стопов)')
    print(f'{"Средневзвешенный Win Rate":<30} | {avg_wr_base:<19.1f}% | {avg_wr_macd:<19.1f}% | {avg_wr_macd - avg_wr_base:+<14.1f}%')
    print('='*92)

if __name__ == '__main__':
    run_test()
