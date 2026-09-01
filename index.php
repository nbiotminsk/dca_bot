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

$all_symbols = ['CAKEUSDT', 'LINKUSDT', 'GRAMUSDT', 'DOGEUSDT', 'HYPEUSDT', 'XRPUSDT', 'NEARUSDT', 'UNIUSDT', 'SUIUSDT', 'ENAUSDT', 'AVAXUSDT', 'ICPUSDT'];
$symbols = $all_symbols;
if (isset($_GET['symbols']) && !empty($_GET['symbols'])) {
    $reqSyms = explode(',', trim($_GET['symbols']));
    $filtered = array_intersect($reqSyms, $all_symbols);
    if (!empty($filtered)) {
        $symbols = array_values($filtered);
    }
}
$MIN_IMP_MANIP  = 1.0;
$MIN_IMP_NORMAL = 2.0;

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

function fmtPrice($val, $sym = '') {
    $f = (float)$val;
    if ($f >= 1.0) {
        return number_format($f, 3, '.', '');
    }
    return number_format($f, 4, '.', '');
}

function fmt3($val) {
    $f = (float)$val;
    if ($f >= 1.0) {
        return number_format($f, 3, '.', '');
    }
    return number_format($f, 4, '.', '');
}

function calculateRSI($candles, $period = 14) {
    $n = count($candles);
    if ($n <= $period) return 50.0;
    
    $gains = 0;
    $losses = 0;
    for ($i = 1; $i <= $period; $i++) {
        $diff = $candles[$i]['close'] - $candles[$i - 1]['close'];
        if ($diff >= 0) $gains += $diff;
        else $losses += abs($diff);
    }
    $avgGain = $gains / $period;
    $avgLoss = $losses / $period;

    for ($i = $period + 1; $i < $n; $i++) {
        $diff = $candles[$i]['close'] - $candles[$i - 1]['close'];
        $gain = $diff >= 0 ? $diff : 0;
        $loss = $diff < 0 ? abs($diff) : 0;
        $avgGain = (($avgGain * ($period - 1)) + $gain) / $period;
        $avgLoss = (($avgLoss * ($period - 1)) + $loss) / $period;
    }

    if ($avgLoss == 0) return 100.0;
    $rs = $avgGain / $avgLoss;
    return 100.0 - (100.0 / (1.0 + $rs));
}

function calculateCCI($candles, $period = 14) {
    $n = count($candles);
    if ($n < $period) return 0.0;

    $hlc3 = [];
    foreach ($candles as $c) {
        $hlc3[] = ($c['high'] + $c['low'] + $c['close']) / 3.0;
    }

    $lastHLC3 = array_slice($hlc3, -$period);
    $sma = array_sum($lastHLC3) / $period;

    $meanDev = 0;
    foreach ($lastHLC3 as $v) {
        $meanDev += abs($v - $sma);
    }
    $meanDev = $meanDev / $period;

    if ($meanDev == 0) return 0.0;
    $curHLC3 = end($hlc3);
    return ($curHLC3 - $sma) / (0.015 * $meanDev);
}

function calculateEMA($candles, $period) {
    $n = count($candles);
    if ($n < $period) return 0.0;
    $k = 2.0 / ($period + 1.0);
    $slice = array_slice($candles, 0, $period);
    $sum = 0;
    foreach ($slice as $c) $sum += $c['close'];
    $ema = $sum / $period;
    for ($i = $period; $i < $n; $i++) {
        $ema = ($candles[$i]['close'] * $k) + ($ema * (1.0 - $k));
    }
    return $ema;
}

function calculateSuperTrend($candles, $period = 10, $multiplier = 3.0) {
    $n = count($candles);
    if ($n < $period + 1) return ['trend' => 1, 'supertrend' => 0.0];
    
    $tr = [];
    $tr[0] = $candles[0]['high'] - $candles[0]['low'];
    for ($i = 1; $i < $n; $i++) {
        $hl = $candles[$i]['high'] - $candles[$i]['low'];
        $hpc = abs($candles[$i]['high'] - $candles[$i - 1]['close']);
        $lpc = abs($candles[$i]['low'] - $candles[$i - 1]['close']);
        $tr[$i] = max($hl, $hpc, $lpc);
    }
    
    $atr = [];
    $sum = 0;
    for ($i = 0; $i < $period; $i++) $sum += $tr[$i];
    $atr[$period - 1] = $sum / $period;
    $alpha = 1.0 / $period;
    for ($i = $period; $i < $n; $i++) {
        $atr[$i] = $alpha * $tr[$i] + (1.0 - $alpha) * $atr[$i - 1];
    }
    
    $upperBand = [];
    $lowerBand = [];
    $trend = 1;
    $st = 0.0;
    
    for ($i = $period; $i < $n; $i++) {
        $hl2 = ($candles[$i]['high'] + $candles[$i]['low']) / 2.0;
        $basicUpper = $hl2 + ($multiplier * $atr[$i]);
        $basicLower = $hl2 - ($multiplier * $atr[$i]);
        
        $prevUpper = isset($upperBand[$i - 1]) ? $upperBand[$i - 1] : $basicUpper;
        $prevLower = isset($lowerBand[$i - 1]) ? $lowerBand[$i - 1] : $basicLower;
        $prevClose = $candles[$i - 1]['close'];
        
        $upperBand[$i] = ($basicUpper < $prevUpper || $prevClose > $prevUpper) ? $basicUpper : $prevUpper;
        $lowerBand[$i] = ($basicLower > $prevLower || $prevClose < $prevLower) ? $basicLower : $prevLower;
        
        if ($trend == 1 && $candles[$i]['close'] < $lowerBand[$i]) {
            $trend = -1;
        } else if ($trend == -1 && $candles[$i]['close'] > $upperBand[$i]) {
            $trend = 1;
        }
        $st = ($trend == 1) ? $lowerBand[$i] : $upperBand[$i];
    }
    return ['trend' => $trend, 'supertrend' => $st];
}

function detectLatestLongImpulse($candles, $min_pct = 1.5) {
    $n = count($candles);
    $best = null;

    if ($n < 3) return null;

    // Сканируем только закрытые свечи (до $n - 2)
    for ($s = max(0, $n - 35); $s < $n - 2; $s++) {
        $l_s = $candles[$s]['low'];
        $h_s = $candles[$s]['high'];
        $cur_h = $h_s;
        $is_impulse = false;
        $broken = false;
        $entered_05 = false;
        $end_idx = $s;

        for ($k = $s + 1; $k < $n - 1; $k++) {
            $l_k = $candles[$k]['low'];
            $h_k = $candles[$k]['high'];
            $c_k = $candles[$k]['close'];

            if (!$is_impulse) {
                if ($l_k < $l_s) {
                    $broken = true;
                    break;
                }
                $fib_05_first = calcFibLongLog($h_s, $l_s, 0.500);
                if ($l_k <= $fib_05_first) {
                    $broken = true;
                    break;
                }
                if ($h_k > $h_s) {
                    $is_impulse = true;
                    $cur_h = $h_k;
                    $end_idx = $k;
                }
            } else {
                if (!$entered_05) {
                    $fib_05_cur = calcFibLongLog($cur_h, $l_s, 0.500);
                    if ($l_k <= $fib_05_cur) {
                        $entered_05 = true;
                    } else {
                        if ($h_k > $cur_h) {
                            $cur_h = $h_k;
                            $end_idx = $k;
                        }
                    }
                }

                if ($entered_05) {
                    $tp_0382 = calcFibLongLog($cur_h, $l_s, 0.382);
                    $in_0618 = calcFibLongLog($cur_h, $l_s, 0.618);

                    if (!$entered_0618) {
                        if ($l_k <= $in_0618) {
                            $entered_0618 = true;
                        }
                    }

                    // Если был вход на 0.618 и после этого цена взяла тейк 0.382 (или пробила уровень 0) -> сигнал отработал, не показываем!
                    if ($entered_0618) {
                        if ($h_k >= $tp_0382 || $c_k >= $tp_0382 || $h_k >= $cur_h || $c_k >= $cur_h) {
                            $broken = true;
                            break;
                        }
                    } else {
                        // Если входили только на 0.500: пробой уровня 0 (хай) стирает фибу
                        if ($h_k >= $cur_h || $c_k >= $cur_h) {
                            $broken = true;
                            break;
                        }
                    }
                    
                    // Если упали ниже уровня 1.0 (Low базы l_s) -> проверяем манипуляцию 1.618 и откат к 0.5
                    if ($l_k < $l_s) {
                        $manip_1618 = calcFibLongLog($cur_h, $l_s, 1.618);
                        $tp_050 = calcFibLongLog($cur_h, $l_s, 0.500);
                        if ($l_k <= $manip_1618 && ($h_k >= $tp_050 || $c_k >= $tp_050)) {
                            $broken = true;
                            break;
                        }
                    }

                    // Выбили предельный стоп манипуляции (2.000 Fib)
                    $sl_lim = calcFibLongLog($cur_h, $l_s, 2.000);
                    if ($l_k <= $sl_lim) {
                        $broken = true;
                        break;
                    }
                }
            }
        }

        // На незакрытой живой свече n-1:
        // Если вход на 0.5 еще НЕ произошел — фиба динамически растет за новым максимумом!
        if ($is_impulse && !$broken) {
            $cur_live = $candles[$n - 1];
            if (!$entered_05) {
                if ($cur_live['high'] > $cur_h) {
                    $cur_h = $cur_live['high'];
                }
                $fib_05_live = calcFibLongLog($cur_h, $l_s, 0.500);
                if ($cur_live['low'] <= $fib_05_live) {
                    $entered_05 = true;
                }
            } else {
                $tp_0382 = calcFibLongLog($cur_h, $l_s, 0.382);
                $in_0618 = calcFibLongLog($cur_h, $l_s, 0.618);
                if (!$entered_0618 && $cur_live['low'] <= $in_0618) {
                    $entered_0618 = true;
                }

                if ($entered_0618 && ($cur_live['high'] >= $tp_0382 || $cur_live['close'] >= $tp_0382 || $cur_live['high'] >= $cur_h || $cur_live['close'] >= $cur_h)) {
                    $broken = true;
                } else {
                    $sl_lim = calcFibLongLog($cur_h, $l_s, 2.000);
                    if ($cur_live['high'] >= $cur_h || $cur_live['close'] >= $cur_h || $cur_live['low'] <= $sl_lim) {
                        $broken = true;
                    }
                }
            }
        }

        if ($is_impulse && !$broken) {
            $pct = ($cur_h - $l_s) / $l_s * 100.0;
            if ($pct >= $min_pct) {
                if ($best === null || $pct > $best['pct']) {
                    $best = [
                        'start_time' => $candles[$s]['time'],
                        'end_time'   => $candles[$end_idx]['time'],
                        'high'       => $cur_h,
                        'low'        => $l_s,
                        'pct'        => $pct,
                        'bars'       => $end_idx - $s + 1,
                        'is_live'    => true
                    ];
                }
            }
        }
    }

    return $best;
}

