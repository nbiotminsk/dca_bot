<?php
/**
 * Live Web Screener & Signal Scanner с Риск-Калькулятором Позиций
 * Монеты: HYPEUSDT, NEARUSDT, UNIUSDT
 * Включает:
 * 1. Обычный Long/Short (Сетка 0.500 / 0.618 со стопом 0.710)
 * 2. Манипуляцию Long (1.618 1x + 2.000 2x DCA с оптимальным стопом 2.618 Fib)
 * 3. Полный расчет риска в $ и количества монет под депозит/плечо
 */

error_reporting(E_ALL & ~E_DEPRECATED);
date_default_timezone_set('Europe/Moscow'); // UTC+3

$symbols = ['HYPEUSDT', 'NEARUSDT', 'UNIUSDT', 'SUIUSDT'];
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

        $card = ['symbol' => $sym, 'price' => fmt3($curPrice), 'raw_price' => $curPrice];

        $long_time = $impLN ? $impLN['end_time'] : 0;
        $short_time = $impSN ? $impSN['end_time'] : 0;

        // Long Normal
        if ($impLN) {
            $in050  = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $in0618 = calcFibLongLog($impLN['high'], $impLN['low'], 0.618);
            $tp0500 = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $tp0382 = calcFibLongLog($impLN['high'], $impLN['low'], 0.382);
            $sl0710 = calcFibLongLog($impLN['high'], $impLN['low'], 0.710);

            $card['long_normal'] = [
                'entry_050'    => fmt3($in050),
                'raw_e050'     => (float)$in050,
                'entry_0618'   => fmt3($in0618),
                'raw_e0618'    => (float)$in0618,
                'tp_0500'      => fmt3($tp0500),
                'raw_tp0500'   => (float)$tp0500,
                'tp_0382'      => fmt3($tp0382),
                'raw_tp0382'   => (float)$tp0382,
                'sl'           => fmt3($sl0710),
                'raw_sl'       => (float)$sl0710,
                'pct'          => number_format($impLN['pct'], 2),
                'active'       => ($curPrice <= $in050 && $curPrice > $sl0710),
                'time'         => date('d.m H:i', (int)($impLN['end_time'] / 1000)),
                'is_fresher'   => ($long_time >= $short_time)
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
                'entry_050'    => fmt3($in050),
                'raw_e050'     => (float)$in050,
                'entry_0618'   => fmt3($in0618),
                'raw_e0618'    => (float)$in0618,
                'tp_0500'      => fmt3($tp0500),
                'raw_tp0500'   => (float)$tp0500,
                'tp_0382'      => fmt3($tp0382),
                'raw_tp0382'   => (float)$tp0382,
                'sl'           => fmt3($sl0710),
                'raw_sl'       => (float)$sl0710,
                'pct'          => number_format($impSN['pct'], 2),
                'active'       => ($curPrice >= $in050 && $curPrice < $sl0710),
                'time'         => date('d.m H:i', (int)($impSN['end_time'] / 1000)),
                'is_fresher'   => ($short_time > $long_time)
            ];
        }

        // Long Manip (Индивидуальный оптимальный стоп и R:R под характер каждой монеты на 2-летней истории)
        $coinManipConfig = [
            'UNIUSDT'  => ['sl_fib' => 2.395, 'rr' => '1:2.4'],
            'NEARUSDT' => ['sl_fib' => 2.395, 'rr' => '1:2.4'],
            'HYPEUSDT' => ['sl_fib' => 2.291, 'rr' => '1:3.0'],
            'SUIUSDT'  => ['sl_fib' => 2.291, 'rr' => '1:3.0']
        ];
        $sl_fib_opt = isset($coinManipConfig[$sym]) ? $coinManipConfig[$sym]['sl_fib'] : 2.500;
        $rr_label_opt = isset($coinManipConfig[$sym]) ? $coinManipConfig[$sym]['rr'] : '1:2.0';

        if ($impLM) {
            $m1 = calcFibLongLog($impLM['high'], $impLM['low'], 1.618);
            $m2 = calcFibLongLog($impLM['high'], $impLM['low'], 2.000);
            $tp1 = calcFibLongLog($impLM['high'], $impLM['low'], 0.618);
            $tp2 = calcFibLongLog($impLM['high'], $impLM['low'], 0.500);
            $sl_opt = calcFibLongLog($impLM['high'], $impLM['low'], $sl_fib_opt);

            $card['long_manip'] = [
                'entry_1'      => fmt3($m1),
                'raw_e1'       => (float)$m1,
                'entry_2'      => fmt3($m2),
                'raw_e2'       => (float)$m2,
                'tp_1'         => fmt3($tp1),
                'raw_tp1'      => (float)$tp1,
                'tp_2'         => fmt3($tp2),
                'raw_tp2'      => (float)$tp2,
                'sl'           => fmt3($sl_opt),
                'raw_sl'       => (float)$sl_opt,
                'sl_fib'       => $sl_fib_opt,
                'rr_label'     => $rr_label_opt,
                'pct'          => number_format($impLM['pct'], 2),
                'active'       => ($curPrice <= $m1 && $curPrice > $sl_opt),
                'time'         => date('d.m H:i', (int)($impLM['end_time'] / 1000))
            ];
        }

        // Авто-вердикт и скоринг близости ко входу (для умной сортировки)
        $priorityScore = 999.0; // Чем меньше число, тем выше в списке

        // 1. Проверяем активные входы прямо сейчас
        if (isset($card['long_manip']) && $card['long_manip']['active']) {
            $card['best_choice'] = "🟣 ВХОД В МАНИПУЛЯЦИЮ ПРЯМО СЕЙЧАС (Приоритет 1)";
            $priorityScore = 0.0;
        } elseif (isset($card['long_normal']) && $card['long_normal']['active'] && $card['long_normal']['is_fresher']) {
            $card['best_choice'] = "🟢 ВХОД В LONG ПРЯМО СЕЙЧАС (Свежий тренд роста)";
            $priorityScore = 0.1;
        } elseif (isset($card['short_normal']) && $card['short_normal']['active'] && $card['short_normal']['is_fresher']) {
            $card['best_choice'] = "🔴 ВХОД В SHORT ПРЯМО СЕЙЧАС (Свежий тренд падения)";
            $priorityScore = 0.2;
        } else {
            // 2. Если входа прямо сейчас нет — считаем минимальную дистанцию (%) до ближайшего входа
            $minDist = 999.0;
            $nearestDesc = "";

            if (isset($card['long_normal']) && $card['long_normal']['is_fresher']) {
                $d = abs($curPrice - $card['long_normal']['raw_e050']) / $curPrice * 100.0;
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🟢 До Long 0.500: " . number_format($d, 2) . "%"; }
            }
            if (isset($card['short_normal']) && $card['short_normal']['is_fresher']) {
                $d = abs($curPrice - $card['short_normal']['raw_e050']) / $curPrice * 100.0;
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🔴 До Short 0.500: " . number_format($d, 2) . "%"; }
            }
            if (isset($card['long_manip'])) {
                $d = abs($curPrice - $card['long_manip']['raw_e1']) / $curPrice * 100.0;
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🟣 До Манипуляции 1.618: " . number_format($d, 2) . "%"; }
            }

            if ($minDist < 990.0) {
                $priorityScore = 1.0 + $minDist; // Приоритет по близости в %
                $card['best_choice'] = "⏳ ОЖИДАНИЕ ВХОДА ({$nearestDesc})";
            } else {
                $priorityScore = 999.0;
                $card['best_choice'] = "💤 НЕТ АКТИВНЫХ ВОЛН (Вне позиции)";
            }
        }

        $card['priority_score'] = $priorityScore;
        $data[] = $card;
    }

    // Сортировка: сначала те, где есть вход прямо сейчас (score 0), затем самые близкие по % дистанции
    usort($data, function($a, $b) {
        if ($a['priority_score'] == $b['priority_score']) return 0;
        return ($a['priority_score'] < $b['priority_score']) ? -1 : 1;
    });

    echo json_encode(['time' => date('H:i:s'), 'items' => $data]);
    exit;
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Mon 1H Terminal & Risk Calculator</title>
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
            --yellow: #ffd600;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: var(--bg); color: var(--text); padding: 16px; }
        
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid var(--border); flex-wrap: wrap; gap: 10px; }
        .header h1 { font-size: 18px; font-weight: 800; display: flex; align-items: center; gap: 8px; }
        
        .header-actions { display: flex; align-items: center; gap: 8px; }
        .btn-refresh { 
            display: inline-flex; align-items: center; gap: 6px; 
            background: #252836; color: #fff; border: 1px solid #3b4054; 
            padding: 8px 14px; border-radius: 8px; font-size: 13px; font-weight: 700; 
            cursor: pointer; transition: all 0.2s ease; 
        }
        .btn-refresh:hover { background: #32374a; border-color: var(--blue); }
        .btn-refresh:active { transform: scale(0.96); }
        .btn-refresh.loading svg { animation: spin 0.8s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }

        .badge-live { display: inline-flex; align-items: center; gap: 6px; background: rgba(0,230,118,0.15); color: var(--green); padding: 6px 10px; border-radius: 8px; font-size: 12px; font-weight: 700; }
        .badge-live::before { content: ""; width: 8px; height: 8px; background: var(--green); border-radius: 50%; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0% { opacity: 1; transform: scale(1); } 50% { opacity: 0.4; transform: scale(1.2); } 100% { opacity: 1; transform: scale(1); } }

        /* Панель Калькулятора */
        .calc-panel {
            background: #1a1d26;
            border: 1px solid #2e3447;
            border-radius: 12px;
            padding: 14px 16px;
            margin-bottom: 20px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.25);
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 12px;
        }
        .calc-title { font-size: 13px; font-weight: 800; text-transform: uppercase; color: var(--yellow); letter-spacing: 0.5px; display: flex; align-items: center; gap: 6px; width: 100%; }
        .calc-inputs { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; width: 100%; }
        .calc-field { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 85px; }
        .calc-field label { font-size: 10px; font-weight: 700; color: var(--text-dim); text-transform: uppercase; }
        .calc-input-wrap { display: flex; align-items: center; background: #101218; border: 1px solid var(--border); border-radius: 6px; padding: 6px 8px; }
        .calc-input-wrap input { background: transparent; border: none; color: #fff; font-family: monospace; font-size: 15px; font-weight: 700; width: 100%; outline: none; }
        .calc-input-wrap span { color: var(--text-dim); font-size: 12px; font-weight: 700; margin-left: 2px; }
        .calc-info-badge { width: 100%; background: rgba(255,214,0,0.12); border: 1px solid rgba(255,214,0,0.3); color: var(--yellow); padding: 8px 12px; border-radius: 8px; font-size: 12px; font-weight: 700; font-family: monospace; text-align: center; margin-top: 4px; }
        
        .grid { 
            display: flex;
            flex-direction: column;
            gap: 20px;
            max-width: 1400px;
            margin: 0 auto;
        }
        .coin-card { 
            background: var(--card-bg); 
            border: 1px solid var(--border); 
            border-radius: 14px; 
            padding: 18px 20px; 
            box-shadow: 0 4px 20px rgba(0,0,0,0.3); 
        }
        .coin-header { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
        .coin-title { font-size: 22px; font-weight: 800; }
        .coin-price { font-size: 24px; font-weight: 800; color: #fff; font-family: monospace; }

        .coin-blocks-row {
            display: flex;
            gap: 14px;
            flex-wrap: wrap;
        }
        .coin-blocks-row .block {
            flex: 1 1 320px;
            min-width: 300px;
        }
        @media (max-width: 768px) {
            .coin-blocks-row { flex-direction: column; }
            .coin-blocks-row .block { flex: 1 1 100%; min-width: 0; }
        }

        .verdict-box { background: rgba(255,255,255,0.06); border-radius: 8px; padding: 8px 10px; margin-bottom: 14px; text-align: center; font-weight: 800; font-size: 13px; border: 1px solid var(--border); }

        .block { background: rgba(255,255,255,0.03); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 0; display: flex; flex-direction: column; justify-content: space-between; }
        .block-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: flex; justify-content: space-between; }
        
        .table-levels { width: 100%; border-collapse: collapse; font-size: 14.5px; font-family: monospace; }
        .table-levels td { padding: 5px 0; vertical-align: middle; }
        .table-levels td:last-child { text-align: right; font-weight: 800; font-size: 15px; }
        .lbl { color: var(--text-dim); font-size: 13px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-weight: 600; }
        
        .profit-payout-box {
            background: rgba(0, 0, 0, 0.28);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 8px;
            padding: 9px 12px;
            margin-top: 10px;
            font-family: monospace;
            font-size: 12.5px;
        }
        .profit-payout-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 4px 0;
        }
        .payout-val-green { color: var(--green); font-weight: 800; font-size: 13.5px; }
        .payout-val-cyan { color: var(--cyan); font-weight: 800; font-size: 13.5px; }
        .payout-val-red { color: var(--red); font-weight: 800; font-size: 13.5px; }

        .entry-val-box {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 3px;
        }
        .price-num { font-size: 15px; font-weight: 800; }
        .coins-badge-row { display: flex; align-items: center; gap: 5px; }
        .coins-tag {
            display: inline-block;
            background: #ffd600;
            color: #000;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 900;
            letter-spacing: -0.2px;
        }
        .margin-subtext { color: var(--text-dim); font-size: 11.5px; font-weight: 700; }

        .status-pill { display: block; text-align: center; padding: 7px; border-radius: 6px; font-size: 12px; font-weight: 800; margin-top: 10px; }
        .status-ready { background: rgba(0,230,118,0.2); color: var(--green); border: 1px solid var(--green); }
        .status-wait { background: rgba(255,255,255,0.05); color: var(--text-dim); }

        .c-green { color: var(--green); }
        .c-purple { color: var(--purple); }
        .c-orange { color: var(--orange); }
        .c-red { color: var(--red); }
        .c-blue { color: var(--blue); }
        .c-cyan { color: var(--cyan); }
        .c-yellow { color: var(--yellow); }
    </style>
</head>
<body>

<div class="header">
    <h1>📡 Mon 1H Strategy Terminal</h1>
    <div class="header-actions">
        <span id="update-time" style="color:var(--text-dim); font-size:12px;">Обновление...</span>
        <button class="btn-refresh" id="refresh-btn" type="button">
            <svg id="refresh-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
            </svg>
            Обновить
        </button>
        <span class="badge-live">LIVE</span>
    </div>
</div>

<!-- 🧮 ПАНЕЛЬ РИСК-КАЛЬКУЛЯТОРА -->
<div class="calc-panel">
    <div class="calc-title">
        <span>🧮 Риск-Калькулятор Позиций</span>
    </div>
    <div class="calc-inputs">
        <div class="calc-field">
            <label>Депозит</label>
            <div class="calc-input-wrap">
                <input type="number" id="cfg-deposit" value="1000" min="10" step="50" oninput="saveAndRecalc()">
                <span>$</span>
            </div>
        </div>
        <div class="calc-field">
            <label>Риск на Стоп</label>
            <div class="calc-input-wrap">
                <input type="number" id="cfg-risk" value="2.0" min="0.1" max="50" step="0.5" oninput="saveAndRecalc()">
                <span>%</span>
            </div>
        </div>
        <div class="calc-field">
            <label>Плечо</label>
            <div class="calc-input-wrap">
                <input type="number" id="cfg-leverage" value="1" min="1" max="50" step="1" oninput="saveAndRecalc()">
                <span>x</span>
            </div>
        </div>
    </div>
    <div class="calc-info-badge" id="calc-summary">
        Макс. риск на сетку: $20.00
    </div>
</div>

<div class="grid" id="coins-container">
    <div style="color:var(--text-dim); font-size:16px;">Загрузка котировок и импульсов...</div>
</div>

<script>
let isRefreshing = false;
let globalData = null;

function loadSavedSettings() {
    const dep = localStorage.getItem('mon1h_deposit');
    const rsk = localStorage.getItem('mon1h_risk');
    const lev = localStorage.getItem('mon1h_leverage');
    if (dep) document.getElementById('cfg-deposit').value = dep;
    if (rsk) document.getElementById('cfg-risk').value = rsk;
    if (lev) document.getElementById('cfg-leverage').value = lev;
}

function saveAndRecalc() {
    const dep = parseFloat(document.getElementById('cfg-deposit').value) || 1000;
    const rsk = parseFloat(document.getElementById('cfg-risk').value) || 2.0;
    const lev = parseFloat(document.getElementById('cfg-leverage').value) || 1;

    localStorage.setItem('mon1h_deposit', dep);
    localStorage.setItem('mon1h_risk', rsk);
    localStorage.setItem('mon1h_leverage', lev);

    const maxRiskDollar = (dep * (rsk / 100)).toFixed(2);
    document.getElementById('calc-summary').innerText = `Макс. риск на сетку: $${maxRiskDollar} (${rsk}%)`;

    if (globalData) {
        renderCards(globalData);
    }
}

function fmtCoinQty(qty) {
    if (qty >= 100) return qty.toFixed(1);
    if (qty >= 10) return qty.toFixed(2);
    return qty.toFixed(3);
}

// Расчет СЕТКИ DCA (Вход 1 [1 доля] + Вход 2 [2 доли]), где СУММАРНЫЙ убыток на стопе = maxRiskDollar
function calculateDcaGrid(e1, e2, sl, isShort = false) {
    const dep = parseFloat(document.getElementById('cfg-deposit').value) || 1000;
    const rsk = parseFloat(document.getElementById('cfg-risk').value) || 2.0;
    const lev = parseFloat(document.getElementById('cfg-leverage').value) || 1;
    const maxRiskDollar = dep * (rsk / 100.0);

    if (!e1 || !e2 || !sl) return null;

    const d1 = isShort ? (sl - e1) : (e1 - sl);
    const d2 = isShort ? (sl - e2) : (e2 - sl);

    if (d1 <= 0.0001 || d2 <= 0.0001) return null;

    const q1 = maxRiskDollar / (d1 + 2 * d2);
    const q2 = 2 * q1;

    const pos1Usd = q1 * e1;
    const pos2Usd = q2 * e2;

    const margin1Usd = pos1Usd / lev;
    const margin2Usd = pos2Usd / lev;

    const stopPct = (Math.abs(e1 - sl) / e1 * 100).toFixed(2);

    return {
        q1: q1,
        q2: q2,
        q_total: (q1 + q2),
        q1_fmt: fmtCoinQty(q1),
        q2_fmt: fmtCoinQty(q2),
        margin1: margin1Usd.toFixed(1),
        margin2: margin2Usd.toFixed(1),
        pos1_usd: pos1Usd,
        pos2_usd: pos2Usd,
        stop_pct: stopPct,
        loss_if_only_1: (q1 * d1).toFixed(2),
        loss_total: maxRiskDollar.toFixed(2)
    };
}

function renderCards(data) {
    if (!data || !data.items) return;
    let html = '';
    data.items.forEach(c => {
        const coinTicker = c.symbol.replace('USDT', '');

        // Расчет сетки DCA для Long Normal
        let ln_grid = null;
        let ln_pnl_only1_to_382 = "0.00";
        let ln_pnl_both_to_500 = "0.00";
        let ln_pnl_split_50_382 = "0.00";
        let ln_pnl_both_to_382 = "0.00";

        if (c.long_normal) {
            ln_grid = calculateDcaGrid(c.long_normal.raw_e050, c.long_normal.raw_e0618, c.long_normal.raw_sl, false);
            if (ln_grid) {
                ln_pnl_only1_to_382 = (ln_grid.q1 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e050)).toFixed(2);
                const pnl2_to_500 = ln_grid.q2 * (c.long_normal.raw_tp0500 - c.long_normal.raw_e0618);
                ln_pnl_both_to_500 = pnl2_to_500.toFixed(2);

                const pnl1_to_382 = ln_grid.q1 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e050);
                const pnl2_to_382 = ln_grid.q2 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e0618);
                const full_pnl_382 = pnl1_to_382 + pnl2_to_382;

                const split_pnl = (0.50 * pnl2_to_500) + (0.50 * full_pnl_382);
                ln_pnl_split_50_382 = split_pnl.toFixed(2);
                ln_pnl_both_to_382 = full_pnl_382.toFixed(2);
            }
        }

        // Расчет сетки DCA для Short Normal
        let sn_grid = null;
        let sn_pnl_only1_to_382 = "0.00";
        let sn_pnl_both_to_500 = "0.00";
        let sn_pnl_split_50_382 = "0.00";
        let sn_pnl_both_to_382 = "0.00";

        if (c.short_normal) {
            sn_grid = calculateDcaGrid(c.short_normal.raw_e050, c.short_normal.raw_e0618, c.short_normal.raw_sl, true);
            if (sn_grid) {
                sn_pnl_only1_to_382 = (sn_grid.q1 * (c.short_normal.raw_e050 - c.short_normal.raw_tp0382)).toFixed(2);
                const pnl2_to_500 = sn_grid.q2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0500);
                sn_pnl_both_to_500 = pnl2_to_500.toFixed(2);

                const pnl1_to_382 = sn_grid.q1 * (c.short_normal.raw_e050 - c.short_normal.raw_tp0382);
                const pnl2_to_382 = sn_grid.q2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0382);
                const full_pnl_382 = pnl1_to_382 + pnl2_to_382;

                const split_pnl = (0.50 * pnl2_to_500) + (0.50 * full_pnl_382);
                sn_pnl_split_50_382 = split_pnl.toFixed(2);
                sn_pnl_both_to_382 = full_pnl_382.toFixed(2);
            }
        }

        // Расчет для Long Manip (Сетка 1.618 1x + 2.000 2x со стопом 2.618 Fib)
        let lm_grid = null;
        let lm_pnl_only1_tp1 = "0.00";
        let lm_pnl_only1_tp2 = "0.00";
        let lm_pnl_both_tp1 = "0.00";
        let lm_pnl_both_tp2 = "0.00";

        if (c.long_manip) {
            lm_grid = calculateDcaGrid(c.long_manip.raw_e1, c.long_manip.raw_e2, c.long_manip.raw_sl, false);
            if (lm_grid) {
                // 1. Только Вход-1 (1.618) -> Тейк-1 (0.618)
                lm_pnl_only1_tp1 = (lm_grid.q1 * (c.long_manip.raw_tp1 - c.long_manip.raw_e1)).toFixed(2);

                // 2. Только Вход-1 (1.618) -> Тейк-2 (0.500)
                lm_pnl_only1_tp2 = (lm_grid.q1 * (c.long_manip.raw_tp2 - c.long_manip.raw_e1)).toFixed(2);

                // 3. Оба входа -> Тейк-1 (0.618)
                const pnl1_tp1 = lm_grid.q1 * (c.long_manip.raw_tp1 - c.long_manip.raw_e1);
                const pnl2_tp1 = lm_grid.q2 * (c.long_manip.raw_tp1 - c.long_manip.raw_e2);
                lm_pnl_both_tp1 = (pnl1_tp1 + pnl2_tp1).toFixed(2);

                // 4. Оба входа -> Тейк-2 (0.500)
                const pnl1_tp2 = lm_grid.q1 * (c.long_manip.raw_tp2 - c.long_manip.raw_e1);
                const pnl2_tp2 = lm_grid.q2 * (c.long_manip.raw_tp2 - c.long_manip.raw_e2);
                lm_pnl_both_tp2 = (pnl1_tp2 + pnl2_tp2).toFixed(2);
            }
        }

        html += `
        <div class="coin-card">
            <div class="coin-header">
                <div class="coin-title">${coinTicker}<span style="color:var(--text-dim); font-size:13px;"> / USDT</span></div>
                <div class="coin-price">${c.price} $</div>
            </div>

            <div class="verdict-box">👉 РЕШЕНИЕ: ${c.best_choice}</div>
            
            <div class="coin-blocks-row">
            ${c.long_normal && ln_grid ? `
            <!-- 1. LONG NORMAL -->
            <div class="block" style="border-left: 3px solid var(--blue); opacity: ${!c.long_normal.is_fresher ? '0.6' : '1.0'};">
                <div class="block-title c-blue">
                    🟢 LONG ОБЫЧНЫЙ (Сетка 0.500 / 0.618) 
                    <span style="font-size:10px; color:var(--text-dim);">${c.long_normal.time}</span>
                </div>
                <table class="table-levels">
                    <tr>
                        <td class="lbl">🔹 Вход-1 (0.500 Fib) 1x</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-cyan">${c.long_normal.entry_050} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${ln_grid.q1_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${ln_grid.margin1})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td class="lbl">🔹 Вход-2 / DCA (0.618 Fib) 2x</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-blue">${c.long_normal.entry_0618} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${ln_grid.q2_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${ln_grid.margin2})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr><td class="lbl">🎯 Тейк-1 (0.500 Fib)</td><td class="c-green">${c.long_normal.tp_0500} $</td></tr>
                    <tr><td class="lbl">🎯 Тейк-2 (0.382 Fib)</td><td class="c-green">${c.long_normal.tp_0382} $</td></tr>
                    <tr><td class="lbl">🛑 Стоп (0.710 Fib)</td><td class="c-red">${c.long_normal.sl} $ <span style="font-size:10px; color:var(--text-dim);">(-${ln_grid.stop_pct}%)</span></td></tr>
                </table>

                <div class="profit-payout-box">
                    <div class="profit-payout-row">
                        <span class="lbl">💰 [1] Только Вход-1 → 0.382:</span>
                        <span class="payout-val-green">+$${ln_pnl_only1_to_382}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">💰 [2] ОБА входа → 100% на 0.500:</span>
                        <span class="payout-val-green">+$${ln_pnl_both_to_500}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">⭐ [3] Сплит (50% на 0.5 + 50% на 0.382):</span>
                        <span class="payout-val-cyan">+$${ln_pnl_split_50_382}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🚀 [4] ОБА входа → 100% на 0.382:</span>
                        <span class="payout-val-green">+$${ln_pnl_both_to_382}</span>
                    </div>
                    <div class="profit-payout-row" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:3px; padding-top:3px;">
                        <span class="lbl">🛑 Стоп (если только Вход-1):</span>
                        <span class="payout-val-red">-$${ln_grid.loss_if_only_1}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🛑 Стоп (если ОБА входа 1+2):</span>
                        <span class="payout-val-red">-$${ln_grid.loss_total}</span>
                    </div>
                </div>

                <div class="status-pill ${c.long_normal.active ? 'status-ready' : 'status-wait'}">
                    ${c.long_normal.active ? (c.long_normal.is_fresher ? '🟢 ВХОД В LONG (Актуальный импульс)' : '⚠️ Старый импульс') : '⏳ Ожидание отката'}
                </div>
            </div>
            ` : ''}

            ${c.short_normal && sn_grid ? `
            <!-- 2. SHORT NORMAL -->
            <div class="block" style="border-left: 3px solid var(--red); opacity: ${!c.short_normal.is_fresher ? '0.6' : '1.0'};">
                <div class="block-title c-red">
                    🔴 SHORT ОБЫЧНЫЙ (Сетка 0.500 / 0.618)
                    <span style="font-size:10px; color:var(--text-dim);">${c.short_normal.time}</span>
                </div>
                <table class="table-levels">
                    <tr>
                        <td class="lbl">🔹 Вход-1 в Short (0.500)</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-orange">${c.short_normal.entry_050} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${sn_grid.q1_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${sn_grid.margin1})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td class="lbl">🔹 Вход-2 в Short (0.618)</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-red">${c.short_normal.entry_0618} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${sn_grid.q2_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${sn_grid.margin2})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr><td class="lbl">🎯 Тейк-1 (0.500 Fib)</td><td class="c-green">${c.short_normal.tp_0500} $</td></tr>
                    <tr><td class="lbl">🎯 Тейк-2 (0.382 Fib)</td><td class="c-green">${c.short_normal.tp_0382} $</td></tr>
                    <tr><td class="lbl">🛑 Стоп (0.710 Fib)</td><td class="c-red">${c.short_normal.sl} $ <span style="font-size:10px; color:var(--text-dim);">(-${sn_grid.stop_pct}%)</span></td></tr>
                </table>

                <div class="profit-payout-box">
                    <div class="profit-payout-row">
                        <span class="lbl">💰 [1] Только Вход-1 → 0.382:</span>
                        <span class="payout-val-green">+$${sn_pnl_only1_to_382}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">💰 [2] ОБА входа → 100% на 0.500:</span>
                        <span class="payout-val-green">+$${sn_pnl_both_to_500}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">⭐ [3] Сплит (50% на 0.5 + 50% на 0.382):</span>
                        <span class="payout-val-cyan">+$${sn_pnl_split_50_382}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🚀 [4] ОБА входа → 100% на 0.382:</span>
                        <span class="payout-val-green">+$${sn_pnl_both_to_382}</span>
                    </div>
                    <div class="profit-payout-row" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:3px; padding-top:3px;">
                        <span class="lbl">🛑 Стоп (если только Вход-1):</span>
                        <span class="payout-val-red">-$${sn_grid.loss_if_only_1}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🛑 Стоп (если ОБА входа 1+2):</span>
                        <span class="payout-val-red">-$${sn_grid.loss_total}</span>
                    </div>
                </div>

                <div class="status-pill ${c.short_normal.active ? 'status-ready' : 'status-wait'}">
                    ${c.short_normal.active ? (c.short_normal.is_fresher ? '🔴 ВХОД В SHORT (Актуальный дамп)' : '⚠️ Старый дамп') : '⏳ Ожидание отскока вверх'}
                </div>
            </div>
            ` : ''}

            ${c.long_manip && lm_grid ? `
            <!-- 3. LONG MANIPULATION -->
            <div class="block" style="border-left: 3px solid var(--purple);">
                <div class="block-title c-purple">
                    🟣 МАНИПУЛЯЦИЯ (1.618 + 2.0 DCA)
                    <span style="font-size:10px; color:var(--text-dim);">${c.long_manip.time}</span>
                </div>
                <table class="table-levels">
                    <tr>
                        <td class="lbl">🔹 Вход-1 (1.618) 1x</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-purple">${c.long_manip.entry_1} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${lm_grid.q1_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${lm_grid.margin1})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td class="lbl">🔹 Добор-2 (2.000) 2x</td>
                        <td>
                            <div class="entry-val-box">
                                <span class="price-num c-orange">${c.long_manip.entry_2} $</span>
                                <div class="coins-badge-row">
                                    <span class="coins-tag">${lm_grid.q2_fmt} ${coinTicker}</span>
                                    <span class="margin-subtext">($${lm_grid.margin2})</span>
                                </div>
                            </div>
                        </td>
                    </tr>
                    <tr><td class="lbl">🎯 Тейк-1 (0.618 Fib)</td><td class="c-green">${c.long_manip.tp_1} $</td></tr>
                    <tr><td class="lbl">🎯 Тейк-2 (0.500 Fib)</td><td class="c-green">${c.long_manip.tp_2} $</td></tr>
                    <tr><td class="lbl">🛑 Стоп (${c.long_manip.sl_fib} Fib) [R:R ${c.long_manip.rr_label}]</td><td class="c-red">${c.long_manip.sl} $ <span style="font-size:10px; color:var(--text-dim);">(-${lm_grid.stop_pct}%)</span></td></tr>
                </table>

                <div class="profit-payout-box">
                    <div class="profit-payout-row">
                        <span class="lbl">💰 Тейк-1 (только Вход-1 → 0.618):</span>
                        <span class="payout-val-green">+$${lm_pnl_only1_tp1}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">💰 Тейк-2 (только Вход-1 → 0.500):</span>
                        <span class="payout-val-cyan">+$${lm_pnl_only1_tp2}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🚀 Тейк-1 (ОБА входа → 0.618) [R:R ${c.long_manip.rr_label}]:</span>
                        <span class="payout-val-green">+$${lm_pnl_both_tp1}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🔥 Тейк-2 (ОБА входа → 0.500):</span>
                        <span class="payout-val-green">+$${lm_pnl_both_tp2}</span>
                    </div>
                    <div class="profit-payout-row" style="border-top:1px solid rgba(255,255,255,0.08); margin-top:3px; padding-top:3px;">
                        <span class="lbl">🛑 Стоп (если только Вход-1):</span>
                        <span class="payout-val-red">-$${lm_grid.loss_if_only_1}</span>
                    </div>
                    <div class="profit-payout-row">
                        <span class="lbl">🛑 Стоп (если ОБА входа 1+2) [R:R ${c.long_manip.rr_label}]:</span>
                        <span class="payout-val-red">-$${lm_grid.loss_total}</span>
                    </div>
                </div>

                <div class="status-pill ${c.long_manip.active ? 'status-ready' : 'status-wait'}">
                    ${c.long_manip.active ? '🟣 ВХОД В МАНИПУЛЯЦИЮ ПРЯМО СЕЙЧАС' : '⏳ Ожидание уровня 1.618'}
                </div>
            </div>
            ` : ''}

            ${(!c.long_normal && !c.short_normal && !c.long_manip) ? `
            <div style="grid-column: 1 / -1; background:rgba(255,255,255,0.02); border:1px dashed var(--border); border-radius:8px; padding:20px; text-align:center; color:var(--text-dim); font-size:13px;">
                💤 Нет активных импульсов (цена во флэте)
            </div>
            ` : ''}

            </div> <!-- .coin-blocks-row -->
        </div>
        `;
    });
    document.getElementById('coins-container').innerHTML = html;
}

async function updateScreener() {
    if (isRefreshing) return;
    isRefreshing = true;
    const btn = document.getElementById('refresh-btn');
    if (btn) btn.classList.add('loading');

    try {
        const currentUrl = window.location.pathname;
        const res = await fetch(currentUrl + '?ajax=1&t=' + new Date().getTime());
        globalData = await res.json();
        document.getElementById('update-time').innerText = 'UTC+3: ' + globalData.time;
        renderCards(globalData);
    } catch (e) {
        console.error("Ошибка загрузки данных", e);
    } finally {
        isRefreshing = false;
        if (btn) btn.classList.remove('loading');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadSavedSettings();
    saveAndRecalc();

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
