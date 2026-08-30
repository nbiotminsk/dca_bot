<?php
/**
 * Live Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * Монеты: HYPEUSDT, NEARUSDT, UNIUSDT
 * Автоматическое определение ДОМИНИРУЮЩЕГО СИГНАЛА с ручной кнопкой обновления.
 * 
 * В LONG ОБЫЧНЫЙ добавлены:
 * - Вход 1 (0.500 Fib) + Вход 2 / Добор (0.618 Fib)
 * - Тейк 1 (0.500 Fib) + Тейк 2 (0.382 Fib)
 */

error_reporting(E_ALL & ~E_DEPRECATED);
date_default_timezone_set('Europe/Moscow'); // UTC+3

$symbols = ['HYPEUSDT', 'NEARUSDT', 'UNIUSDT'];
$MIN_IMP_MANIP  = 1.0;
$MIN_IMP_NORMAL = 3.5;

function fetchBybitKlines($symbol, $interval = '60', $limit = 100) {
    $url = "https://api.bybit.com/v5/market/kline?category=linear&symbol={$symbol}&interval={$interval}&limit={$limit}";
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 8);
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    $response = curl_exec($ch);

    if (!$response) return null;
    $json = json_decode($response, true);
    if (!isset($json['result']['list']) || empty($json['result']['list'])) return null;

    $rawList = array_reverse($json['result']['list']);
    $candles = [];
    foreach ($rawList as $k) {
        $candles[] = [
            'time'  => (int)$k[0],
            'open'  => (float)$k[1],
            'high'  => (float)$k[2],
            'low'   => (float)$k[3],
            'close' => (float)$k[4],
        ];
    }
    return $candles;
}

function calcFibLongLog($high, $low, $level) {
    if ($high <= 0 || $low <= 0) return 0;
    return exp(log($high) - $level * (log($high) - log($low)));
}

function calcFibShortLog($high, $low, $level) {
    if ($high <= 0 || $low <= 0) return 0;
    return exp(log($low) + $level * (log($high) - log($low)));
}

function fmt3($val) {
    return number_format((float)$val, 3, '.', '');
}

function detectLatestLongImpulse($candles, $min_pct) {
    $n = count($candles);
    $best_impulse = null;

    for ($i = 1; $i < min(72, $n - 1); $i++) {
        $start_idx = $n - 1 - $i;
        $imp_low   = $candles[$start_idx]['low'];
        $imp_high  = $candles[$start_idx + 1]['high'];
        
        if ($candles[$start_idx + 1]['high'] <= $candles[$start_idx]['high']) {
            continue;
        }

        $broken = false;
        $max_high = $imp_high;
        $max_idx  = $start_idx + 1;

        for ($k = $start_idx + 1; $k < $n; $k++) {
            $cur_05 = calcFibLongLog($max_high, $imp_low, 0.500);
            if ($candles[$k]['low'] <= $cur_05) {
                $broken = true;
                break;
            }
            if ($candles[$k]['high'] > $max_high) {
                $max_high = $candles[$k]['high'];
                $max_idx  = $k;
            }
        }

        if (!$broken) {
            $pct = ($max_high - $imp_low) / $imp_low * 100.0;
            if ($pct >= $min_pct) {
                if ($best_impulse === null || $pct > $best_impulse['pct']) {
                    $best_impulse = [
                        'start_time' => $candles[$start_idx]['time'],
                        'end_time'   => $candles[$max_idx]['time'],
                        'end_idx'    => $max_idx,
                        'high'       => $max_high,
                        'low'        => $imp_low,
                        'pct'        => $pct,
                        'is_live'    => true
                    ];
                }
            }
        }
    }

    return $best_impulse;
}