function detectLatestShortImpulse($candles, $min_pct = 2.0) {
    $n = count($candles);
    $best = null;

    if ($n < 3) return null;

    for ($s = max(0, $n - 35); $s < $n - 2; $s++) {
        $h_s = $candles[$s]['high'];
        $l_s = $candles[$s]['low'];
        $cur_l = $l_s;
        $is_dump = false;
        $broken = false;
        $entered_05 = false;
        $entered_0618 = false;
        $end_idx = $s;

        for ($k = $s + 1; $k < $n - 1; $k++) {
            $l_k = $candles[$k]['low'];
            $h_k = $candles[$k]['high'];
            $c_k = $candles[$k]['close'];

            if (!$is_dump) {
                if ($h_k > $h_s) {
                    $broken = true;
                    break;
                }
                $fib_05_first = calcFibShortLog($h_s, $l_s, 0.500);
                if ($h_k >= $fib_05_first) {
                    $broken = true;
                    break;
                }
                if ($l_k < $l_s) {
                    $is_dump = true;
                    $cur_l = $l_k;
                    $end_idx = $k;
                }
            } else {
                if (!$entered_05) {
                    $fib_05_cur = calcFibShortLog($h_s, $cur_l, 0.500);
                    if ($h_k >= $fib_05_cur) {
                        $entered_05 = true;
                    } else {
                        if ($l_k < $cur_l) {
                            $cur_l = $l_k;
                            $end_idx = $k;
                        }
                    }
                }

                if ($entered_05) {
                    $tp_0382 = calcFibShortLog($h_s, $cur_l, 0.382);
                    $in_0618 = calcFibShortLog($h_s, $cur_l, 0.618);

                    if (!$entered_0618) {
                        if ($h_k >= $in_0618) {
                            $entered_0618 = true;
                        }
                    }

                    if ($entered_0618) {
                        if ($l_k <= $tp_0382 || $c_k <= $tp_0382 || $l_k <= $cur_l || $c_k <= $cur_l) {
                            $broken = true;
                            break;
                        }
                    } else {
                        if ($l_k <= $cur_l || $c_k <= $cur_l) {
                            $broken = true;
                            break;
                        }
                    }

                    if ($h_k > $h_s) {
                        $manip_1618 = calcFibShortLog($h_s, $cur_l, 1.618);
                        $tp_050 = calcFibShortLog($h_s, $cur_l, 0.500);
                        if ($h_k >= $manip_1618 && ($l_k <= $tp_050 || $c_k <= $tp_050)) {
                            $broken = true;
                            break;
                        }
                    }

                    $sl_lim = calcFibShortLog($h_s, $cur_l, 2.000);
                    if ($h_k >= $sl_lim) {
                        $broken = true;
                        break;
                    }
                }
            }
        }

        if ($is_dump && !$broken) {
            $cur_live = $candles[$n - 1];
            if (!$entered_05) {
                if ($cur_live['low'] < $cur_l) {
                    $cur_l = $cur_live['low'];
                }
                $fib_05_live = calcFibShortLog($h_s, $cur_l, 0.500);
                if ($cur_live['high'] >= $fib_05_live) {
                    $entered_05 = true;
                }
            } else {
                $tp_0382 = calcFibShortLog($h_s, $cur_l, 0.382);
                $in_0618 = calcFibShortLog($h_s, $cur_l, 0.618);
                if (!$entered_0618 && $cur_live['high'] >= $in_0618) {
                    $entered_0618 = true;
                }

                if ($entered_0618 && ($cur_live['low'] <= $tp_0382 || $cur_live['close'] <= $tp_0382 || $cur_live['low'] <= $cur_l || $cur_live['close'] <= $cur_l)) {
                    $broken = true;
                } else {
                    $sl_lim = calcFibShortLog($h_s, $cur_l, 2.000);
                    if ($cur_live['low'] <= $cur_l || $cur_live['close'] <= $cur_l || $cur_live['high'] >= $sl_lim) {
                        $broken = true;
                    }
                }
            }
        }

        if ($is_dump && !$broken) {
            $pct = ($h_s - $cur_l) / $h_s * 100.0;
            if ($pct >= $min_pct) {
                if ($best === null || $pct > $best['pct']) {
                    $best = [
                        'start_time' => $candles[$s]['time'],
                        'end_time'   => $candles[$end_idx]['time'],
                        'high'       => $h_s,
                        'low'        => $cur_l,
                        'pct'        => $pct,
                        'bars'       => $end_idx - $s + 1,
                        'is_live'    => true
                    ];
                }
            }
        }
    }

    return $best;
}

function detectMacroLong($candles, $min_pct = 5.0) {
    $n = count($candles);
    $best = null;
    for ($b = 1; $b < min(45, $n - 2); $b++) {
        $h = $candles[$n - 1 - $b]['high'];
        if ($candles[$n - 1 - ($b - 1)]['high'] <= $h && $candles[$n - 1 - ($b + 1)]['high'] <= $h) {
            $cur_min_l = $h;
            $cur_min_s = null;
            $scan_end = min($b + 32, $n - 1);
            for ($s = $b + 1; $s <= $scan_end; $s++) {
                $l_val = $candles[$n - 1 - $s]['low'];
                if ($l_val < $cur_min_l) {
                    $cur_min_l = $l_val;
                    $cur_min_s = $s;
                }
            }
            if ($cur_min_s !== null) {
                $pct = ($h - $cur_min_l) / $cur_min_l * 100.0;
                if ($pct >= $min_pct) {
                    $broken_high = false;
                    for ($p = $b - 1; $p >= 0; $p--) {
                        if ($candles[$n - 1 - $p]['high'] > $h) {
                            $broken_high = true;
                            break;
                        }
                    }
                    if (!$broken_high) {
                        if ($best === null || $pct > $best['pct']) {
                            $best = [
                                'high'       => $h,
                                'low'        => $cur_min_l,
                                'pct'        => $pct,
                                'start_time' => $candles[$n - 1 - $cur_min_s]['time'],
                                'end_time'   => $candles[$n - 1 - $b]['time'],
                                'is_live'    => true
                            ];
                        }
                    }
                }
            }
        }
    }
    return $best;
}

function detectMacroShort($candles, $min_pct = 5.0) {
    $n = count($candles);
    $best = null;
    for ($b = 1; $b < min(45, $n - 2); $b++) {
        $l = $candles[$n - 1 - $b]['low'];
        if ($candles[$n - 1 - ($b - 1)]['low'] >= $l && $candles[$n - 1 - ($b + 1)]['low'] >= $l) {
            $cur_max_h = $l;
            $cur_max_s = null;
            $scan_end = min($b + 32, $n - 1);
            for ($s = $b + 1; $s <= $scan_end; $s++) {
                $h_val = $candles[$n - 1 - $s]['high'];
                if ($h_val > $cur_max_h) {
                    $cur_max_h = $h_val;
                    $cur_max_s = $s;
                }
            }
            if ($cur_max_s !== null) {
                $pct = ($cur_max_h - $l) / $cur_max_h * 100.0;
                if ($pct >= $min_pct) {
                    $broken_low = false;
                    for ($p = $b - 1; $p >= 0; $p--) {
                        if ($candles[$n - 1 - $p]['low'] < $l) {
                            $broken_low = true;
                            break;
                        }
                    }
                    if (!$broken_low) {
                        if ($best === null || $pct > $best['pct']) {
                            $best = [
                                'high'       => $cur_max_h,
                                'low'        => $l,
                                'pct'        => $pct,
                                'start_time' => $candles[$n - 1 - $cur_max_s]['time'],
                                'end_time'   => $candles[$n - 1 - $b]['time'],
                                'is_live'    => true
                            ];
                        }
                    }
                }
            }
        }
    }
    return $best;
}

