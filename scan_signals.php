<?php
/**
 * Live Screener & Signal Scanner для стратегии "Манипуляция на часе (Mon 1H)"
 * Монеты: HYPEUSDT, NEARUSDT, UNIUSDT
 * Запуск: php scan_signals.php
 */

error_reporting(E_ALL & ~E_DEPRECATED);
date_default_timezone_set('Europe/Moscow'); // UTC+3

$symbols = ['HYPEUSDT', 'NEARUSDT', 'UNIUSDT'];
$min_impulse_pct = 1.0; // Порог импульса 1.0%

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

    // Bybit отдает свечи от новых к старым. Переворачиваем в хронологический порядок:
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

// Поиск самого свежего актуального импульса (1-в-1 с Python бэктестом)
function detectLatestImpulse($candles, $min_pct = 1.0) {
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
                // Тень сломала 0.5 Fib — завершение
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
    echo "<!DOCTYPE html><html><head><meta charset='UTF-8'><title>Mon 1H Live Screener</title>";
    echo "<style>body{font-family:Consolas,monospace;background:#121214;color:#e1e1e6;padding:20px;}";
    echo ".card{background:#1e1e24;border-radius:8px;padding:15px;margin-bottom:20px;border-left:5px solid #00c853;}";
    echo ".val{font-weight:bold;color:#00e5ff;} table{width:100%;border-collapse:collapse;margin-top:10px;}";
    echo "th,td{border:1px solid #333;padding:8px;text-align:left;} th{background:#2a2a35;}";
    echo ".green{color:#00e676;} .purple{color:#d500f9;} .orange{color:#ff9100;} .red{color:#ff5252;}";
    echo "</style></head><body><h1>📡 Сканер точек входа (Mon 1H Strategy) — UTC+3</h1>";
} else {
    echo "\n=======================================================================\n";
    echo "  📡 СКАНЕР СИГНАЛОВ И ТОЧЕК ВХОДА (Mon 1H Strategy) — " . date('d.m.Y H:i') . " (UTC+3)\n";
    echo "=======================================================================\n\n";
}

foreach ($symbols as $sym) {
    $candles = fetchBybitKlines($sym, '60', 100);
    if (!$candles) {
        echo "Ошибка загрузки данных для {$sym}\n";
        continue;
    }

    $curPrice = end($candles)['close'];
    $imp = detectLatestImpulse($candles, $min_impulse_pct);

    if (!$imp) {
        if ($isCli) {
            echo "[$sym] Текущая цена: {$curPrice}$ | Нет активного импульса > {$min_impulse_pct}%\n\n";
        }
        continue;
    }

    $h = $imp['high'];
    $l = $imp['low'];

    // Расчет уровней (Log Scale)
    $tp0500 = calcFibLog($h, $l, 0.500);
    $in0618 = calcFibLog($h, $l, 0.618);
    $sl0764 = calcFibLog($h, $l, 0.764);
    $m1618  = calcFibLog($h, $l, 1.618);
    $m2000  = calcFibLog($h, $l, 2.000);

    // Определение статуса и рекомендации
    $distToNormal = (($curPrice - $in0618) / $curPrice) * 100.0;
    $distToManip  = (($curPrice - $m1618) / $curPrice) * 100.0;

    $recommendation = "";
    if ($curPrice <= $in0618 && $curPrice > $sl0764) {
        $recommendation = "🟢 ЗОНА ВХОДА В LONG (0.618)! Тейк: " . number_format($tp0500, 4) . "$, Стоп: " . number_format($sl0764, 4) . "$";
    } elseif ($curPrice <= $m1618 && $curPrice > $m2000) {
        $recommendation = "🟣 ЗОНА ВХОДА В МАНИПУЛЯЦИЮ 1.618! Тейк: " . number_format($tp0500, 4) . "$, Добор на: " . number_format($m2000, 4) . "$";
    } elseif ($curPrice <= $m2000) {
        $recommendation = "🟠 ЗОНА ДОБОРА МАНИПУЛЯЦИИ 2.0 (2x)! Тейк: " . number_format($tp0500, 4) . "$";
    } elseif ($curPrice > $in0618) {
        $recommendation = "⏳ ОЖИДАНИЕ КОРРЕКЦИИ: До входа в LONG 0.618 осталось " . number_format($distToNormal, 2) . "% падения.";
    } else {
        $recommendation = "🛑 Зона между Стопом и Манипуляцией. Ждем уровня 1.618 (" . number_format($m1618, 4) . "$).";
    }

    $tStart = date('d.m H:i', (int)($imp['start_time'] / 1000));
    $tEnd   = date('d.m H:i', (int)($imp['end_time'] / 1000));

    if ($isCli) {
        echo "🪙 МОНЕТА: {$sym}\n";
        echo "   Текущая цена   : " . number_format($curPrice, 4) . " $\n";
        echo "   Импульс волны  : {$tStart} → {$tEnd} (Дно: " . number_format($l, 4) . "$ | Пик: " . number_format($h, 4) . "$ | Размах: +" . number_format($imp['pct'], 2) . "%)\n";
        echo "   --------------------------------------------------------------------\n";
        echo "   🎯 [0.500] Тейк-Профит (TP)       : " . number_format($tp0500, 4) . " $\n";
        echo "   🟢 [0.618] Точка Входа в LONG     : " . number_format($in0618, 4) . " $\n";
        echo "   🛑 [0.764] Стоп-Лосс (SL)         : " . number_format($sl0764, 4) . " $\n";
        echo "   🟣 [1.618] Манипуляция (1 лот)    : " . number_format($m1618, 4) . " $\n";
        echo "   🟠 [2.000] Добор DCA (2 лота)     : " . number_format($m2000, 4) . " $\n";
        echo "   --------------------------------------------------------------------\n";
        echo "   👉 СТАТУС: {$recommendation}\n\n";
    } else {
        echo "<div class='card'>";
        echo "<h2>🪙 {$sym} — Цена: <span class='val'>" . number_format($curPrice, 4) . " $</span></h2>";
        echo "<p>Импульс: {$tStart} → {$tEnd} (Дно: " . number_format($l, 4) . "$ | Пик: " . number_format($h, 4) . "$ | Рост: +" . number_format($imp['pct'], 2) . "%)</p>";
        echo "<table><tr><th>Уровень Fib</th><th>Назначение</th><th>Цена ($)</th></tr>";
        echo "<tr><td class='green'>0.500</td><td>Тейк-Профит (TP)</td><td>" . number_format($tp0500, 4) . " $</td></tr>";
        echo "<tr><td class='green'>0.618</td><td>Точка Входа в LONG</td><td>" . number_format($in0618, 4) . " $</td></tr>";
        echo "<tr><td class='red'>0.764</td><td>Стоп-Лосс (SL)</td><td>" . number_format($sl0764, 4) . " $</td></tr>";
        echo "<tr><td class='purple'>1.618</td><td>Манипуляция (1 лот)</td><td>" . number_format($m1618, 4) . " $</td></tr>";
        echo "<tr><td class='orange'>2.000</td><td>Добор DCA (2 лота)</td><td>" . number_format($m2000, 4) . " $</td></tr>";
        echo "</table>";
        echo "<p style='margin-top:12px;font-size:16px;'><b>👉 Статус:</b> {$recommendation}</p>";
        echo "</div>";
    }
}

if (!$isCli) {
    echo "</body></html>";
}
