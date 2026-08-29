<?php
/**
 * Live Web Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * Монеты: HYPEUSDT, NEARUSDT, UNIUSDT
 * Автоматическое обновление данных без перезагрузки страницы каждые 10 секунд.
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
    $last_valid = null;
    $active_start = null; $active_low = null; $active_high = null; $active_end = null; $is_active = false;

    for ($i = 0; $i < $n - 1; $i++) {
        $cur = $candles[$i]; $next = $candles[$i + 1];
        if ($next['high'] > $cur['high'] && !$is_active) {
            $active_start = $i; $active_low = $cur['low']; $active_high = $next['high']; $active_end = $i + 1; $is_active = true;
        }
        if ($is_active) {
            $f05 = calcFibLongLog($active_high, $active_low, 0.500);
            if ($cur['low'] <= $f05 && $i > $active_start) {
                $is_active = false;
                $pct = ($active_high - $active_low) / $active_low * 100.0;
                if ($pct >= $min_pct && $active_end > $active_start) {
                    $last_valid = ['start_time' => $candles[$active_start]['time'], 'end_time' => $candles[$active_end]['time'], 'high' => $active_high, 'low' => $active_low, 'pct' => $pct];
                }
            } else {
                if ($cur['high'] > $active_high) { $active_high = $cur['high']; $active_end = $i; }
            }
        }
    }
    if ($is_active && $active_high && $active_low) {
        $pct = ($active_high - $active_low) / $active_low * 100.0;
        if ($pct >= $min_pct) {
            $last_valid = ['start_time' => $candles[$active_start]['time'], 'end_time' => $candles[$active_end]['time'], 'high' => $active_high, 'low' => $active_low, 'pct' => $pct, 'is_live' => true];
        }
    }
    return $last_valid;
}

function detectLatestShortImpulse($candles, $min_pct) {
    $n = count($candles);
    $last_valid = null;
    $active_start = null; $active_high = null; $active_low = null; $active_end = null; $is_active = false;

    for ($i = 0; $i < $n - 1; $i++) {
        $cur = $candles[$i]; $next = $candles[$i + 1];
        if ($next['low'] < $cur['low'] && !$is_active) {
            $active_start = $i; $active_high = $cur['high']; $active_low = $next['low']; $active_end = $i + 1; $is_active = true;
        }
        if ($is_active) {
            $f05 = calcFibShortLog($active_high, $active_low, 0.500);
            if ($cur['high'] >= $f05 && $i > $active_start) {
                $is_active = false;
                $pct = ($active_high - $active_low) / $active_high * 100.0;
                if ($pct >= $min_pct && $active_end > $active_start) {
                    $last_valid = ['start_time' => $candles[$active_start]['time'], 'end_time' => $candles[$active_end]['time'], 'high' => $active_high, 'low' => $active_low, 'pct' => $pct];
                }
            } else {
                if ($cur['low'] < $active_low) { $active_low = $cur['low']; $active_end = $i; }
            }
        }
    }
    if ($is_active && $active_high && $active_low) {
        $pct = ($active_high - $active_low) / $active_high * 100.0;
        if ($pct >= $min_pct) {
            $last_valid = ['start_time' => $candles[$active_start]['time'], 'end_time' => $candles[$active_end]['time'], 'high' => $active_high, 'low' => $active_low, 'pct' => $pct, 'is_live' => true];
        }
    }
    return $last_valid;
}

// Если запрос пришел по AJAX для обновления карточек:
if (isset($_GET['ajax'])) {
    header('Content-Type: application/json');
    $data = [];
    foreach ($symbols as $sym) {
        $candles = fetchBybitKlines($sym, '60', 100);
        if (!$candles) continue;
        $curPrice = end($candles)['close'];
        $impLN = detectLatestLongImpulse($candles, $MIN_IMP_NORMAL);
        $impLM = detectLatestLongImpulse($candles, $MIN_IMP_MANIP);
        $impSN = detectLatestShortImpulse($candles, $MIN_IMP_NORMAL);

        $card = ['symbol' => $sym, 'price' => fmt3($curPrice)];

        // Long Normal
        if ($impLN) {
            $in = calcFibLongLog($impLN['high'], $impLN['low'], 0.618);
            $tp = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $sl = calcFibLongLog($impLN['high'], $impLN['low'], 0.764);
            $card['long_normal'] = [
                'entry' => fmt3($in), 'tp' => fmt3($tp), 'sl' => fmt3($sl),
                'pct' => number_format($impLN['pct'], 2),
                'active' => ($curPrice <= $in && $curPrice > $sl)
            ];
        }

        // Short Normal
        if ($impSN) {
            $in = calcFibShortLog($impSN['high'], $impSN['low'], 0.618);
            $tp = calcFibShortLog($impSN['high'], $impSN['low'], 0.500);
            $sl = calcFibShortLog($impSN['high'], $impSN['low'], 0.764);
            $card['short_normal'] = [
                'entry' => fmt3($in), 'tp' => fmt3($tp), 'sl' => fmt3($sl),
                'pct' => number_format($impSN['pct'], 2),
                'active' => ($curPrice >= $in && $curPrice < $sl)
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
                'active' => ($curPrice <= $m1)
            ];
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
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg); color: var(--text); padding: 24px; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }
        .header h1 { font-size: 22px; font-weight: 700; display: flex; align-items: center; gap: 10px; }
        .badge-live { display: inline-flex; align-items: center; gap: 6px; background: rgba(0,230,118,0.15); color: var(--green); padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; }
        .badge-live::before { content: ""; width: 8px; height: 8px; background: var(--green); border-radius: 50%; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.2); } 100% { opacity: 1; transform: scale(1); } }
        
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; }
        .coin-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); transition: transform 0.2s ease; }
        .coin-card:hover { transform: translateY(-2px); }
        .coin-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 16px; }
        .coin-title { font-size: 20px; font-weight: 800; }
        .coin-price { font-size: 22px; font-weight: 800; color: #fff; font-family: monospace; }

        .block { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 12px; }
        .block-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 10px; display: flex; justify-content: space-between; }
        
        .table-levels { width: 100%; border-collapse: collapse; font-size: 13px; font-family: monospace; }
        .table-levels td { padding: 4px 0; }
        .table-levels td:last-child { text-align: right; font-weight: 700; }
        .lbl { color: var(--text-dim); }

        .status-pill { display: block; text-align: center; padding: 6px; border-radius: 6px; font-size: 12px; font-weight: 700; margin-top: 8px; }
        .status-ready { background: rgba(0,230,118,0.2); color: var(--green); border: 1px solid var(--green); }
        .status-wait { background: rgba(255,255,255,0.05); color: var(--text-dim); }

        .c-green { color: var(--green); }
        .c-purple { color: var(--purple); }
        .c-orange { color: var(--orange); }
        .c-red { color: var(--red); }
        .c-blue { color: var(--blue); }
    </style>
</head>
<body>

<div class="header">
    <h1>📡 Mon 1H Strategy Terminal</h1>
    <div style="display:flex; align-items:center; gap:16px;">
        <span id="update-time" style="color:var(--text-dim); font-size:13px;">Обновление...</span>
        <span class="badge-live">LIVE BYBIT</span>
    </div>
</div>

<div class="grid" id="coins-container">
    <div style="color:var(--text-dim); font-size:16px;">Загрузка котировок и импульсов...</div>
</div>

<script>
async function updateScreener() {
    try {
        const res = await fetch('?ajax=1');
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

                <!-- 1. LONG NORMAL -->
                <div class="block" style="border-left: 3px solid var(--blue);">
                    <div class="block-title c-blue">🟢 LONG ОБЫЧНЫЙ (0.618 → 0.500) <span style="font-size:11px; color:var(--text-dim);">≥ 3.5%</span></div>
                    ${c.long_normal ? `
                    <table class="table-levels">
                        <tr><td class="lbl">Вход (0.618)</td><td class="c-blue">${c.long_normal.entry} $</td></tr>
                        <tr><td class="lbl">Тейк (0.500)</td><td class="c-green">${c.long_normal.tp} $</td></tr>
                        <tr><td class="lbl">Стоп (0.764)</td><td class="c-red">${c.long_normal.sl} $</td></tr>
                    </table>
                    <div class="status-pill ${c.long_normal.active ? 'status-ready' : 'status-wait'}">
                        ${c.long_normal.active ? '🟢 ВХОД В LONG ПРЯМО СЕЙЧАС' : '⏳ Ожидание отката'}
                    </div>
                    ` : '<div style="color:var(--text-dim); font-size:12px;">Нет активного импульса ≥ 3.5%</div>'}
                </div>

                <!-- 2. SHORT NORMAL -->
                <div class="block" style="border-left: 3px solid var(--red);">
                    <div class="block-title c-red">🔴 SHORT ОБЫЧНЫЙ (0.618 → 0.500) <span style="font-size:11px; color:var(--text-dim);">≥ 3.5%</span></div>
                    ${c.short_normal ? `
                    <table class="table-levels">
                        <tr><td class="lbl">Вход (0.618)</td><td class="c-red">${c.short_normal.entry} $</td></tr>
                        <tr><td class="lbl">Тейк (0.500)</td><td class="c-green">${c.short_normal.tp} $</td></tr>
                        <tr><td class="lbl">Стоп (0.764)</td><td class="c-red">${c.short_normal.sl} $</td></tr>
                    </table>
                    <div class="status-pill ${c.short_normal.active ? 'status-ready' : 'status-wait'}">
                        ${c.short_normal.active ? '🔴 ВХОД В SHORT ПРЯМО СЕЙЧАС' : '⏳ Ожидание отскока вверх'}
                    </div>
                    ` : '<div style="color:var(--text-dim); font-size:12px;">Нет дамп-импульса ≥ 3.5%</div>'}
                </div>

                <!-- 3. LONG MANIPULATION -->
                <div class="block" style="border-left: 3px solid var(--purple);">
                    <div class="block-title c-purple">🟣 МАНИПУЛЯЦИЯ (1.618 + 2.0 DCA) <span style="font-size:11px; color:var(--text-dim);">≥ 1.0%</span></div>
                    ${c.long_manip ? `
                    <table class="table-levels">
                        <tr><td class="lbl">Вход (1.618) 1 лот</td><td class="c-purple">${c.long_manip.entry_1} $</td></tr>
                        <tr><td class="lbl">Добор (2.000) 2 лота</td><td class="c-orange">${c.long_manip.entry_2} $</td></tr>
                        <tr><td class="lbl">Тейк-1 (0.618)</td><td class="c-green">${c.long_manip.tp_1} $</td></tr>
                        <tr><td class="lbl">Тейк-2 (0.500)</td><td class="c-green">${c.long_manip.tp_2} $</td></tr>
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
    }
}

updateScreener();
setInterval(updateScreener, 5000); // Автообновление каждые 5 секунд
</script>

</body>
</html>