function detectLatestShortImpulse($candles, $min_pct) {
    $n = count($candles);
    $best_impulse = null;

    for ($i = 1; $i < min(72, $n - 1); $i++) {
        $start_idx = $n - 1 - $i;
        $imp_high  = $candles[$start_idx]['high'];
        $imp_low   = $candles[$start_idx + 1]['low'];
        
        if ($candles[$start_idx + 1]['low'] >= $candles[$start_idx]['low']) {
            continue;
        }

        $broken = false;
        $min_low = $imp_low;
        $min_idx = $start_idx + 1;

        for ($k = $start_idx + 1; $k < $n; $k++) {
            $cur_05 = calcFibShortLog($imp_high, $min_low, 0.500);
            if ($candles[$k]['high'] >= $cur_05) {
                $broken = true;
                break;
            }
            if ($candles[$k]['low'] < $min_low) {
                $min_low = $candles[$k]['low'];
                $min_idx = $k;
            }
        }

        if (!$broken) {
            $pct = ($imp_high - $min_low) / $imp_high * 100.0;
            if ($pct >= $min_pct) {
                if ($best_impulse === null || $pct > $best_impulse['pct']) {
                    $best_impulse = [
                        'start_time' => $candles[$start_idx]['time'],
                        'end_time'   => $candles[$min_idx]['time'],
                        'end_idx'    => $min_idx,
                        'high'       => $imp_high,
                        'low'        => $min_low,
                        'pct'        => $pct,
                        'is_live'    => true
                    ];
                }
            }
        }
    }

    return $best_impulse;
}