function detectLatestLongManipulation($candles, $min_pct = 2.0, $lookback = 72) {
    $n = count($candles);
    $best = null;

    for ($start_idx = max(0, $n - $lookback); $start_idx < $n - 2; $start_idx++) {
        $imp_low  = $candles[$start_idx]['low'];
        $imp_high = $candles[$start_idx]['high'];
        $end_idx  = $start_idx;

        for ($k = $start_idx + 1; $k < $n; $k++) {
            $fib_05 = calcFibLongLog($imp_high, $imp_low, 0.500);
            if ($candles[$k]['low'] <= $fib_05 || $candles[$k]['low'] < $imp_low) {
                break;
            }
            if ($candles[$k]['high'] > $imp_high) {
                $imp_high = $candles[$k]['high'];
                $end_idx  = $k;
            }
        }

        if ($end_idx > $start_idx) {
            $pct = ($imp_high - $imp_low) / $imp_low * 100.0;
            if ($pct >= $min_pct) {
                $m1618 = calcFibLongLog($imp_high, $imp_low, 1.618);
                $m2000 = calcFibLongLog($imp_high, $imp_low, 2.000);
                $tp050 = calcFibLongLog($imp_high, $imp_low, 0.500);

                $touched_1618 = false;
                $tp_hit = false;
                $sl_hit = false;

                for ($p = $end_idx + 1; $p < $n; $p++) {
                    if (!$touched_1618) {
                        if ($candles[$p]['low'] <= $m1618) {
                            $touched_1618 = true;
                        }
                    } else {
                        if ($candles[$p]['high'] >= $tp050) {
                            $tp_hit = true;
                            break;
                        }
                        if ($candles[$p]['low'] <= $m2000) {
                            $sl_hit = true;
                            break;
                        }
                    }
                }

                if ($touched_1618 && !$tp_hit && !$sl_hit) {
                    if ($best === null || $pct > $best['pct']) {
                        $best = [
                            'start_time' => $candles[$start_idx]['time'],
                            'end_time'   => $candles[$end_idx]['time'],
                            'high'       => $imp_high,
                            'low'        => $imp_low,
                            'pct'        => $pct,
                            'is_live'    => true
                        ];
                    }
                }
            }
        }
    }
    return $best;
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
        $impLN = detectLatestLongImpulse($candles, 1.5);
        $impLM = detectLatestLongManipulation($candles, $MIN_IMP_MANIP);
        $impSN = detectLatestShortImpulse($candles, $MIN_IMP_NORMAL);
        $impMacroL = detectMacroLong($candles, 5.0);
        $impMacroS = detectMacroShort($candles, 5.0);

        $card = ['symbol' => $sym, 'price' => fmtPrice($curPrice, $sym), 'raw_price' => $curPrice];

        $long_time = $impLN ? $impLN['end_time'] : 0;
        $short_time = $impSN ? $impSN['end_time'] : 0;

        // Long Normal
        // Карта исторического винрейта на 18-месячной истории (13 000 свечей 1H)
        $coinStats = [
            'CAKEUSDT' => ['wr_normal' => '89.7%', 'wr_manip' => '65.5%', 'sl_fib' => 2.618, 'rr' => '1:2.4'],
            'XRPUSDT'  => ['wr_normal' => '89.4%', 'wr_manip' => '64.2%', 'sl_fib' => 2.618, 'rr' => '1:2.0'],
            'GRAMUSDT' => ['wr_normal' => '88.7%', 'wr_manip' => '47.8%', 'sl_fib' => 2.291, 'rr' => '1:3.0'],
            'SUIUSDT'  => ['wr_normal' => '88.2%', 'wr_manip' => '65.5%', 'sl_fib' => 2.618, 'rr' => '1:3.0'],
            'UNIUSDT'  => ['wr_normal' => '87.9%', 'wr_manip' => '63.3%', 'sl_fib' => 2.618, 'rr' => '1:2.4'],
            'HYPEUSDT' => ['wr_normal' => '87.9%', 'wr_manip' => '62.5%', 'sl_fib' => 2.291, 'rr' => '1:3.0'],
            'LINKUSDT' => ['wr_normal' => '87.7%', 'wr_manip' => '70.6%', 'sl_fib' => 2.618, 'rr' => '1:2.4'],
            'DOGEUSDT' => ['wr_normal' => '87.5%', 'wr_manip' => '69.8%', 'sl_fib' => 2.618, 'rr' => '1:2.4'],
            'AVAXUSDT' => ['wr_normal' => '86.6%', 'wr_manip' => '57.9%', 'sl_fib' => 2.618, 'rr' => '1:3.0'],
            'ICPUSDT'  => ['wr_normal' => '86.5%', 'wr_manip' => '63.2%', 'sl_fib' => 2.337, 'rr' => '1:2.7'],
            'NEARUSDT' => ['wr_normal' => '85.4%', 'wr_manip' => '63.0%', 'sl_fib' => 2.618, 'rr' => '1:2.4'],
            'ENAUSDT'  => ['wr_normal' => '82.0%', 'wr_manip' => '66.7%', 'sl_fib' => 2.291, 'rr' => '1:3.0']
        ];
        $cur_ema34 = calculateEMA($candles, 34);
        $cur_ema50 = calculateEMA($candles, 50);
        $st_res = calculateSuperTrend($candles, 10, 3.0);
        $is_st_bull = ($st_res['trend'] === 1);
        $is_ema_bull = ($cur_ema34 > $cur_ema50);
        
        $active_imp = ($long_time >= $short_time && $impLN) ? $impLN : ($impSN ? $impSN : null);
        $imp_pct = $active_imp ? (float)$active_imp['pct'] : 0.0;
        $imp_bars = $active_imp ? (int)$active_imp['bars'] : 0;
        
        $is_mature = ($imp_bars >= 4);
        $is_strong = ($imp_pct >= 3.5);
        $is_safe_imp = ($is_mature && $is_strong);

        $card['ema34'] = number_format($cur_ema34, 4);
        $card['ema50'] = number_format($cur_ema50, 4);
        $card['ema_bull'] = $is_ema_bull;
        $card['ema_status'] = $is_ema_bull ? 'EMA34 > EMA50' : 'EMA34 <= EMA50';
        $card['st_status'] = $is_st_bull ? 'BULLISH' : 'BEARISH';
        $card['imp_pct'] = number_format($imp_pct, 1);
        $card['imp_bars'] = $imp_bars;
        $card['imp_safe'] = $is_safe_imp;
        $card['imp_status'] = $is_safe_imp ? "💎 Зрелый (≥3.5%, {$imp_bars} св.)" : ($imp_bars < 4 ? "⚠️ Спайк ({$imp_bars} св.)" : "⚠️ Слабый ({$card['imp_pct']}%)");

        // Оценка риска:
        $score = ($is_safe_imp ? 1 : 0) + ($is_st_bull ? 1 : 0) + ($is_ema_bull ? 1 : 0);
        if ($score === 3) {
            $card['risk_level'] = 'LOW';
            $card['risk_text'] = '🟢 Супер-вход (Винрейт ~93%)';
            $card['risk_badge_col'] = 'rgba(16, 185, 129, 0.2)';
            $card['risk_border_col'] = 'rgba(16, 185, 129, 0.5)';
            $card['risk_txt_col'] = '#10b981';
        } else if ($score === 2) {
            $card['risk_level'] = 'MED';
            $card['risk_text'] = '🔵 Умеренный риск (2 из 3)';
            $card['risk_badge_col'] = 'rgba(59, 130, 246, 0.2)';
            $card['risk_border_col'] = 'rgba(59, 130, 246, 0.5)';
            $card['risk_txt_col'] = '#3b82f6';
        } else {
            $card['risk_level'] = 'HIGH';
            $card['risk_text'] = '🔴 Опасность лавины (Ждать 1.618!)';
            $card['risk_badge_col'] = 'rgba(239, 68, 68, 0.2)';
            $card['risk_border_col'] = 'rgba(239, 68, 68, 0.5)';
            $card['risk_txt_col'] = '#ef4444';
        }

        $stats = isset($coinStats[$sym]) ? $coinStats[$sym] : ['wr_normal' => '85%', 'wr_manip' => '75%', 'sl_fib' => 2.500, 'rr' => '1:2.0'];
        $card['wr_normal'] = $stats['wr_normal'];
        $card['wr_manip']  = $stats['wr_manip'];

        // 🏛️ БОЛЬШАЯ (ГЛОБАЛЬНАЯ) ФИБА LONG
        if ($impMacroL) {
            $m_in050  = calcFibLongLog($impMacroL['high'], $impMacroL['low'], 0.500);
            $m_in0618 = calcFibLongLog($impMacroL['high'], $impMacroL['low'], 0.618);
            $m_in0786 = calcFibLongLog($impMacroL['high'], $impMacroL['low'], 0.786);
            $m_tp0382 = calcFibLongLog($impMacroL['high'], $impMacroL['low'], 0.382);
            $m_sl0860 = calcFibLongLog($impMacroL['high'], $impMacroL['low'], 0.860);

            $card['macro_long'] = [
                'high'         => fmtPrice($impMacroL['high'], $sym),
                'low'          => fmtPrice($impMacroL['low'], $sym),
                'raw_high'     => (float)$impMacroL['high'],
                'raw_low'      => (float)$impMacroL['low'],
                'entry_050'    => fmtPrice($m_in050, $sym),
                'raw_e050'     => (float)$m_in050,
                'entry_0618'   => fmtPrice($m_in0618, $sym),
                'raw_e0618'    => (float)$m_in0618,
                'entry_0786'   => fmtPrice($m_in0786, $sym),
                'raw_e0786'    => (float)$m_in0786,
                'tp_0382'      => fmtPrice($m_tp0382, $sym),
                'raw_tp0382'   => (float)$m_tp0382,
                'sl'           => fmtPrice($m_sl0860, $sym),
                'raw_sl'       => (float)$m_sl0860,
                'pct'          => number_format($impMacroL['pct'], 2),
                'active'       => ($curPrice <= $m_in050 && $curPrice > $m_sl0860),
                'time'         => date('d.m H:i', (int)($impMacroL['end_time'] / 1000)),
                'wr'           => '88.5%'
            ];
        }

        // ⚡ МЛАДШАЯ ФИБА LONG
        if ($impLN) {
            $in050  = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $in0618 = calcFibLongLog($impLN['high'], $impLN['low'], 0.618);
            $tp0500 = calcFibLongLog($impLN['high'], $impLN['low'], 0.500);
            $tp0382 = calcFibLongLog($impLN['high'], $impLN['low'], 0.382);
            $sl0860 = calcFibLongLog($impLN['high'], $impLN['low'], 0.860);

            $card['long_normal'] = [
                'entry_050'    => fmtPrice($in050, $sym),
                'raw_e050'     => (float)$in050,
                'entry_0618'   => fmtPrice($in0618, $sym),
                'raw_e0618'    => (float)$in0618,
                'tp_0500'      => fmtPrice($tp0500, $sym),
                'raw_tp0500'   => (float)$tp0500,
                'tp_0382'      => fmtPrice($tp0382, $sym),
                'raw_tp0382'   => (float)$tp0382,
                'sl'           => fmtPrice($sl0860, $sym),
                'raw_sl'       => (float)$sl0860,
                'pct'          => number_format($impLN['pct'], 2),
                'active'       => ($curPrice <= $in050 && $curPrice > $sl0860),
                'time'         => date('d.m H:i', (int)($impLN['end_time'] / 1000)),
                'is_fresher'   => ($long_time >= $short_time),
                'wr'           => $stats['wr_normal']
            ];
        }

        // Short Normal
        if ($impSN) {
            $in050  = calcFibShortLog($impSN['high'], $impSN['low'], 0.500);
            $in0618 = calcFibShortLog($impSN['high'], $impSN['low'], 0.618);
            $tp0500 = calcFibShortLog($impSN['high'], $impSN['low'], 0.500);
            $tp0382 = calcFibShortLog($impSN['high'], $impSN['low'], 0.382);
            $sl0860 = calcFibShortLog($impSN['high'], $impSN['low'], 0.860);

            $card['short_normal'] = [
                'entry_050'    => fmtPrice($in050, $sym),
                'raw_e050'     => (float)$in050,
                'entry_0618'   => fmtPrice($in0618, $sym),
                'raw_e0618'    => (float)$in0618,
                'tp_0500'      => fmtPrice($tp0500, $sym),
                'raw_tp0500'   => (float)$tp0500,
                'tp_0382'      => fmtPrice($tp0382, $sym),
                'raw_tp0382'   => (float)$tp0382,
                'sl'           => fmtPrice($sl0860, $sym),
                'raw_sl'       => (float)$sl0860,
                'pct'          => number_format($impSN['pct'], 2),
                'active'       => ($curPrice >= $in050 && $curPrice < $sl0860),
                'time'         => date('d.m H:i', (int)($impSN['end_time'] / 1000)),
                'is_fresher'   => ($short_time > $long_time),
                'wr'           => $stats['wr_normal']
            ];
        }

        // Long Manip
        if ($impLM) {
            $m1 = calcFibLongLog($impLM['high'], $impLM['low'], 1.618);
            $m2 = calcFibLongLog($impLM['high'], $impLM['low'], 2.000);
            $tp1 = calcFibLongLog($impLM['high'], $impLM['low'], 0.618);
            $tp2 = calcFibLongLog($impLM['high'], $impLM['low'], 0.500);
            $sl_opt = calcFibLongLog($impLM['high'], $impLM['low'], $stats['sl_fib']);

            $card['long_manip'] = [
                'entry_1'      => fmtPrice($m1, $sym),
                'raw_e1'       => (float)$m1,
                'entry_2'      => fmtPrice($m2, $sym),
                'raw_e2'       => (float)$m2,
                'tp_1'         => fmtPrice($tp1, $sym),
                'raw_tp1'      => (float)$tp1,
                'tp_2'         => fmtPrice($tp2, $sym),
                'raw_tp2'      => (float)$tp2,
                'sl'           => fmtPrice($sl_opt, $sym),
                'raw_sl'       => (float)$sl_opt,
                'sl_fib'       => $stats['sl_fib'],
                'rr_label'     => $stats['rr'],
                'pct'          => number_format($impLM['pct'], 2),
                'active'       => ($curPrice <= $m1 && $curPrice > $sl_opt),
                'time'         => date('d.m H:i', (int)($impLM['end_time'] / 1000)),
                'wr'           => $stats['wr_manip']
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
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🟢 Long 0.5: " . number_format($d, 2) . "%"; }
            }
            if (isset($card['short_normal']) && $card['short_normal']['is_fresher']) {
                $d = abs($curPrice - $card['short_normal']['raw_e050']) / $curPrice * 100.0;
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🔴 Short 0.5: " . number_format($d, 2) . "%"; }
            }
            if (isset($card['long_manip'])) {
                $d = abs($curPrice - $card['long_manip']['raw_e1']) / $curPrice * 100.0;
                if ($d < $minDist) { $minDist = $d; $nearestDesc = "🟣 Манип 1.6: " . number_format($d, 2) . "%"; }
            }

            if ($minDist < 990.0) {
                $priorityScore = 1.0 + $minDist; // Приоритет по близости в %
                $card['best_choice'] = "⏳ {$nearestDesc}";
            } else {
                $priorityScore = 999.0;
                $card['best_choice'] = "💤 Вне позиции";
            }
        }

        // Сводка по активному импульсу для превью карточки
        $impSummary = "";
        if ($long_time >= $short_time && isset($card['long_normal'])) {
            $impSummary = "⚡ +" . $card['long_normal']['pct'] . "%";
        } elseif ($short_time > $long_time && isset($card['short_normal'])) {
            $impSummary = "⚡ -" . $card['short_normal']['pct'] . "%";
        } elseif (isset($card['long_manip'])) {
            $impSummary = "⚡ +" . $card['long_manip']['pct'] . "%";
        }
        $card['impulse_summary'] = $impSummary;

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
    <title>Тест стратегии Николая</title>
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
        
        .coin-filter-panel {
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 12px 16px;
            margin-bottom: 20px;
        }
        .coin-filter-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
            font-size: 13px;
            font-weight: 700;
            color: var(--text-dim);
        }
        .coin-filter-actions {
            display: flex;
            gap: 10px;
            font-size: 12px;
        }
        .coin-filter-btn {
            background: rgba(255,255,255,0.06);
            border: 1px solid var(--border);
            color: #fff;
            padding: 2px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: 600;
            transition: background 0.2s;
        }
        .coin-filter-btn:hover {
            background: rgba(255,255,255,0.12);
        }
        .coin-chips-list {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }
        .coin-chip {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(255,255,255,0.03);
            border: 1px solid var(--border);
            padding: 6px 10px;
            border-radius: 8px;
            cursor: pointer;
            user-select: none;
            font-size: 13px;
            font-weight: 700;
            transition: all 0.15s ease;
        }
        .coin-chip input[type="checkbox"] {
            cursor: pointer;
            accent-color: var(--yellow);
            width: 15px;
            height: 15px;
        }
        .coin-chip.active {
            background: rgba(255, 214, 0, 0.12);
            border-color: rgba(255, 214, 0, 0.35);
            color: #fff;
        }
        .coin-chip:hover {
            border-color: var(--yellow);
        }
        .chip-wr {
            font-size: 10px;
            color: var(--green);
            background: rgba(0, 230, 118, 0.12);
            padding: 1px 4px;
            border-radius: 3px;
            font-family: monospace;
        }
        
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
            padding: 16px 20px; 
            box-shadow: 0 4px 20px rgba(0,0,0,0.3); 
            transition: border-color 0.2s, box-shadow 0.2s;
        }
        .coin-card.is-active-signal {
            border-color: rgba(0, 230, 118, 0.4);
            box-shadow: 0 0 20px rgba(0, 230, 118, 0.1);
        }
        .coin-header { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            cursor: pointer;
            user-select: none;
            padding: 4px 0;
        }
        .coin-header:hover .coin-title {
            color: var(--yellow);
        }
        .coin-header-left {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .coin-header-right {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .toggle-icon {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 28px;
            height: 28px;
            border-radius: 6px;
            background: rgba(255,255,255,0.06);
            border: 1px solid var(--border);
            color: var(--text-dim);
            font-size: 13px;
            transition: transform 0.25s ease, background 0.2s, color 0.2s;
        }
        .coin-card.open .toggle-icon {
            transform: rotate(180deg);
            background: rgba(255,214,0,0.15);
            color: var(--yellow);
            border-color: rgba(255,214,0,0.3);
        }
        .badge-wr { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 800; letter-spacing: 0; white-space: nowrap; }
        .badge-impulse { background: rgba(255, 214, 0, 0.15); color: var(--yellow); border: 1px solid rgba(255, 214, 0, 0.35); padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 800; letter-spacing: 0; white-space: nowrap; }
        .coin-title { font-size: 22px; font-weight: 800; transition: color 0.2s; white-space: nowrap; }
        .coin-price { font-size: 22px; font-weight: 800; color: #fff; font-family: monospace; white-space: nowrap; }
        .coin-quick-summary {
            font-size: 13px;
            color: var(--text-dim);
            background: rgba(255,255,255,0.04);
            padding: 4px 10px;
            border-radius: 6px;
            border: 1px solid rgba(255,255,255,0.08);
            font-weight: 600;
            white-space: nowrap;
        }
        .coin-card.is-active-signal .coin-quick-summary {
            background: rgba(0, 230, 118, 0.15);
            color: var(--green);
            border-color: rgba(0, 230, 118, 0.3);
        }

        .coin-body {
            display: none;
            margin-top: 14px;
            padding-top: 14px;
            border-top: 1px solid rgba(255,255,255,0.06);
            animation: fadeIn 0.2s ease;
        }
        .coin-card.open .coin-body {
            display: block;
        }
        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(-4px); }
            to { opacity: 1; transform: translateY(0); }
        }

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
        .block-title { font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .badge-wr { background: rgba(0, 230, 118, 0.15); color: var(--green); border: 1px solid rgba(0, 230, 118, 0.3); padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 800; letter-spacing: 0; }
        
        .table-levels { width: 100%; border-collapse: collapse; font-size: 14.5px; font-family: monospace; }
        .table-levels td { padding: 5px 0; vertical-align: middle; }
        .table-levels td:last-child { text-align: right; font-weight: 800; font-size: 15px; }
        .lbl { color: var(--text-dim); font-size: 13px; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; font-weight: 600; }
        
        .general-levels-box {
            display: flex;
            flex-direction: column;
            gap: 4px;
            background: rgba(0,0,0,0.4);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            padding: 8px 12px;
            margin-bottom: 8px;
            font-size: 13px;
            font-family: monospace;
        }
        .level-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 2px 0;
        }
        .level-row .pill-lbl { color: var(--text-dim); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 12px; }
        .level-row .pill-val { font-weight: 800; font-size: 13.5px; }

        .scenario-box {
            background: rgba(0, 0, 0, 0.28);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 9px 11px;
            margin-top: 7px;
        }
        .scenario-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            padding-bottom: 5px;
            margin-bottom: 5px;
        }
        .scenario-title {
            font-size: 12px;
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 5px;
        }
        .scenario-coins {
            font-size: 11px;
            color: var(--text-dim);
            font-family: monospace;
            font-weight: 700;
        }
        .scenario-grid {
            display: flex;
            flex-direction: column;
            gap: 3.5px;
            font-size: 12px;
            font-family: monospace;
        }
        .scenario-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .scenario-lbl { color: var(--text-dim); font-size: 11.5px; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }
        .scenario-val { font-weight: 800; }

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

        /* 📱 Компактный аккуратный вид на мобильных */
        @media (max-width: 768px) {
            body { padding: 10px; }
            .grid { gap: 14px; }
            .coin-card { padding: 12px; border-radius: 10px; }
            .coin-body { margin-top: 10px; padding-top: 10px; }
            .coin-blocks-row { flex-direction: column; width: 100%; gap: 10px; }
            .coin-blocks-row .block { flex: 1 1 100%; width: 100%; min-width: 100%; box-sizing: border-box; padding: 10px; }
            .table-levels { font-size: 12.5px; width: 100%; }
            .table-levels td { padding: 3px 0; }
            .table-levels td:last-child { font-size: 13px; font-weight: 700; }
            .lbl { font-size: 11.5px; font-weight: 500; }
            .price-num { font-size: 13px; font-weight: 700; }
            .coins-tag { font-size: 10px; font-weight: 800; padding: 1px 5px; }
            .margin-subtext { font-size: 10px; font-weight: 600; }
            .profit-payout-box { font-size: 11px; padding: 7px 9px; margin-top: 8px; width: 100%; box-sizing: border-box; }
            .profit-payout-row { padding: 2.5px 0; }
            .payout-val-green, .payout-val-cyan, .payout-val-red { font-size: 11.5px; font-weight: 800; }
            .status-pill { font-size: 11px; padding: 5px; margin-top: 7px; width: 100%; box-sizing: border-box; }
            .coin-title { font-size: 17px; }
            .coin-price { font-size: 15px; font-weight: 800; }
            .coin-header-right { gap: 8px; }
            .coin-header-left { gap: 6px; }
            .badge-wr { font-size: 9.5px; padding: 1px 4px; }
            .badge-impulse { font-size: 9.5px; padding: 1px 4px; }
            .coin-quick-summary { font-size: 11px; padding: 2px 6px; }
            .toggle-icon { width: 24px; height: 24px; font-size: 11px; }
        }
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
    <h1>📡 Тест стратегии Николая</h1>
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

