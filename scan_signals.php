<?php
/**
 * Live Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * 
 * Правила фильтрации размаха импульсов:
 * 1) Для Манипуляции (1.618 / 2.0 DCA): импульсы от 1.0%
 * 2) Для Обычного входа (0.618 -> 0.500): крупные импульсы от 3.5% (чтобы комиссия не съедала профит)
 * 
 * Формат цен: 3 знака после запятой
 * Запуск: php scan_signals.php
 */

error_reporting(E_ALL & ~E_DEPRECATED);
date_default_timezone_set('Europe/Moscow'); // UTC+3

$symbols = ['HYPEUSDT', 'NEARUSDT', 'UNIUSDT'];

// Пороги импульсов
$MIN_IMP_MANIP  = 1.0; // от 1.0% для Манипуляции
$MIN_IMP_NORMAL = 3.5; // от 3.5% для Обычного входа (0.618 -> 0.500)

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

// Расчет уровней Фибоначчи (Логарифмическая шкала Log)
function calcFibLog($high, $low, $level) {
    if ($high <= 0 || $low <= 0) return 0;
    return exp(log($high) - $level * (log($high) - log($low)));
}

// Форматирование цен с 3 знаками после запятой
function fmt3($val) {
    return number_format((float)$val, 3, '.', '');
}

