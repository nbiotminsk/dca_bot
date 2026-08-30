<?php
/**
 * Live Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * Монеты: HYPEUSDT, NEARUSDT, UNIUSDT
 * Автоматический поиск ПОЛНОЙ МАКРО-ВОЛНЫ импульса от истинного дна.
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

echo "\n========================================================================================\n";
echo "  📡 ПОЛНЫЙ СКАНЕР СИГНАЛОВ: LONG (Обычный + Манипуляция) И SHORT (Обычный) — " . date('d.m.Y H:i') . "\n";
echo "========================================================================================\n\n";

foreach ($symbols as $sym) {
    $candles = fetchBybitKlines($sym, '60', 100);
    if (!$candles) continue;

    $curPrice = end($candles)['close'];
    $impLongNormal  = detectLatestLongImpulse($candles, $MIN_IMP_NORMAL);
    $impLongManip   = detectLatestLongImpulse($candles, $MIN_IMP_MANIP);
    $impShortNormal = detectLatestShortImpulse($candles, $MIN_IMP_NORMAL);

    echo "🪙 МОНЕТА: {$sym} | Текущая цена: " . fmt3($curPrice) . " $\n";
    echo "========================================================================================\n";

    // LONG NORMAL
    if ($impLongNormal) {
        $h = $impLongNormal['high']; $l = $impLongNormal['low'];
        $tp = calcFibLongLog($h, $l, 0.500);
        $in = calcFibLongLog($h, $l, 0.618);
        $sl = calcFibLongLog($h, $l, 0.764);
        $profit_pct = (($tp - $in) / $in) * 100.0;
        $dist = (($curPrice - $in) / $curPrice) * 100.0;

        $status = "";
        if ($curPrice <= $in && $curPrice > $sl) $status = "🟢 ВХОД В LONG СЕЙЧАС!";
        elseif ($curPrice > $in) $status = "⏳ Ожидание отката: осталось " . number_format($dist, 2) . "%";
        else $status = "🛑 Ниже стопа";

        $tS = date('d.m H:i', (int)($impLongNormal['start_time'] / 1000));
        $tE = date('d.m H:i', (int)($impLongNormal['end_time'] / 1000));

        echo "  [1] 🟢 LONG ОБЫЧНЫЙ 0.618 → 0.500 (Импульс ≥ 3.5%):\n";
        echo "      • Волна: {$tS} → {$tE} (Дно: " . fmt3($l) . "$ | Пик: " . fmt3($h) . "$ | Рост: +" . number_format($impLongNormal['pct'], 2) . "%)\n";
        echo "      • 🟢 ВХОД (0.618)      : " . fmt3($in) . " $\n";
        echo "      • 🎯 ТЕЙК (0.500)      : " . fmt3($tp) . " $ (Ход: +" . number_format($profit_pct, 2) . "%)\n";
        echo "      • 🛑 СТОП (0.764)      : " . fmt3($sl) . " $\n";
        echo "      • 👉 Статус            : {$status}\n";
    } else {
        echo "  [1] 🟢 LONG ОБЫЧНЫЙ 0.618 → 0.500 (Импульс ≥ 3.5%):\n      • Нет импульса ≥ 3.5%\n";
    }

    echo "  --------------------------------------------------------------------------------------\n";

    // SHORT NORMAL
    if ($impShortNormal) {
        $h = $impShortNormal['high']; $l = $impShortNormal['low'];
        $tp = calcFibShortLog($h, $l, 0.500);
        $in = calcFibShortLog($h, $l, 0.618);
        $sl = calcFibShortLog($h, $l, 0.764);
        $profit_pct = (($in - $tp) / $in) * 100.0;
        $dist = (($in - $curPrice) / $curPrice) * 100.0;

        $status = "";
        if ($curPrice >= $in && $curPrice < $sl) $status = "🔴 ВХОД В SHORT СЕЙЧАС!";
        elseif ($curPrice < $in) $status = "⏳ Ожидание отскока вверх к 0.618: осталось " . number_format($dist, 2) . "%";
        else $status = "🛑 Выше стопа";

        $tS = date('d.m H:i', (int)($impShortNormal['start_time'] / 1000));
        $tE = date('d.m H:i', (int)($impShortNormal['end_time'] / 1000));

        echo "  [2] 🔴 SHORT ОБЫЧНЫЙ 0.618 → 0.500 (Дамп-импульс ≥ 3.5%):\n";
        echo "      • Волна: {$tS} → {$tE} (Пик: " . fmt3($h) . "$ | Дно: " . fmt3($l) . "$ | Падение: -" . number_format($impShortNormal['pct'], 2) . "%)\n";
        echo "      • 🔴 ВХОД В SHORT (0.618): " . fmt3($in) . " $\n";
        echo "      • 🎯 ТЕЙК (0.500)        : " . fmt3($tp) . " $ (Ход: +" . number_format($profit_pct, 2) . "%)\n";
        echo "      • 🛑 СТОП (0.764)        : " . fmt3($sl) . " $\n";
        echo "      • 👉 Статус              : {$status}\n";
    } else {
        echo "  [2] 🔴 SHORT ОБЫЧНЫЙ 0.618 → 0.500 (Дамп-импульс ≥ 3.5%):\n      • Нет дамп-импульса ≥ 3.5%\n";
    }

    echo "  --------------------------------------------------------------------------------------\n";

    // LONG MANIP
    if ($impLongManip) {
        $h = $impLongManip['high']; $l = $impLongManip['low'];
        $tp0618 = calcFibLongLog($h, $l, 0.618);
        $tp0500 = calcFibLongLog($h, $l, 0.500);
        $m1618  = calcFibLongLog($h, $l, 1.618);
        $m2000  = calcFibLongLog($h, $l, 2.000);

        $profit_tp0618 = (($tp0618 - $m1618) / $m1618) * 100.0;
        $profit_tp0500 = (($tp0500 - $m1618) / $m1618) * 100.0;
        $dist = (($curPrice - $m1618) / $curPrice) * 100.0;

        $status = "";
        if ($curPrice <= $m1618 && $curPrice > $m2000) $status = "🟣 ВХОД В МАНИПУЛЯЦИЮ 1.618 СЕЙЧАС!";
        elseif ($curPrice <= $m2000) $status = "🟠 ДОБОР DCA 2.0 (2 лота) СЕЙЧАС!";
        else $status = "⏳ Ожидание: до уровня 1.618 осталось " . number_format($dist, 2) . "%";

        $tS = date('d.m H:i', (int)($impLongManip['start_time'] / 1000));
        $tE = date('d.m H:i', (int)($impLongManip['end_time'] / 1000));

        echo "  [3] 🟣 LONG МАНИПУЛЯЦИЯ 1.618 + 2.0 DCA (Импульс ≥ 1.0%):\n";
        echo "      • Волна: {$tS} → {$tE} (Дно: " . fmt3($l) . "$ | Пик: " . fmt3($h) . "$ | Рост: +" . number_format($impLongManip['pct'], 2) . "%)\n";
        echo "      • 🟣 ВХОД (1.618) 1 лот : " . fmt3($m1618) . " $\n";
        echo "      • 🟠 ДОБОР (2.000) 2x   : " . fmt3($m2000) . " $\n";
        echo "      • 🎯 ТЕЙК-1 (0.618 Fib) : " . fmt3($tp0618) . " $ (Ход от 1.618: +" . number_format($profit_tp0618, 2) . "%)\n";
        echo "      • 🎯 ТЕЙК-2 (0.500 Fib) : " . fmt3($tp0500) . " $ (Ход от 1.618: +" . number_format($profit_tp0500, 2) . "%)\n";
        echo "      • 👉 Статус             : {$status}\n";
    }
    echo "\n";
}