<!-- 🪙 ПАНЕЛЬ ВЫБОРА МОНЕТ ДЛЯ ОПРОСА АПИ -->
<div class="coin-filter-panel">
    <div class="coin-filter-header">
        <span>🪙 Опрашивать по API только выбранные монеты:</span>
        <div class="coin-filter-actions">
            <button type="button" class="coin-filter-btn" onclick="selectAllCoins(true)">Выбрать все</button>
            <button type="button" class="coin-filter-btn" onclick="selectAllCoins(false)">Снять все</button>
        </div>
    </div>
    <div class="coin-chips-list" id="coin-selector-container">
        <!-- Генерируется из JS -->
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

    // Автономные входы (когда входим ТОЛЬКО на 0.500 или ТОЛЬКО на 0.618 на ВЕСЬ риск депозита)
    const q_solo1 = maxRiskDollar / d1;
    const q_solo2 = maxRiskDollar / d2;
    const margin_solo1 = (q_solo1 * e1) / lev;
    const margin_solo2 = (q_solo2 * e2) / lev;

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
        
        // Полноразмерные одиночные входы (на 100% риска)
        q_solo1: q_solo1,
        q_solo2: q_solo2,
        q_solo1_fmt: fmtCoinQty(q_solo1),
        q_solo2_fmt: fmtCoinQty(q_solo2),
        margin_solo1: margin_solo1.toFixed(1),
        margin_solo2: margin_solo2.toFixed(1),

        stop_pct: stopPct,
        loss_if_only_1: (q1 * d1).toFixed(2),
        loss_if_only_2: (q2 * d2).toFixed(2),
        loss_total: maxRiskDollar.toFixed(2)
    };
}

