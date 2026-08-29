<?php
/**
 * Live Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * 
 * Направления торговли:
 * 1) LONG Обычный (0.618 -> 0.500, SL 0.764) — Импульсы от 3.5%
 * 2) LONG Манипуляция (1.618 + 2.0 DCA -> 0.500) — Импульсы от 1.0%
 * 3) SHORT Обычный (0.618 -> 0.500, SL 0.764) — Импульсы от 3.5% (Без манипуляции)
 * 
 * Формат цен: 3 знака после запятой
 * Запуск: php scan_signals.php
 */

error_reporting(E_ALL & ~E_DEPRECATED);
date_default_timezone_set('Europe/Moscow'); // UTC+3

$symbols = ['HYPEUSDT', 'NEARUSDT', 'UNIUSDT'];

// Пороги импульсов
$MIN_IMP_MANIP  = 1.0; // от 1.0% для Манипуляции LONG
$MIN_IMP_NORMAL = 3.5; // от 3.5% для Обычных входов (LONG и SHORT)

// Функция запроса свечей с биржи Bybit (Linear Futures)
function fetchBybitKlines($symbol, $interval = '60', $limit = 100) {
    $url = "https://api.bybit.com/v5/market/kline?category=linear&symbol={$symbol}&interval={$interval}&limit={$limit}";
    $ch = curl_init();
    curl_setopt($ch, CURLOPT_URL, $url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
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

// Расчет уровней Фибоначчи LONG (Логарифмическая шкала Log)
function calcFibLongLog($high, $low, $level) {
    if ($high <= 0 || $low <= 0) return 0;
    return exp(log($high) - $level * (log($high) - log($low)));
}

// Расчет уровней Фибоначчи SHORT (Логарифмическая шкала Log)
function calcFibShortLog($high, $low, $level) {
    if ($high <= 0 || $low <= 0) return 0;
    return exp(log($low) + $level * (log($high) - log($low)));
}

// Форматирование цен с 3 знаками после запятой
function fmt3($val) {
    return number_format((float)$val, 3, '.', '');
}

// Поиск самого свежего LONG импульса
function detectLatestLongImpulse($candles, $min_pct) {
    $n = count($candles);
    $last_valid = null;
    $active_start = null;
    $active_low   = null;
    $active_high  = null;
    $active_end   = null;
    $is_active    = false;

    for ($i = 0; $i < $n - 1; $i++) {
        $cur  = $candles[$i];
        $next = $candles[$i + 1];

        if ($next['high'] > $cur['high'] && !$is_active) {
            $active_start = $i;
            $active_low   = $cur['low'];
            $active_high  = $next['high'];
            $active_end   = $i + 1;
            $is_active    = true;
        }

        if ($is_active) {
            $f05 = calcFibLongLog($active_high, $active_low, 0.500);
            if ($cur['low'] <= $f05 && $i > $active_start) {
                $is_active = false;
                $pct = ($active_high - $active_low) / $active_low * 100.0;
                if ($pct >= $min_pct && $active_end > $active_start) {
                    $last_valid = [
                        'start_time' => $candles[$active_start]['time'],
                        'end_time'   => $candles[$active_end]['time'],
                        'high'       => $active_high,
                        'low'        => $active_low,
                        'pct'        => $pct,
                    ];
                }
            } else {
                if ($cur['high'] > $active_high) {
                    $active_high = $cur['high'];
                    $active_end  = $i;
                }
            }
        }
    }

    if ($is_active && $active_high && $active_low) {
        $pct = ($active_high - $active_low) / $active_low * 100.0;
        if ($pct >= $min_pct) {
            $last_valid = [
                'start_time' => $candles[$active_start]['time'],
                'end_time'   => $candles[$active_end]['time'],
                'high'       => $active_high,
                'low'        => $active_low,
                'pct'        => $pct,
                'is_live'    => true
            ];
        }
    }
    return $last_valid;
}

// Поиск самого свежего SHORT импульса (Дамп)
function detectLatestShortImpulse($candles, $min_pct) {
    $n = count($candles);
    $last_valid = null;
    $active_start = null;
    $active_high  = null;
    $active_low   = null;
    $active_end   = null;
    $is_active    = false;

    for ($i = 0; $i < $n - 1; $i++) {
        $cur  = $candles[$i];
        $next = $candles[$i + 1];

        if ($next['low'] < $cur['low'] && !$is_active) {
            $active_start = $i;
            $active_high  = $cur['high'];
            $active_low   = $next['low'];
            $active_end   = $i + 1;
            $is_active    = true;
        }

        if ($is_active) {
            $f05 = calcFibShortLog($active_high, $active_low, 0.500);
            if ($cur['high'] >= $f05 && $i > $active_start) {
                $is_active = false;
                $pct = ($active_high - $active_low) / $active_high * 100.0;
                if ($pct >= $min_pct && $active_end > $active_start) {
                    $last_valid = [
                        'start_time' => $candles[$active_start]['time'],
                        'end_time'   => $candles[$active_end]['time'],
                        'high'       => $active_high,
                        'low'        => $active_low,
                        'pct'        => $pct,
                    ];
                }
            } else {
                if ($cur['low'] < $active_low) {
                    $active_low = $cur['low'];
                    $active_end = $i;
                }
            }
        }
    }

    if ($is_active && $active_high && $active_low) {
        $pct = ($active_high - $active_low) / $active_high * 100.0;
        if ($pct >= $min_pct) {
            $last_valid = [
                'start_time' => $candles[$active_start]['time'],
                'end_time'   => $candles[$active_end]['time'],
                'high'       => $active_high,
                'low'        => $active_low,
                'pct'        => $pct,
                'is_live'    => true
            ];
        }
    }
    return $last_valid;
}

$isCli = (php_sapi_name() === 'cli');

if (!$isCli) {
    echo "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Mon 1H Screener (LONG & SHORT)</title>";
    echo "<style>body{font-family:Consolas,monospace;background:#121214;color:#e1e1e6;padding:20px;}";
    echo ".card{background:#1e1e24;border-radius:8px;padding:15px;margin-bottom:20px;border-left:5px solid #00c853;}";
    echo ".val{font-weight:bold;color:#00e5ff;} table{width:100%;border-collapse:collapse;margin-top:10px;}";
    echo "th,td{border:1px solid #333;padding:8px;text-align:left;} th{background:#2a2a35;}";
    echo ".green{color:#00e676;} .purple{color:#d500f9;} .orange{color:#ff9100;} .red{color:#ff5252;}";
    echo "</style></head><body><h1>📡 Полный Сканер Сигналов (LONG & SHORT) — UTC+3</h1>";
} else {
    echo "\n========================================================================================\n";
    echo "  📡 ПОЛНЫЙ СКАНЕР СИГНАЛОВ: LONG (Обычный + Манипуляция) И SHORT (Обычный) — " . date('d.m.Y H:i') . "\n";
    echo "========================================================================================\n\n";
}

foreach ($symbols as $sym) {
    $candles = fetchBybitKlines($sym, '60', 100);
    if (!$candles) {
        echo "Ошибка загрузки данных для {$sym}\n";
        continue;
    }

    $curPrice = end($candles)['close'];

    // 1. Поиск LONG импульсов
    $impLongNormal = detectLatestLongImpulse($candles, $MIN_IMP_NORMAL);
    $impLongManip  = detectLatestLongImpulse($candles, $MIN_IMP_MANIP);

    // 2. Поиск SHORT импульса
    $impShortNormal = detectLatestShortImpulse($candles, $MIN_IMP_NORMAL);

    if ($isCli) {
        echo "🪙 МОНЕТА: {$sym} | Текущая цена: " . fmt3($curPrice) . " $\n";
        echo "========================================================================================\n";

        // ─── [1] 🟢 LONG ОБЫЧНЫЙ (0.618 -> 0.500) ───
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
            echo "  [1] 🟢 LONG ОБЫЧНЫЙ 0.618 → 0.500 (Импульс ≥ 3.5%):\n";
            echo "      • Нет активного импульса ≥ 3.5%\n";
        }

        echo "  --------------------------------------------------------------------------------------\n";

        // ─── [2] 🔴 SHORT ОБЫЧНЫЙ (0.618 -> 0.500) ───
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
            echo "  [2] 🔴 SHORT ОБЫЧНЫЙ 0.618 → 0.500 (Дамп-импульс ≥ 3.5%):\n";
            echo "      • Нет активного дамп-импульса ≥ 3.5%\n";
        }

        echo "  --------------------------------------------------------------------------------------\n";

        // ─── [3] 🟣 LONG МАНИПУЛЯЦИЯ (1.618 / 2.0 DCA) ───
        if ($impLongManip) {
            $h = $impLongManip['high']; $l = $impLongManip['low'];
            $tp = calcFibLongLog($h, $l, 0.500);
            $m1618 = calcFibLongLog($h, $l, 1.618);
            $m2000 = calcFibLongLog($h, $l, 2.000);
            $profit_m = (($tp - $m1618) / $m1618) * 100.0;
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
            echo "      • 🎯 ТЕЙК (0.500)       : " . fmt3($tp) . " $ (Ход: +" . number_format($profit_m, 2) . "%)\n";
            echo "      • 👉 Статус             : {$status}\n";
        }
        echo "\n";
    } else {
        echo "<div class='card'>";
        echo "<h2>🪙 {$sym} — Цена: <span class='val'>" . fmt3($curPrice) . " $</span></h2>";
        
        if ($impLongNormal) {
            $h = $impLongNormal['high']; $l = $impLongNormal['low'];
            $tp = calcFibLongLog($h, $l, 0.500); $in = calcFibLongLog($h, $l, 0.618); $sl = calcFibLongLog($h, $l, 0.764);
            echo "<h3>🟢 LONG Обычный 0.618 → 0.500 (Импульс ≥ 3.5%)</h3>";
            echo "<p>Вход: <b>" . fmt3($in) . "$</b> | Тейк: <b class='green'>" . fmt3($tp) . "$</b> | Стоп: <b class='red'>" . fmt3($sl) . "$</b></p>";
        }

        if ($impShortNormal) {
            $h = $impShortNormal['high']; $l = $impShortNormal['low'];
            $tp = calcFibShortLog($h, $l, 0.500); $in = calcFibShortLog($h, $l, 0.618); $sl = calcFibShortLog($h, $l, 0.764);
            echo "<h3>🔴 SHORT Обычный 0.618 → 0.500 (Дамп-импульс ≥ 3.5%)</h3>";
            echo "<p>Вход в Short: <b>" . fmt3($in) . "$</b> | Тейк: <b class='green'>" . fmt3($tp) . "$</b> | Стоп: <b class='red'>" . fmt3($sl) . "$</b></p>";
        }

        if ($impLongManip) {
            $h = $impLongManip['high']; $l = $impLongManip['low'];
            $tp = calcFibLongLog($h, $l, 0.500); $m1618 = calcFibLongLog($h, $l, 1.618); $m2000 = calcFibLongLog($h, $l, 2.000);
            echo "<h3>🟣 LONG Манипуляция 1.618 + 2.0 DCA (Импульс ≥ 1.0%)</h3>";
            echo "<p>Вход 1.618: <b class='purple'>" . fmt3($m1618) . "$</b> | Добор 2.0: <b class='orange'>" . fmt3($m2000) . "$</b> | Тейк: <b class='green'>" . fmt3($tp) . "$</b></p>";
        }
        echo "</div>";
    }
}

if (!$isCli) {
    echo "</body></html>";
}
