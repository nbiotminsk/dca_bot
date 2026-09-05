#!/usr/bin/env python3
"""
Сравнение фильтров для двухордерной сетки (корзина 0.382).
Фильтры: без фильтра | EMA34/50 | SuperTrend(10,3) | CCI-14 | CCI-50 | MACD(12,26,9)
Монеты: ТОП-9 | 1h | 365 дней | импульс >= 2% | риск $10/ордер
"""
import warnings
warnings.filterwarnings('ignore')
import sys
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from volatility_calc.data_fetcher import fetch_ohlcv
from scripts.backtest_strategy_interactive import detect_impulses
from scripts.strategy_engine import GridConfig, simulate_grid, summarize

COINS = [
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

def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def supertrend(df, period=10, mult=3.0):
    hl2 = (df['high'] + df['low']) / 2.0
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low']  - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    upper_raw = hl2 + mult * atr
    lower_raw = hl2 - mult * atr
    upper = upper_raw.copy()
    lower = lower_raw.copy()
    bull = [True] * len(df)
    for i in range(1, len(df)):
        upper.iloc[i] = upper_raw.iloc[i] if upper_raw.iloc[i] < upper.iloc[i-1] or df['close'].iloc[i-1] > upper.iloc[i-1] else upper.iloc[i-1]
        lower.iloc[i] = lower_raw.iloc[i] if lower_raw.iloc[i] > lower.iloc[i-1] or df['close'].iloc[i-1] < lower.iloc[i-1] else lower.iloc[i-1]
        prev_bull = bull[i-1]
        if prev_bull:
            bull[i] = df['close'].iloc[i] >= lower.iloc[i]
        else:
            bull[i] = df['close'].iloc[i] > upper.iloc[i]
    return pd.Series(bull, index=df.index)

def cci(df, period):
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    ma = tp.rolling(period).mean()
    md = tp.rolling(period).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
    return (tp - ma) / (0.015 * md)

def macd_ind(series, fast=12, slow=26, signal=9):
    ml = series.ewm(span=fast, adjust=False).mean() - series.ewm(span=slow, adjust=False).mean()
    return ml, ml.ewm(span=signal, adjust=False).mean()

def build_indicators(df):
    d = df.copy()
    d['ema34'] = ema(d['close'], 34)
    d['ema50'] = ema(d['close'], 50)
    d['ema_bull'] = d['ema34'] > d['ema50']
    d['st_bull'] = supertrend(d, 10, 3.0)
    d['cci14'] = cci(d, 14)
    d['cci50'] = cci(d, 50)
    d['macd_line'], d['macd_signal'] = macd_ind(d['close'])
    return d

def filter_impulses(impulses, df_ind, filter_name):
    if filter_name == 'none':
        return impulses
    out = []
    for imp in impulses:
        idx = imp.end_idx
        if idx >= len(df_ind):
            continue
        row = df_ind.iloc[idx]
        is_long = imp.is_long
        if filter_name == 'ema':
            ok = bool(row['ema_bull']) if is_long else not bool(row['ema_bull'])
        elif filter_name == 'st':
            ok = bool(row['st_bull']) if is_long else not bool(row['st_bull'])
        elif filter_name == 'cci14':
            ok = row['cci14'] > -100 if is_long else row['cci14'] < 100
        elif filter_name == 'cci50':
            ok = row['cci50'] > -100 if is_long else row['cci50'] < 100
        elif filter_name == 'macd':
            ok = row['macd_line'] > row['macd_signal'] if is_long else row['macd_line'] < row['macd_signal']
        else:
            ok = True
        if ok:
            out.append(imp)
    return out

FILTERS = ['none', 'ema', 'st', 'cci14', 'cci50', 'macd']
FILTER_LABELS = {
    'none':  'БЕЗ ФИЛЬТРА',
    'ema':   'EMA 34/50',
    'st':    'SuperTrend(10,3)',
    'cci14': 'CCI-14',
    'cci50': 'CCI-50',
    'macd':  'MACD(12,26,9)',
}

def main():
    print('Загрузка данных и расчет...', flush=True)
    data, raw_imp, inds = {}, {}, {}
    for idx, (name, sym) in enumerate(COINS, 1):
        try:
            df = fetch_ohlcv(sym, timeframe='1h', days=180, use_cache=True)
            imps = detect_impulses(df, min_pct=2.0, side='both', scale='log', allow_internal=True)
            data[name] = df
            raw_imp[name] = imps
            inds[name] = build_indicators(df)
            print(f'  [{idx:02d}/30] {name:<6} {len(df)} свечей, {len(imps)} импульсов', flush=True)
        except Exception as e:
            print(f'  [{idx:02d}/30] {name:<6} ERR: {e}', flush=True)

    print('\nСимуляция сделок по фильтрам...', flush=True)
    results = {f: {} for f in FILTERS}
    trades_by_filter = {f: {} for f in FILTERS}
    for fname in FILTERS:
        for name in data:
            filtered = filter_impulses(raw_imp[name], inds[name], fname)
            trades = simulate_grid(data[name], filtered, CFG)
            results[fname][name] = summarize(trades)
            trades_by_filter[fname][name] = trades

    names_ok = [n for n, _ in COINS if n in data]

    print('=' * 110)
    print(f'  ФИЛЬТРЫ | Корзина 0.382 | {len(names_ok)} монет | 1h | 180 дней (6 месяцев) | риск $10/ордер')
    print('=' * 110)
    print(f'{"Фильтр":<18} | {"Сделок":>7} | {"SL":>4} | {"WR %":>6} | {"PnL ($)":>10} | {"$/мес":>8} | {"vs Base":>10}')
    print('-' * 80)

    baseline_pnl = None
    for fname in FILTERS:
        label = FILTER_LABELS[fname]
        tot_n = sum(results[fname].get(n, {}).get('n', 0) for n in names_ok)
        tot_w = sum(results[fname].get(n, {}).get('wins', 0) for n in names_ok)
        tot_sl = sum(results[fname].get(n, {}).get('sl_count', 0) for n in names_ok)
        tot_pnl = sum(results[fname].get(n, {}).get('pnl', 0.0) for n in names_ok)
        wr = (tot_w / tot_n * 100) if tot_n else 0.0
        per_m = tot_pnl / 6.0 # 6 месяцев

        if baseline_pnl is None:
            baseline_pnl = tot_pnl
            vs = '  (base)'
        else:
            diff_p = tot_pnl - baseline_pnl
            vs = f'{diff_p:>+8.0f}$'
        print(f'{label:<18} | {tot_n:>7d} | {tot_sl:>4d} | {wr:>5.1f}% | {tot_pnl:>+9.2f}$ | {per_m:>+7.2f}$ | {vs:>10}')

    print('=' * 110)
    print('\nДЕТАЛЬНАЯ ТАБЛИЦА ПО КАЖДОЙ ИЗ МОНЕТ (БЕЗ ФИЛЬТРА vs MACD):')
    print('-' * 110)
    print(f'{"#":<3} {"Монета":<7} | {"Base сд":>7} {"WR%":>5} {"SL":>3} {"Base PnL":>10} | {"MACD сд":>7} {"WR%":>5} {"SL":>3} {"MACD PnL":>10} | {"Дельта PnL":>11} {"Срез SL":>7}')
    print('-' * 110)

    for i, name in enumerate(names_ok, 1):
        b = results['none'].get(name, {})
        m = results['macd'].get(name, {})
        b_pnl = b.get('pnl', 0.0)
        m_pnl = m.get('pnl', 0.0)
        d_pnl = m_pnl - b_pnl
        b_sl = b.get('sl_count', 0)
        m_sl = m.get('sl_count', 0)
        d_sl = b_sl - m_sl # сколько срезано
        flag = "🟢" if d_pnl >= 0 else "🔴"
        print(f'{i:<3d} {name:<7} | {b.get("n", 0):>7d} {b.get("wr", 0):>5.1f} {b_sl:>3d} {b_pnl:>+9.2f}$ | {m.get("n", 0):>7d} {m.get("wr", 0):>5.1f} {m_sl:>3d} {m_pnl:>+9.2f}$ | {flag} {d_pnl:>+8.2f}$ {d_sl:>+7d}')

    print('=' * 110)

    # Return structures for external reporting
    return data, results, trades_by_filter, names_ok

if __name__ == '__main__':
    data, results, trades_by_filter, names_ok = main()

    # Additional reporting: MACD histogram at stop‑loss exits (for MACD filter)
    from indicators.macd import MACDIndicator
    macd_indicator = MACDIndicator()
    print("\n[MACD] Гистограмма при стоп‑лоссе (для фильтра MACD):")
    for name in names_ok:
        trades = trades_by_filter.get('macd', {}).get(name, [])
        if not trades:
            continue
        df = data[name]
        macd_df = macd_indicator.calculate(df)
        for idx, tr in enumerate(trades, 1):
            if "SL" in tr.outcome:
                e_idx = tr.exit_idx
                if e_idx is not None and 0 <= e_idx < len(macd_df):
                    hist = macd_df['hist'].iloc[e_idx]
                    exit_ts = str(df.iloc[e_idx]['timestamp'])[:16]
                    print(f"{name:5} | Trade #{idx:3} | Exit: {exit_ts} (idx {e_idx:4}) | Hist {hist:+.4f} | Outcome: {tr.outcome}")