function renderCards(data) {
    if (!data || !data.items) return;
    let html = '';
    data.items.forEach(c => {
        const coinTicker = c.symbol.replace('USDT', '');

        // Расчет сетки DCA для Macro Long (Большая Фиба: 0.500 1x + 0.618 2x со стопом 0.860)
        let macro_grid = null;
        let macro_pnl_only1_to_382 = "0.00";
        let macro_pnl_only2_to_500 = "0.00";
        let macro_pnl_both_to_382 = "0.00";

        if (c.macro_long) {
            macro_grid = calculateDcaGrid(c.macro_long.raw_e050, c.macro_long.raw_e0618, c.macro_long.raw_sl, false);
            if (macro_grid) {
                macro_pnl_only1_to_382 = (macro_grid.q_solo1 * (c.macro_long.raw_tp0382 - c.macro_long.raw_e050)).toFixed(2);
                macro_pnl_only2_to_500 = (macro_grid.q_solo2 * (c.macro_long.raw_e050 - c.macro_long.raw_e0618)).toFixed(2);
                const p1 = macro_grid.q1 * (c.macro_long.raw_tp0382 - c.macro_long.raw_e050);
                const p2 = macro_grid.q2 * (c.macro_long.raw_tp0382 - c.macro_long.raw_e0618);
                macro_pnl_both_to_382 = (p1 + p2).toFixed(2);
            }
        }

        // Расчет сетки DCA для Long Normal
        let ln_grid = null;
        let ln_pnl_only1_to_382 = "0.00";
        let ln_pnl_only2_to_500 = "0.00";
        let ln_pnl_only2_to_382 = "0.00";
        let ln_pnl_both_to_500 = "0.00";
        let ln_pnl_split_50_382 = "0.00";
        let ln_pnl_both_to_382 = "0.00";

        if (c.long_normal) {
            ln_grid = calculateDcaGrid(c.long_normal.raw_e050, c.long_normal.raw_e0618, c.long_normal.raw_sl, false);
            if (ln_grid) {
                // Если входим ТОЛЬКО на 0.500 (на 100% риска)
                ln_pnl_only1_to_382 = (ln_grid.q_solo1 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e050)).toFixed(2);
                
                // Если входим ТОЛЬКО на 0.618 (на 100% риска)
                ln_pnl_only2_to_500 = (ln_grid.q_solo2 * (c.long_normal.raw_tp0500 - c.long_normal.raw_e0618)).toFixed(2);
                ln_pnl_only2_to_382 = (ln_grid.q_solo2 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e0618)).toFixed(2);
                
                // Если входим СЕТКОЙ (0.5 1x + 0.618 2x)
                const pnl2_to_500_dca = ln_grid.q2 * (c.long_normal.raw_tp0500 - c.long_normal.raw_e0618);
                const pnl2_to_382_dca = ln_grid.q2 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e0618);
                const pnl1_to_382_dca = ln_grid.q1 * (c.long_normal.raw_tp0382 - c.long_normal.raw_e050);
                const full_pnl_382_dca = pnl1_to_382_dca + pnl2_to_382_dca;

                ln_pnl_both_to_500 = pnl2_to_500_dca.toFixed(2);
                ln_pnl_split_50_382 = ((0.50 * pnl2_to_500_dca) + (0.50 * full_pnl_382_dca)).toFixed(2);
                ln_pnl_both_to_382 = full_pnl_382_dca.toFixed(2);
            }
        }

        // Расчет сетки DCA для Short Normal
        let sn_grid = null;
        let sn_pnl_only1_to_382 = "0.00";
        let sn_pnl_only2_to_500 = "0.00";
        let sn_pnl_only2_to_382 = "0.00";
        let sn_pnl_both_to_500 = "0.00";
        let sn_pnl_split_50_382 = "0.00";
        let sn_pnl_both_to_382 = "0.00";

        if (c.short_normal) {
            sn_grid = calculateDcaGrid(c.short_normal.raw_e050, c.short_normal.raw_e0618, c.short_normal.raw_sl, true);
            if (sn_grid) {
                // Если входим ТОЛЬКО на 0.500 (на 100% риска)
                sn_pnl_only1_to_382 = (sn_grid.q_solo1 * (c.short_normal.raw_e050 - c.short_normal.raw_tp0382)).toFixed(2);
                
                // Если входим ТОЛЬКО на 0.618 (на 100% риска)
                sn_pnl_only2_to_500 = (sn_grid.q_solo2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0500)).toFixed(2);
                sn_pnl_only2_to_382 = (sn_grid.q_solo2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0382)).toFixed(2);
                
                // Если входим СЕТКОЙ (0.5 1x + 0.618 2x)
                const pnl2_to_500_dca = sn_grid.q2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0500);
                const pnl2_to_382_dca = sn_grid.q2 * (c.short_normal.raw_e0618 - c.short_normal.raw_tp0382);
                const pnl1_to_382_dca = sn_grid.q1 * (c.short_normal.raw_e050 - c.short_normal.raw_tp0382);
                const full_pnl_382_dca = pnl1_to_382_dca + pnl2_to_382_dca;

                sn_pnl_both_to_500 = pnl2_to_500_dca.toFixed(2);
                sn_pnl_split_50_382 = ((0.50 * pnl2_to_500_dca) + (0.50 * full_pnl_382_dca)).toFixed(2);
                sn_pnl_both_to_382 = full_pnl_382_dca.toFixed(2);
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
                // Если входим ТОЛЬКО на 1.618 (на 100% риска)
                lm_pnl_only1_tp1 = (lm_grid.q_solo1 * (c.long_manip.raw_tp1 - c.long_manip.raw_e1)).toFixed(2);
                lm_pnl_only1_tp2 = (lm_grid.q_solo1 * (c.long_manip.raw_tp2 - c.long_manip.raw_e1)).toFixed(2);

                // Если входим СЕТКОЙ (1.618 1x + 2.000 2x)
                const pnl1_tp1 = lm_grid.q1 * (c.long_manip.raw_tp1 - c.long_manip.raw_e1);
                const pnl2_tp1 = lm_grid.q2 * (c.long_manip.raw_tp1 - c.long_manip.raw_e2);
                lm_pnl_both_tp1 = (pnl1_tp1 + pnl2_tp1).toFixed(2);

                const pnl1_tp2 = lm_grid.q1 * (c.long_manip.raw_tp2 - c.long_manip.raw_e1);
                const pnl2_tp2 = lm_grid.q2 * (c.long_manip.raw_tp2 - c.long_manip.raw_e2);
                lm_pnl_both_tp2 = (pnl1_tp2 + pnl2_tp2).toFixed(2);
            }
        }

        // Определяем наличие активного входа
        const hasActiveSignal = (c.long_normal && c.long_normal.active) || 
                                (c.short_normal && c.short_normal.active) || 
                                (c.long_manip && c.long_manip.active);

        // Статус раскрытия: сохраняем в памяти, по умолчанию активные сигналы раскрыты, остальные свернуты
        const isOpen = openedCards[c.symbol] !== undefined ? openedCards[c.symbol] : (hasActiveSignal);

        html += `
        <div class="coin-card ${isOpen ? 'open' : ''} ${hasActiveSignal ? 'is-active-signal' : ''}" id="card-${c.symbol}">
            <div class="coin-header" onclick="toggleCard('${c.symbol}')">
                <div class="coin-header-left">
                    <div class="coin-title">${coinTicker}<span style="color:var(--text-dim); font-size:13px;"> / USDT</span></div>
                    <div style="display:flex; gap:6px; align-items:center; flex-wrap:wrap;">
                        <span class="badge-wr" title="Исторический Win Rate обычного Long/Short">Норм: ${c.wr_normal}</span>
                        <span class="badge-wr" style="color:var(--purple); background:rgba(213,0,249,0.12); border-color:rgba(213,0,249,0.3);" title="Исторический Win Rate Манипуляции">Манип: ${c.wr_manip}</span>
                        <span class="badge-wr" style="color:${c.imp_safe ? 'var(--green)' : 'var(--orange)'}; background:${c.imp_safe ? 'rgba(0,230,118,0.12)' : 'rgba(249,115,22,0.12)'}; border-color:${c.imp_safe ? 'rgba(0,230,118,0.3)' : 'rgba(249,115,22,0.3)'};" title="Качество импульса">${c.imp_status}</span>
                        <span class="badge-wr" style="color:${c.st_status === 'BULLISH' ? 'var(--green)' : 'var(--red)'}; background:${c.st_status === 'BULLISH' ? 'rgba(0,230,118,0.12)' : 'rgba(239,68,68,0.12)'}; border-color:${c.st_status === 'BULLISH' ? 'rgba(0,230,118,0.3)' : 'rgba(239,68,68,0.3)'};" title="SuperTrend (10, 3.0)">ST: ${c.st_status}</span>
                        <span class="badge-wr" style="color:${c.ema_bull ? 'var(--green)' : 'var(--red)'}; background:${c.ema_bull ? 'rgba(0,230,118,0.12)' : 'rgba(239,68,68,0.12)'}; border-color:${c.ema_bull ? 'rgba(0,230,118,0.3)' : 'rgba(239,68,68,0.3)'};" title="EMA 34 / 50">${c.ema_status}</span>
                        <span class="badge-wr" style="color:${c.risk_txt_col}; background:${c.risk_badge_col}; border-color:${c.risk_border_col}; font-weight:700;" title="Оценка риска входа">${c.risk_text}</span>
                        ${c.impulse_summary ? `<span class="badge-impulse" title="Текущий импульс монеты">${c.impulse_summary}</span>` : ''}
                    </div>
                    <div class="coin-quick-summary">${c.best_choice}</div>
                </div>
                <div class="coin-header-right">
                    <div class="coin-price">${c.price} $</div>
                    <div class="toggle-icon">▼</div>
                </div>
            </div>

            <div class="coin-body">
                <div class="verdict-box">👉 РЕШЕНИЕ: ${c.best_choice}</div>
                
                <div class="coin-blocks-row">
                ${c.macro_long && macro_grid ? `
                <!-- 🏛️ БОЛЬШАЯ (ГЛОБАЛЬНАЯ) ФИБА LONG -->
                <div class="block" style="border-left: 3px solid var(--yellow);">
                    <div class="block-title" style="color:var(--yellow);">
                        <span>🏛️ БОЛЬШАЯ ФИБА (${c.macro_long.low} ➔ ${c.macro_long.high}) <span class="badge-wr">WR ${c.macro_long.wr}</span></span>
                        <span style="font-size:10px; color:var(--text-dim);">${c.macro_long.time}</span>
                    </div>

                    <!-- 📍 ОБЩИЕ УРОВНИ БОЛЬШОЙ ФИБЫ -->
                    <div class="general-levels-box">
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-1 (0.500 Fib):</span> <span class="pill-val c-cyan">${c.macro_long.entry_050} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-2 (0.618 Fib):</span> <span class="pill-val c-blue">${c.macro_long.entry_0618} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-3 (0.786 Fib):</span> <span class="pill-val" style="color:var(--purple); font-weight:800;">${c.macro_long.entry_0786} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-1 (0.382 Fib):</span> <span class="pill-val c-green">${c.macro_long.tp_0382} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🛑 Стоп (0.860 Fib):</span> <span class="pill-val c-red">${c.macro_long.sl} $</span></div>
                    </div>

                    <!-- 1. ВХОД В БОЛЬШУЮ ФИБУ 0.500 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--cyan);">
                        <div class="scenario-header">
                            <div class="scenario-title c-cyan">🔹 Вариант 1: Вход только от 0.500</div>
                            <div class="scenario-coins"><span class="coins-tag">${macro_grid.q_solo1_fmt} ${coinTicker}</span> <span class="margin-subtext">($${macro_grid.margin_solo1})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход (Тейк 0.382):</span><span class="scenario-val payout-val-green">+$${macro_pnl_only1_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток (Стоп 0.860):</span><span class="scenario-val payout-val-red">-$${macro_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 2. СЕТКА В БОЛЬШОЙ ФИБЕ 0.500 + 0.618 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--green);">
                        <div class="scenario-header">
                            <div class="scenario-title c-green">🔥 Вариант 2: Сетка 0.5 (1x) + 0.618 (2x)</div>
                            <div class="scenario-coins"><span class="coins-tag">${fmtCoinQty(macro_grid.q_total)} ${coinTicker}</span> <span class="margin-subtext">($${(parseFloat(macro_grid.margin1)+parseFloat(macro_grid.margin2)).toFixed(1)})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🔹 Доли:</span><span class="scenario-val" style="font-size:11.5px; color:var(--text-dim);">1x (${macro_grid.q1_fmt}) + 2x (${macro_grid.q2_fmt})</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🚀 Доход при Тейке (0.382):</span><span class="scenario-val payout-val-green">+$${macro_pnl_both_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток на стопе (ОБА входа):</span><span class="scenario-val payout-val-red">-$${macro_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <div class="status-pill ${c.macro_long.active ? 'status-ready' : 'status-wait'}">
                        ${c.macro_long.active ? '🏛️ ВХОД В БОЛЬШУЮ ФИБУ ПРЯМО СЕЙЧАС' : '⏳ Ожидание макро-отката'}
                    </div>
                </div>
                ` : ''}

                ${c.long_normal && ln_grid ? `
                <!-- 1. LONG NORMAL -->
                <div class="block" style="border-left: 3px solid var(--blue); opacity: ${!c.long_normal.is_fresher ? '0.6' : '1.0'};">
                    <div class="block-title c-blue">
                        <span>⚡ МЛАДШАЯ ФИБА (0.5 / 0.618) <span class="badge-wr">WR ${c.long_normal.wr}</span></span>
                        <span style="font-size:10px; color:var(--text-dim);">${c.long_normal.time}</span>
                    </div>

                    <!-- 📍 ОБЩИЕ УРОВНИ И ЦЕНЫ -->
                    <div class="general-levels-box">
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-1 (0.500 Fib):</span> <span class="pill-val c-cyan">${c.long_normal.entry_050} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-2 (0.618 Fib):</span> <span class="pill-val c-blue">${c.long_normal.entry_0618} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-1 (0.500 Fib):</span> <span class="pill-val c-green">${c.long_normal.tp_0500} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-2 (0.382 Fib):</span> <span class="pill-val c-green">${c.long_normal.tp_0382} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🛑 Стоп (0.860 Fib):</span> <span class="pill-val c-red">${c.long_normal.sl} $</span></div>
                    </div>

                    <!-- 1. ВХОД ТОЛЬКО ОТ 0.5 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--cyan);">
                        <div class="scenario-header">
                            <div class="scenario-title c-cyan">🔹 Вариант 1: Вход только от 0.500</div>
                            <div class="scenario-coins"><span class="coins-tag">${ln_grid.q_solo1_fmt} ${coinTicker}</span> <span class="margin-subtext">($${ln_grid.margin_solo1})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход (Тейк 0.382):</span><span class="scenario-val payout-val-green">+$${ln_pnl_only1_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток (Стоп 0.860):</span><span class="scenario-val payout-val-red">-$${ln_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 2. ВХОД ОТ 0.5 + ОТ 0.618 (СЕТКА) -->
                    <div class="scenario-box" style="border-left: 3px solid var(--green);">
                        <div class="scenario-header">
                            <div class="scenario-title c-green">🔥 Вариант 2: Сетка 0.5 (1x) + 0.618 (2x)</div>
                            <div class="scenario-coins"><span class="coins-tag">${fmtCoinQty(ln_grid.q_total)} ${coinTicker}</span> <span class="margin-subtext">($${(parseFloat(ln_grid.margin1)+parseFloat(ln_grid.margin2)).toFixed(1)})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🔹 Доли входа:</span><span class="scenario-val" style="font-size:11.5px; color:var(--text-dim);">1x (${ln_grid.q1_fmt}) + 2x (${ln_grid.q2_fmt})</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.500):</span><span class="scenario-val payout-val-green">+$${ln_pnl_both_to_500}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">⭐ Сплит (50% на ТП1 + 50% на ТП2):</span><span class="scenario-val payout-val-cyan">+$${ln_pnl_split_50_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🚀 Доход при ТП2 (0.382):</span><span class="scenario-val payout-val-green">+$${ln_pnl_both_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток на стопе (ОБА входа):</span><span class="scenario-val payout-val-red">-$${ln_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 3. ВХОД ТОЛЬКО ОТ 0.618 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--blue);">
                        <div class="scenario-header">
                            <div class="scenario-title c-blue">🔹 Вариант 3: Вход только от 0.618</div>
                            <div class="scenario-coins"><span class="coins-tag">${ln_grid.q_solo2_fmt} ${coinTicker}</span> <span class="margin-subtext">($${ln_grid.margin_solo2})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.500):</span><span class="scenario-val payout-val-green">+$${ln_pnl_only2_to_500}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🚀 Доход при ТП2 (0.382):</span><span class="scenario-val payout-val-green">+$${ln_pnl_only2_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток (Стоп 0.860):</span><span class="scenario-val payout-val-red">-$${ln_grid.loss_total}</span></div>
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
                        <span>🔴 SHORT ОБЫЧНЫЙ (0.5 / 0.618) <span class="badge-wr">WR ${c.short_normal.wr}</span></span>
                        <span style="font-size:10px; color:var(--text-dim);">${c.short_normal.time}</span>
                    </div>

                    <!-- 📍 ОБЩИЕ УРОВНИ И ЦЕНЫ В SHORT -->
                    <div class="general-levels-box">
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-1 в Short (0.500 Fib):</span> <span class="pill-val c-orange">${c.short_normal.entry_050} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🔹 Вход-2 в Short (0.618 Fib):</span> <span class="pill-val c-red">${c.short_normal.entry_0618} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-1 (0.500 Fib):</span> <span class="pill-val c-green">${c.short_normal.tp_0500} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-2 (0.382 Fib):</span> <span class="pill-val c-green">${c.short_normal.tp_0382} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🛑 Стоп (0.860 Fib):</span> <span class="pill-val c-red">${c.short_normal.sl} $</span></div>
                    </div>

                    <!-- 1. ВХОД В SHORT ТОЛЬКО ОТ 0.5 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--orange);">
                        <div class="scenario-header">
                            <div class="scenario-title c-orange">🔹 Вариант 1: Вход в Short только от 0.500</div>
                            <div class="scenario-coins"><span class="coins-tag">${sn_grid.q_solo1_fmt} ${coinTicker}</span> <span class="margin-subtext">($${sn_grid.margin_solo1})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход (Тейк 0.382):</span><span class="scenario-val payout-val-green">+$${sn_pnl_only1_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток (Стоп 0.860):</span><span class="scenario-val payout-val-red">-$${sn_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 2. ВХОД В SHORT 0.5 + 0.618 (СЕТКА) -->
                    <div class="scenario-box" style="border-left: 3px solid var(--red);">
                        <div class="scenario-header">
                            <div class="scenario-title c-red">🔥 Вариант 2: Сетка 0.5 (1x) + 0.618 (2x)</div>
                            <div class="scenario-coins"><span class="coins-tag">${fmtCoinQty(sn_grid.q_total)} ${coinTicker}</span> <span class="margin-subtext">($${(parseFloat(sn_grid.margin1)+parseFloat(sn_grid.margin2)).toFixed(1)})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🔹 Доли входа:</span><span class="scenario-val" style="font-size:11.5px; color:var(--text-dim);">1x (${sn_grid.q1_fmt}) + 2x (${sn_grid.q2_fmt})</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.500):</span><span class="scenario-val payout-val-green">+$${sn_pnl_both_to_500}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">⭐ Сплит (50% на ТП1 + 50% на ТП2):</span><span class="scenario-val payout-val-cyan">+$${sn_pnl_split_50_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🚀 Доход при ТП2 (0.382):</span><span class="scenario-val payout-val-green">+$${sn_pnl_both_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток на стопе (ОБА входа):</span><span class="scenario-val payout-val-red">-$${sn_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 3. ВХОД В SHORT ТОЛЬКО ОТ 0.618 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--red);">
                        <div class="scenario-header">
                            <div class="scenario-title c-red">🔹 Вариант 3: Вход в Short только от 0.618</div>
                            <div class="scenario-coins"><span class="coins-tag">${sn_grid.q_solo2_fmt} ${coinTicker}</span> <span class="margin-subtext">($${sn_grid.margin_solo2})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.500):</span><span class="scenario-val payout-val-green">+$${sn_pnl_only2_to_500}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🚀 Доход при ТП2 (0.382):</span><span class="scenario-val payout-val-green">+$${sn_pnl_only2_to_382}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток (Стоп 0.860):</span><span class="scenario-val payout-val-red">-$${sn_grid.loss_total}</span></div>
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
                        <span>🟣 МАНИПУЛЯЦИЯ (1.618+2.0) <span class="badge-wr">WR ${c.long_manip.wr}</span></span>
                        <span style="font-size:10px; color:var(--text-dim);">${c.long_manip.time}</span>
                    </div>

                    <!-- 📍 ОБЩИЕ УРОВНИ И ЦЕНЫ В МАНИПУЛЯЦИИ -->
                    <div class="general-levels-box">
                        <div class="level-row"><span class="pill-lbl">🟣 Вход-1 (1.618 Fib):</span> <span class="pill-val c-purple">${c.long_manip.entry_1} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🟠 Добор-2 (2.000 Fib):</span> <span class="pill-val c-orange">${c.long_manip.entry_2} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-1 (0.618 Fib):</span> <span class="pill-val c-green">${c.long_manip.tp_1} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🎯 Тейк-2 (0.500 Fib):</span> <span class="pill-val c-green">${c.long_manip.tp_2} $</span></div>
                        <div class="level-row"><span class="pill-lbl">🛑 Стоп (${c.long_manip.sl_fib} Fib):</span> <span class="pill-val c-red">${c.long_manip.sl} $</span></div>
                    </div>

                    <!-- 1. ВХОД ТОЛЬКО ОТ 1.618 -->
                    <div class="scenario-box" style="border-left: 3px solid var(--purple);">
                        <div class="scenario-header">
                            <div class="scenario-title c-purple">🟣 Вариант 1: Вход только от 1.618</div>
                            <div class="scenario-coins"><span class="coins-tag">${lm_grid.q_solo1_fmt} ${coinTicker}</span> <span class="margin-subtext">($${lm_grid.margin_solo1})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.618):</span><span class="scenario-val payout-val-green">+$${lm_pnl_only1_tp1}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🔥 Доход при ТП2 (0.500):</span><span class="scenario-val payout-val-cyan">+$${lm_pnl_only1_tp2}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток на стопе:</span><span class="scenario-val payout-val-red">-$${lm_grid.loss_total}</span></div>
                        </div>
                    </div>

                    <!-- 2. ВХОД 1.618 + 2.0 DCA -->
                    <div class="scenario-box" style="border-left: 3px solid var(--purple);">
                        <div class="scenario-header">
                            <div class="scenario-title c-purple">🔥 Вариант 2: Сетка 1.618 (1x) + 2.000 (2x)</div>
                            <div class="scenario-coins"><span class="coins-tag">${fmtCoinQty(lm_grid.q_total)} ${coinTicker}</span> <span class="margin-subtext">($${(parseFloat(lm_grid.margin1)+parseFloat(lm_grid.margin2)).toFixed(1)})</span></div>
                        </div>
                        <div class="scenario-grid">
                            <div class="scenario-row"><span class="scenario-lbl">🔹 Доли входа:</span><span class="scenario-val" style="font-size:11.5px; color:var(--text-dim);">1x (${lm_grid.q1_fmt}) + 2x (${lm_grid.q2_fmt})</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🎯 Доход при ТП1 (0.618):</span><span class="scenario-val payout-val-green">+$${lm_pnl_both_tp1}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🔥 Доход при ТП2 (0.500):</span><span class="scenario-val payout-val-green">+$${lm_pnl_both_tp2}</span></div>
                            <div class="scenario-row"><span class="scenario-lbl">🛑 Убыток на стопе:</span><span class="scenario-val payout-val-red">-$${lm_grid.loss_total}</span></div>
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
            </div> <!-- .coin-body -->
        </div>
        `;
    });
    document.getElementById('coins-container').innerHTML = html;
}

const ALL_AVAILABLE_COINS = [
    { sym: 'CAKEUSDT', name: 'CAKE', wr_n: '89.7%', wr_m: '65.5%' },
    { sym: 'XRPUSDT',  name: 'XRP',  wr_n: '89.4%', wr_m: '64.2%' },
    { sym: 'GRAMUSDT', name: 'GRAM', wr_n: '88.7%', wr_m: '47.8%' },
    { sym: 'SUIUSDT',  name: 'SUI',  wr_n: '88.2%', wr_m: '65.5%' },
    { sym: 'UNIUSDT',  name: 'UNI',  wr_n: '87.9%', wr_m: '63.3%' },
    { sym: 'HYPEUSDT', name: 'HYPE', wr_n: '87.9%', wr_m: '62.5%' },
    { sym: 'LINKUSDT', name: 'LINK', wr_n: '87.7%', wr_m: '70.6%' },
    { sym: 'DOGEUSDT', name: 'DOGE', wr_n: '87.5%', wr_m: '69.8%' },
    { sym: 'AVAXUSDT', name: 'AVAX', wr_n: '86.6%', wr_m: '57.9%' },
    { sym: 'ICPUSDT',  name: 'ICP',  wr_n: '86.5%', wr_m: '63.2%' },
    { sym: 'NEARUSDT', name: 'NEAR', wr_n: '85.4%', wr_m: '63.0%' },
    { sym: 'ENAUSDT',  name: 'ENA',  wr_n: '82.0%', wr_m: '66.7%' }
];

let selectedCoins = JSON.parse(localStorage.getItem('dca_selected_coins') || 'null');
if (!selectedCoins || !Array.isArray(selectedCoins)) {
    selectedCoins = ALL_AVAILABLE_COINS.map(c => c.sym); // По умолчанию выбраны все
}

function renderCoinSelector() {
    const container = document.getElementById('coin-selector-container');
    if (!container) return;
    let html = '';
    ALL_AVAILABLE_COINS.forEach(c => {
        const isChecked = selectedCoins.includes(c.sym);
        html += `
        <label class="coin-chip ${isChecked ? 'active' : ''}">
            <input type="checkbox" value="${c.sym}" ${isChecked ? 'checked' : ''} onchange="toggleCoinSelection('${c.sym}')">
            <span>${c.name}</span>
            <span class="chip-wr">WR ${c.wr_n}</span>
        </label>
        `;
    });
    container.innerHTML = html;
}

function toggleCoinSelection(sym) {
    if (selectedCoins.includes(sym)) {
        selectedCoins = selectedCoins.filter(s => s !== sym);
    } else {
        selectedCoins.push(sym);
    }
    localStorage.setItem('dca_selected_coins', JSON.stringify(selectedCoins));
    renderCoinSelector();
    updateScreener();
}

function selectAllCoins(selectAll) {
    if (selectAll) {
        selectedCoins = ALL_AVAILABLE_COINS.map(c => c.sym);
    } else {
        selectedCoins = [];
    }
    localStorage.setItem('dca_selected_coins', JSON.stringify(selectedCoins));
    renderCoinSelector();
    updateScreener();
}

let openedCards = JSON.parse(localStorage.getItem('dca_opened_cards') || '{}');

function toggleCard(symbol) {
    const card = document.getElementById('card-' + symbol);
    if (!card) return;
    
    if (card.classList.contains('open')) {
        card.classList.remove('open');
        openedCards[symbol] = false;
    } else {
        card.classList.add('open');
        openedCards[symbol] = true;
    }
    localStorage.setItem('dca_opened_cards', JSON.stringify(openedCards));
}

async function updateScreener() {
    if (isRefreshing) return;
    isRefreshing = true;
    const btn = document.getElementById('refresh-btn');
    if (btn) btn.classList.add('loading');

    try {
        if (selectedCoins.length === 0) {
            document.getElementById('coins-container').innerHTML = `
            <div style="background:rgba(255,255,255,0.02); border:1px dashed var(--border); border-radius:12px; padding:30px; text-align:center; color:var(--text-dim);">
                ⚠️ Не выбрана ни одна монета для опроса API. Отметьте галочками нужные монеты в панели выше.
            </div>`;
            return;
        }

        const currentUrl = window.location.pathname;
        const querySyms = selectedCoins.join(',');
        const res = await fetch(currentUrl + '?ajax=1&symbols=' + encodeURIComponent(querySyms) + '&t=' + new Date().getTime());
        globalData = await res.json();
        document.getElementById('update-time').innerText = 'UTC+3: ' + globalData.time + ' (' + globalData.items.length + ' монет)';
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
    renderCoinSelector();

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