// Поиск самого свежего импульса заданной минимальной величины
function detectLatestImpulse($candles, $min_pct) {
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

        // Старт импульса
        if ($next['high'] > $cur['high'] && !$is_active) {
            $active_start = $i;
            $active_low   = $cur['low'];
            $active_high  = $next['high'];
            $active_end   = $i + 1;
            $is_active    = true;
        }

        if ($is_active) {
            $f05 = calcFibLog($active_high, $active_low, 0.500);
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

$isCli = (php_sapi_name() === 'cli');

if (!$isCli) {
    echo "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Mon 1H Screener</title>";
    echo "<style>body{font-family:Consolas,monospace;background:#121214;color:#e1e1e6;padding:20px;}";
    echo ".card{background:#1e1e24;border-radius:8px;padding:15px;margin-bottom:20px;border-left:5px solid #00c853;}";
    echo ".val{font-weight:bold;color:#00e5ff;} table{width:100%;border-collapse:collapse;margin-top:10px;}";
    echo "th,td{border:1px solid #333;padding:8px;text-align:left;} th{background:#2a2a35;}";
    echo ".green{color:#00e676;} .purple{color:#d500f9;} .orange{color:#ff9100;} .red{color:#ff5252;}";
    echo "</style></head><body><h1>📡 Сканер раздельных сигналов (Mon 1H Strategy) — UTC+3</h1>";
} else {
    echo "\n========================================================================================\n";
    echo "  📡 СКАНЕР СИГНАЛОВ: МАНИПУЛЯЦИЯ (от 1.0%) И ОБЫЧНЫЙ ВХОД (от 3.5%) — " . date('d.m.Y H:i') . " (UTC+3)\n";
    echo "========================================================================================\n\n";
}

foreach ($symbols as $sym) {
    $candles = fetchBybitKlines($sym, '60', 100);
    if (!$candles) {
        echo "Ошибка загрузки данных для {$sym}\n";
        continue;
    }

    $curPrice = end($candles)['close'];

    // 1. Ищем импульс для Манипуляции (от 1.0%)
    $impManip  = detectLatestImpulse($candles, $MIN_IMP_MANIP);
    // 2. Ищем крупный импульс для Обычного входа (от 3.5%)
    $impNormal = detectLatestImpulse($candles, $MIN_IMP_NORMAL);

    if ($isCli) {
        echo "🪙 МОНЕТА: {$sym} | Текущая цена: " . fmt3($curPrice) . " $\n";
        echo "========================================================================================\n";

        // ─── БЛОК 1: ОБЫЧНЫЙ ВХОД (0.618 -> 0.500) ОТ 3.5% ───
        if ($impNormal) {
            $h_n = $impNormal['high'];
            $l_n = $impNormal['low'];
            $tp_n = calcFibLog($h_n, $l_n, 0.500);
            $in_n = calcFibLog($h_n, $l_n, 0.618);
            $sl_n = calcFibLog($h_n, $l_n, 0.764);
            $profit_pct = (($tp_n - $in_n) / $in_n) * 100.0;
            $dist_n = (($curPrice - $in_n) / $curPrice) * 100.0;

            $status_n = "";
            if ($curPrice <= $in_n && $curPrice > $sl_n) {
                $status_n = "🟢 ВХОД В LONG СЕЙЧАС!";
            } elseif ($curPrice > $in_n) {
                $status_n = "⏳ Ожидание: до входа осталось " . number_format($dist_n, 2) . "% падения";
            } else {
                $status_n = "🛑 Ниже стопа";
            }

            $tS_n = date('d.m H:i', (int)($impNormal['start_time'] / 1000));
            $tE_n = date('d.m H:i', (int)($impNormal['end_time'] / 1000));

            echo "  [1] 🟢 ОБЫЧНЫЙ ВХОД 0.618 → 0.500 (Импульс ≥ 3.5%):\n";
            echo "      • Волна: {$tS_n} → {$tE_n} (Дно: " . fmt3($l_n) . "$ | Пик: " . fmt3($h_n) . "$ | Размах: +" . number_format($impNormal['pct'], 2) . "%)\n";
            echo "      • 🟢 ВХОД (0.618)      : " . fmt3($in_n) . " $\n";
            echo "      • 🎯 ТЕЙК (0.500)      : " . fmt3($tp_n) . " $ (Чистый ход: +" . number_format($profit_pct, 2) . "%)\n";
            echo "      • 🛑 СТОП (0.764)      : " . fmt3($sl_n) . " $\n";
            echo "      • 👉 Статус            : {$status_n}\n";
        } else {
            echo "  [1] 🟢 ОБЫЧНЫЙ ВХОД 0.618 → 0.500 (Импульс ≥ 3.5%):\n";
            echo "      • Нет активного крупного импульса ≥ 3.5% (защита от комиссий)\n";
        }

        echo "  --------------------------------------------------------------------------------------\n";

        // ─── БЛОК 2: МАНИПУЛЯЦИЯ (1.618 / 2.0 DCA) ОТ 1.0% ───
        if ($impManip) {
            $h_m = $impManip['high'];
            $l_m = $impManip['low'];
            $tp_m = calcFibLog($h_m, $l_m, 0.500);
            $m1618 = calcFibLog($h_m, $l_m, 1.618);
            $m2000 = calcFibLog($h_m, $l_m, 2.000);
            $profit_m = (($tp_m - $m1618) / $m1618) * 100.0;
            $dist_m = (($curPrice - $m1618) / $curPrice) * 100.0;

            $status_m = "";
            if ($curPrice <= $m1618 && $curPrice > $m2000) {
                $status_m = "🟣 ВХОД В МАНИПУЛЯЦИЮ 1.618 СЕЙЧАС!";
            } elseif ($curPrice <= $m2000) {
                $status_m = "🟠 ДОБОР DCA 2.0 (2 лота) СЕЙЧАС!";
            } else {
                $status_m = "⏳ Ожидание: до уровня 1.618 осталось " . number_format($dist_m, 2) . "%";
            }

            $tS_m = date('d.m H:i', (int)($impManip['start_time'] / 1000));
            $tE_m = date('d.m H:i', (int)($impManip['end_time'] / 1000));

            echo "  [2] 🟣 МАНИПУЛЯЦИЯ 1.618 + 2.0 DCA (Импульс ≥ 1.0%):\n";
            echo "      • Волна: {$tS_m} → {$tE_m} (Дно: " . fmt3($l_m) . "$ | Пик: " . fmt3($h_m) . "$ | Размах: +" . number_format($impManip['pct'], 2) . "%)\n";
            echo "      • 🟣 ВХОД (1.618) 1 лот : " . fmt3($m1618) . " $\n";
            echo "      • 🟠 ДОБОР (2.000) 2x   : " . fmt3($m2000) . " $\n";
            echo "      • 🎯 ТЕЙК (0.500)       : " . fmt3($tp_m) . " $ (Чистый ход: +" . number_format($profit_m, 2) . "%)\n";
            echo "      • 👉 Статус             : {$status_m}\n";
        }
        echo "\n";
    } else {
        echo "<div class='card'>";
        echo "<h2>🪙 {$sym} — Цена: <span class='val'>" . fmt3($curPrice) . " $</span></h2>";
        
        if ($impNormal) {
            $h_n = $impNormal['high']; $l_n = $impNormal['low'];
            $tp_n = calcFibLog($h_n, $l_n, 0.500);
            $in_n = calcFibLog($h_n, $l_n, 0.618);
            $sl_n = calcFibLog($h_n, $l_n, 0.764);
            $profit_pct = (($tp_n - $in_n) / $in_n) * 100.0;
            echo "<h3>🟢 Обычный вход 0.618 → 0.500 (Импульс ≥ 3.5%)</h3>";
            echo "<p>Вход: <b>" . fmt3($in_n) . "$</b> | Тейк: <b class='green'>" . fmt3($tp_n) . "$ (+" . number_format($profit_pct, 2) . "%)</b> | Стоп: <b class='red'>" . fmt3($sl_n) . "$</b></p>";
        } else {
            echo "<p><i>Нет импульса ≥ 3.5% для обычного входа (защита от комиссий)</i></p>";
        }

        if ($impManip) {
            $h_m = $impManip['high']; $l_m = $impManip['low'];
            $tp_m = calcFibLog($h_m, $l_m, 0.500);
            $m1618 = calcFibLog($h_m, $l_m, 1.618);
            $m2000 = calcFibLog($h_m, $l_m, 2.000);
            $profit_m = (($tp_m - $m1618) / $m1618) * 100.0;
            echo "<h3>🟣 Манипуляция 1.618 + 2.0 DCA (Импульс ≥ 1.0%)</h3>";
            echo "<p>Вход 1.618: <b class='purple'>" . fmt3($m1618) . "$</b> | Добор 2.0: <b class='orange'>" . fmt3($m2000) . "$</b> | Тейк: <b class='green'>" . fmt3($tp_m) . "$ (+" . number_format($profit_m, 2) . "%)</b></p>";
        }
        echo "</div>";
    }
}

if (!$isCli) {
    echo "</body></html>";
}