if (isset($_GET['ajax'])) {
    header('Content-Type: application/json');
    header('Cache-Control: no-cache, no-store, must-revalidate');
    header('Pragma: no-cache');
    header('Expires: 0');

    $data = [];
    foreach ($symbols as $sym) {
        $candles = fetchBybitKlines($sym, '60', 100);
        if (!$candles) continue;
        $curPrice = end($candles)['close'];
        $impLN = detectLatestLongImpulse($candles, $MIN_IMP_NORMAL);
        $impLM = detectLatestLongImpulse($candles, $MIN_IMP_MANIP);
        $impSN = detectLatestShortImpulse($candles, $MIN_IMP_NORMAL);

        $card = ['symbol' => $sym, 'price' => fmt3($curPrice)];

        $long_time = $impLN ? $impLN['end_time'] : 0;
        $short_time = $impSN ? $impSN['end_time'] : 0;

        // Long Normal с двумя входами (0.500 и 0.618) и двумя тейками (0.500 и 0.382)
        if ($impLN) {
            $in050  = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $in0618 = calcFibLongLog($impLN['high'], $impLN['low'], 0.618);
            $tp0500 = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $tp0382 = calcFibLongLog($impLN['high'], $impLN['low'], 0.382);
            $sl0710 = calcFibLongLog($impLN['high'], $impLN['low'], 0.710);

            $card['long_normal'] = [
                'entry_050'  => fmt3($in050),
                'entry_0618' => fmt3($in0618),
                'tp_0500'    => fmt3($tp0500),
                'tp_0382'    => fmt3($tp0382),
                'sl'         => fmt3($sl0710),
                'pct'        => number_format($impLN['pct'], 2),
                'active'     => ($curPrice <= $in050 && $curPrice > $sl0710),
                'time'       => date('d.m H:i', (int)($impLN['end_time'] / 1000)),
                'is_fresher' => ($long_time >= $short_time)
            ];
        }

        // Short Normal
        if ($impSN) {
            $in050  = calcFibShortLog($impSN['high'], $impSN['low'], 0.500);
            $in0618 = calcFibShortLog($impSN['high'], $impSN['low'], 0.618);
            $tp0500 = calcFibShortLog($impSN['high'], $impSN['low'], 0.500);
            $tp0382 = calcFibShortLog($impSN['high'], $impSN['low'], 0.382);
            $sl0710 = calcFibShortLog($impSN['high'], $impSN['low'], 0.710);

            $card['short_normal'] = [
                'entry_050'  => fmt3($in050),
                'entry_0618' => fmt3($in0618),
                'tp_0500'    => fmt3($tp0500),
                'tp_0382'    => fmt3($tp0382),
                'sl'         => fmt3($sl0710),
                'pct'        => number_format($impSN['pct'], 2),
                'active'     => ($curPrice >= $in050 && $curPrice < $sl0710),
                'time'       => date('d.m H:i', (int)($impSN['end_time'] / 1000)),
                'is_fresher' => ($short_time > $long_time)
            ];
        }

        // Long Manip
        if ($impLM) {
            $m1 = calcFibLongLog($impLM['high'], $impLM['low'], 1.618);
            $m2 = calcFibLongLog($impLM['high'], $impLM['low'], 2.000);
            $tp1 = calcFibLongLog($impLM['high'], $impLM['low'], 0.618);
            $tp2 = calcFibLongLog($impLM['high'], $impLM['low'], 0.500);
            $card['long_manip'] = [
                'entry_1' => fmt3($m1), 'entry_2' => fmt3($m2),
                'tp_1' => fmt3($tp1), 'tp_2' => fmt3($tp2),
                'pct' => number_format($impLM['pct'], 2),
                'active' => ($curPrice <= $m1),
                'time' => date('d.m H:i', (int)($impLM['end_time'] / 1000))
            ];
        }

        // Авто-вердикт главного приоритета
        if (isset($card['long_manip']) && $card['long_manip']['active']) {
            $card['best_choice'] = "🟣 ВХОД В МАНИПУЛЯЦИЮ (Приоритет 1)";
        } elseif (isset($card['long_normal']) && $card['long_normal']['active'] && $card['long_normal']['is_fresher']) {
            $card['best_choice'] = "🟢 ВХОД В LONG (Свежий тренд роста)";
        } elseif (isset($card['short_normal']) && $card['short_normal']['active'] && $card['short_normal']['is_fresher']) {
            $card['best_choice'] = "🔴 ВХОД В SHORT (Свежий тренд падения)";
        } else {
            $card['best_choice'] = "⏳ ВНЕ ПОЗИЦИИ (Ждем подхода к лимиткам)";
        }

        $data[] = $card;
    }
    echo json_encode(['time' => date('H:i:s'), 'items' => $data]);
    exit;
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mon 1H Terminal — Live Screener</title>
    <style>
        :root {
            --bg: #0d0e12;
            --card-bg: #16181f;
            --border: #232733;
            --text: #f0f2f5;
            --text-dim: #8b949e;
            --green: #00e676;
            --purple: #d500f9;
            --orange: #ff9100;
            --red: #ff5252;
            --blue: #2979ff;
            --cyan: #00e5ff;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg); color: var(--text); padding: 24px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
        .header h1 { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        
        .header-actions { display: flex; align-items: center; gap: 12px; }
        .btn-refresh { 
            display: inline-flex; align-items: center; gap: 8px; 
            background: #252836; color: #fff; border: 1px solid #3b4054; 
            padding: 8px 16px; border-radius: 8px; font-size: 14px; font-weight: 700; 
            cursor: pointer; transition: all 0.2s ease; 
        }
        .btn-refresh:hover { background: #32374a; border-color: var(--blue); }
        .btn-refresh:active { transform: scale(0.96); }
        .btn-refresh.loading svg { animation: spin 0.8s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }

        .badge-live { display: inline-flex; align-items: center; gap: 6px; background: rgba(0,230,118,0.15); color: var(--green); padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 700; }
        .badge-live::before { content: ""; width: 8px; height: 8px; background: var(--green); border-radius: 50%; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.2); } 100% { opacity: 1; transform: scale(1); } }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; }
        .coin-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
        .coin-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
        .coin-title { font-size: 20px; font-weight: 800; }
        .coin-price { font-size: 22px; font-weight: 800; color: #fff; font-family: monospace; }

        .verdict-box { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 10px; margin-bottom: 16px; text-align: center; font-weight: 800; font-size: 14px; border: 1px solid var(--border); }

        .block { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
        .block-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: flex; justify-content: space-between; }
        
        .table-levels { width: 100%; border-collapse: collapse; font-size: 13px; font-family: monospace; }
        .table-levels td { padding: 3px 0; }
        .table-levels td:last-child { text-align: right; font-weight: 700; }
        .lbl { color: var(--text-dim); }

        .status-pill { display: block; text-align: center; padding: 5px; border-radius: 6px; font-size: 11px; font-weight: 700; margin-top: 8px; }
        .status-ready { background: rgba(0,230,118,0.2); color: var(--green); border: 1px solid var(--green); }
        .status-wait { background: rgba(255,255,255,0.05); color: var(--text-dim); }

        .c-green { color: var(--green); }
        .c-purple { color: var(--purple); }
        .c-orange { color: var(--orange); }
        .c-red { color: var(--red); }
        .c-blue { color: var(--blue); }
        .c-cyan { color: var(--cyan); }
    </style>
</head>
<body>

<div class="header">
    <h1>📡 Mon 1H Strategy Terminal</h1>
    <div class="header-actions">
        <span id="update-time" style="color:var(--text-dim); font-size:13px;">Обновление...</span>
        <button class="btn-refresh" id="refresh-btn" type="button">
            <svg id="refresh-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
            </svg>
            Обновить
        </button>
        <span class="badge-live">LIVE BYBIT</span>
    </div>
</div>

<div class="grid" id="coins-container">
    <div style="color:var(--text-dim); font-size:16px;">Загрузка котировок и импульсов...</div>
</div>

<script>
let isRefreshing = false;

async function updateScreener() {
    if (isRefreshing) return;
    isRefreshing = true;
    const btn = document.getElementById('refresh-btn');
    if (btn) btn.classList.add('loading');

    try {
        const currentUrl = window.location.pathname;
        const res = await fetch(currentUrl + '?ajax=1&t=' + new Date().getTime());
        const data = await res.json();
        document.getElementById('update-time').innerText = 'UTC+3: ' + data.time;
        
        let html = '';
        data.items.forEach(c => {
            html += `
            <div class="coin-card">
                <div class="coin-header">
                    <div class="coin-title">${c.symbol.replace('USDT', '')}<span style="color:var(--text-dim); font-size:13px;"> / USDT</span></div>
                    <div class="coin-price">${c.price} $</div>
                </div>

                <!-- ГЛАВНЫЙ ВЕРДИКТ -->
                <div class="verdict-box">👉 РЕШЕНИЕ: ${c.best_choice}</div>

                <!-- 1. LONG NORMAL -->
                <div class="block" style="border-left: 3px solid var(--blue); opacity: ${c.long_normal && !c.long_normal.is_fresher ? '0.6' : '1.0'};">
                    <div class="block-title c-blue">
                        🟢 LONG ОБЫЧНЫЙ (Сетка 0.500 / 0.618) 
                        <span style="font-size:10px; color:var(--text-dim);">${c.long_normal ? c.long_normal.time : ''}</span>
                    </div>
                    ${c.long_normal ? `
                    <table class="table-levels">
                        <tr><td class="lbl">🔹 Вход-1 (0.500 Fib) 1x</td><td class="c-cyan">${c.long_normal.entry_050} $</td></tr>
                        <tr><td class="lbl">🔹 Вход-2 / DCA (0.618 Fib) 2x</td><td class="c-blue">${c.long_normal.entry_0618} $</td></tr>
                        <tr><td class="lbl">🎯 Тейк-1 (0.500 Fib)</td><td class="c-green">${c.long_normal.tp_0500} $</td></tr>
                        <tr><td class="lbl">🎯 Тейк-2 (0.382 Fib)</td><td class="c-green">${c.long_normal.tp_0382} $</td></tr>
                        <tr><td class="lbl">🛑 Стоп (0.710 Fib)</td><td class="c-red">${c.long_normal.sl} $</td></tr>
                    </table>
                    <div class="status-pill ${c.long_normal.active ? 'status-ready' : 'status-wait'}">
                        ${c.long_normal.active ? (c.long_normal.is_fresher ? '🟢 ВХОД В LONG (Актуальный импульс)' : '⚠️ Старый импульс') : '⏳ Ожидание отката'}
                    </div>
                    ` : '<div style="color:var(--text-dim); font-size:12px;">Нет импульса ≥ 3.5%</div>'}
                </div>

                <!-- 2. SHORT NORMAL -->
                <div class="block" style="border-left: 3px solid var(--red); opacity: ${c.short_normal && !c.short_normal.is_fresher ? '0.6' : '1.0'};">
                    <div class="block-title c-red">
                        🔴 SHORT ОБЫЧНЫЙ (Сетка 0.500 / 0.618)
                        <span style="font-size:10px; color:var(--text-dim);">${c.short_normal ? c.short_normal.time : ''}</span>
                    </div>
                    ${c.short_normal ? `
                    <table class="table-levels">
                        <tr><td class="lbl">🔹 Вход-1 в Short (0.500)</td><td class="c-orange">${c.short_normal.entry_050} $</td></tr>
                        <tr><td class="lbl">🔹 Вход-2 в Short (0.618)</td><td class="c-red">${c.short_normal.entry_0618} $</td></tr>
                        <tr><td class="lbl">🎯 Тейк-1 (0.500 Fib)</td><td class="c-green">${c.short_normal.tp_0500} $</td></tr>
                        <tr><td class="lbl">🎯 Тейк-2 (0.382 Fib)</td><td class="c-green">${c.short_normal.tp_0382} $</td></tr>
                        <tr><td class="lbl">🛑 Стоп (0.710 Fib)</td><td class="c-red">${c.short_normal.sl} $</td></tr>
                    </table>
                    <div class="status-pill ${c.short_normal.active ? 'status-ready' : 'status-wait'}">
                        ${c.short_normal.active ? (c.short_normal.is_fresher ? '🔴 ВХОД В SHORT (Актуальный дамп)' : '⚠️ Старый дамп') : '⏳ Ожидание отскока вверх'}
                    </div>
                    ` : '<div style="color:var(--text-dim); font-size:12px;">Нет дамп-импульса ≥ 3.5%</div>'}
                </div>

                <!-- 3. LONG MANIPULATION -->
                <div class="block" style="border-left: 3px solid var(--purple);">
                    <div class="block-title c-purple">
                        🟣 МАНИПУЛЯЦИЯ (1.618 + 2.0 DCA)
                        <span style="font-size:10px; color:var(--text-dim);">${c.long_manip ? c.long_manip.time : ''}</span>
                    </div>
                    ${c.long_manip ? `
                    <table class="table-levels">
                        <tr><td class="lbl">Вход (1.618) 1 лот</td><td class="c-purple">${c.long_manip.entry_1} $</td></tr>
                        <tr><td class="lbl">Добор (2.000) 2 лота</td><td class="c-orange">${c.long_manip.entry_2} $</td></tr>
                        <tr><td class="lbl">Тейк-1 (0.618 Fib)</td><td class="c-green">${c.long_manip.tp_1} $</td></tr>
                        <tr><td class="lbl">Тейк-2 (0.500 Fib)</td><td class="c-green">${c.long_manip.tp_2} $</td></tr>
                    </table>
                    <div class="status-pill ${c.long_manip.active ? 'status-ready' : 'status-wait'}">
                        ${c.long_manip.active ? '🟣 ВХОД В МАНИПУЛЯЦИЮ ПРЯМО СЕЙЧАС' : '⏳ Ожидание уровня 1.618'}
                    </div>
                    ` : '<div style="color:var(--text-dim); font-size:12px;">Нет импульса ≥ 1.0%</div>'}
                </div>

            </div>
            `;
        });
        document.getElementById('coins-container').innerHTML = html;
    } catch (e) {
        console.error("Ошибка загрузки данных", e);
    } finally {
        isRefreshing = false;
        if (btn) btn.classList.remove('loading');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('refresh-btn');
    if (btn) {
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            updateScreener();
        });
    }
    updateScreener();
    setInterval(updateScreener, 6000);
});
</script>

</body>
</html>
