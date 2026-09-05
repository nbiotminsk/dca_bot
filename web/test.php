<?php
/**
 * PHP Backtest & Strategy Tester for Custom DCA Bot
 * 
 * Runs historical simulation on Bybit candles for Custom DCA Bot strategy (Long & Short)
 * Calculates: Total PnL %, Win Rate, Trades Count, Max Drawdown, Sharpe, Sortino, Liquidations count, Avg Entries.
 * 
 * Works standalone on simple PHP hosting (PHP 7.4+ / 8.x).
 */

header('Content-Type: text/html; charset=utf-8');

// Supported symbols mapping
$allowedCoins = [
    'HYPE' => 'HYPEUSDT',
    'UNI'  => 'UNIUSDT',
    'SOL'  => 'SOLUSDT',
    'LINK' => 'LINKUSDT',
    'ETH'  => 'ETHUSDT',
    'MNT'  => 'MNTUSDT',
    'GRAM' => 'GRAMUSDT',
];

// Handle AJAX backtest API request
if (isset($_GET['api']) && $_GET['api'] === 'run_backtest') {
    header('Content-Type: application/json; charset=utf-8');

    $coin = strtoupper($_GET['coin'] ?? 'HYPE');
    $days = (int)($_GET['days'] ?? 90);
    $interval = $_GET['interval'] ?? 'D'; // D, 60, 240, 15
    $direction = $_GET['direction'] ?? 'long'; // long or short
    $calcMode = $_GET['calcMode'] ?? 'deposit'; // deposit or qty
    $deposit = (float)($_GET['deposit'] ?? 500);
    
    // Grid settings
    $baseQty = (float)($_GET['baseQty'] ?? 0.19);
    $leverage = (int)($_GET['leverage'] ?? 1);
    $maxOrders = (int)($_GET['maxOrders'] ?? 7); // safety orders count
    $coveragePct = (float)($_GET['coveragePct'] ?? 2.28); // % перекрытия
    $priceScale = (float)($_GET['priceScale'] ?? 1.0228);
    $multiplier = (float)($_GET['multiplier'] ?? 1.10);
    $targetProfitPct = (float)($_GET['targetProfit'] ?? 1.0); // % TP
    $stopLossPct = (float)($_GET['stopLoss'] ?? 20.0);
    $mmrPct = (float)($_GET['mmr'] ?? 0.5) / 100.0;
    $feePct = (float)($_GET['fee'] ?? 0.04) / 100.0; // maker/taker fee
    $stepInterval = max(1, (int)($_GET['stepInterval'] ?? 1)); // candle step for starting new cycles

    if (!isset($allowedCoins[$coin])) {
        echo json_encode(['status' => 'error', 'message' => 'Неизвестная монета']);
        exit;
    }

    $symbol = $allowedCoins[$coin];
    
    // Calculate required candle count based on interval
    $intervalMap = [
        'D'   => 1,
        '240' => 6,
        '60'  => 24,
        '15'  => 96,
    ];
    $candlesPerDay = $intervalMap[$interval] ?? 1;
    $limit = min(max($days * $candlesPerDay, 5), 1000);

    // Fetch candles from Bybit API V5
    $url = "https://api.bybit.com/v5/market/kline?category=linear&symbol={$symbol}&interval={$interval}&limit={$limit}";
    $json = @file_get_contents($url);
    if (!$json) {
        $ch = curl_init();
        curl_setopt($ch, CURLOPT_URL, $url);
        curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
        curl_setopt($ch, CURLOPT_TIMEOUT, 8);
        curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0');
        $json = curl_exec($ch);
        curl_close($ch);
    }

    $data = json_decode($json, true);
    if (($data['retCode'] ?? -1) !== 0 || empty($data['result']['list'])) {
        echo json_encode(['status' => 'error', 'message' => 'Не удалось загрузить исторические свечи']);
        exit;
    }

    // Parse candles chronologically
    $rawList = array_reverse($data['result']['list']);
    $candles = [];
    foreach ($rawList as $c) {
        $candles[] = [
            'time'  => (int)$c[0],
            'open'  => (float)$c[1],
            'high'  => (float)$c[2],
            'low'   => (float)$c[3],
            'close' => (float)$c[4],
            'vol'   => (float)$c[5],
        ];
    }

    $nCandles = count($candles);
    if ($nCandles < 5) {
        echo json_encode(['status' => 'error', 'message' => 'Слишком мало свечей для тестов']);
        exit;
    }

    $isLong = ($direction === 'long');
    $N = $maxOrders;
    $totalOrders = $N + 1;

    // Sum of multiplier ratios for total grid volume
    if (abs($multiplier - 1.0) < 1e-6) {
        $sumMultiplier = $totalOrders;
    } else {
        $sumMultiplier = (pow($multiplier, $totalOrders) - 1.0) / ($multiplier - 1.0);
    }

    // Calculate base step deviation per order from coverage %
    $baseStepDev = $coveragePct / 100.0;
    if ($N > 0) {
        if (abs($priceScale - 1.0) < 1e-6) {
            $baseStepDev = ($coveragePct / 100.0) / $N;
        } else {
            $sumScales = (pow($priceScale, $N) - 1.0) / ($priceScale - 1.0);
            $baseStepDev = ($coveragePct / 100.0) / $sumScales;
        }
    }

    // Run Backtest Simulation
    $trades = [];
    $i = 0;

    while ($i < $nCandles - 1) {
        $startCandle = $candles[$i];
        $entryPrice = $startCandle['close'];

        // Determine 1st order base quantity for this cycle
        if ($calcMode === 'deposit' && $deposit > 0 && $entryPrice > 0) {
            $initMarginUsdt = $deposit / $sumMultiplier;
            $cycleBaseQty = ($initMarginUsdt * $leverage) / $entryPrice;
        } else {
            $cycleBaseQty = $baseQty;
        }

        // Build grid order levels and quantities
        $gridPrices = [];
        $gridCoins = [];
        $prevP = $entryPrice;

        for ($o = 0; $o < $totalOrders; $o++) {
            if ($o === 0) {
                $gridPrices[] = $entryPrice;
            } else {
                $stepDev = $baseStepDev * pow($priceScale, $o - 1);
                $p = $isLong ? $prevP * (1.0 - $stepDev) : $prevP * (1.0 + $stepDev);
                $gridPrices[] = max(0.00000001, $p);
                $prevP = $p;
            }
            $gridCoins[] = $cycleBaseQty * pow($multiplier, $o);
        }

        // Active cycle tracking
        $filledCount = 1; // 1st order placed immediately
        $cumCoins = $gridCoins[0];
        $cumEntryVal = $gridCoins[0] * $gridPrices[0];
        $cumMargin = ($gridCoins[0] * $gridPrices[0]) / $leverage;

        $hitTp = false;
        $hitSl = false;
        $liquidated = false;
        $exitPrice = 0.0;
        $exitIdx = $i;

        for ($j = $i; $j < $nCandles; $j++) {
            $c = $candles[$j];
            $exitIdx = $j;
            $avgCost = $cumEntryVal / $cumCoins;

            // Check if further safety orders execute on adverse candle move
            for ($o = $filledCount; $o < $totalOrders; $o++) {
                $targetOrderP = $gridPrices[$o];
                $triggered = $isLong ? ($c['low'] <= $targetOrderP) : ($c['high'] >= $targetOrderP);

                if ($triggered) {
                    $fillP = $targetOrderP;
                    $coinsAdd = $gridCoins[$o];
                    $cumCoins += $coinsAdd;
                    $cumEntryVal += $coinsAdd * $fillP;
                    $cumMargin += ($coinsAdd * $fillP) / $leverage;
                    $filledCount++;
                    $avgCost = $cumEntryVal / $cumCoins;
                } else {
                    break;
                }
            }

            // Liquidation Price calculation
            $liqPrice = $isLong
                ? ($cumEntryVal - $cumMargin) / ($cumCoins * (1.0 - $mmrPct))
                : ($cumEntryVal + $cumMargin) / ($cumCoins * (1.0 + $mmrPct));

            if ($liqPrice > 0) {
                $isLiq = $isLong ? ($c['low'] <= $liqPrice) : ($c['high'] >= $liqPrice);
                if ($isLiq) {
                    $liquidated = true;
                    $exitPrice = $liqPrice;
                    break;
                }
            }

            // Take Profit Price calculation
            $tpSign = $isLong ? 1.0 : -1.0;
            $tpPrice = $avgCost * (1.0 + $tpSign * ($targetProfitPct / 100.0));

            $isTp = $isLong ? ($c['high'] >= $tpPrice) : ($c['low'] <= $tpPrice);
            if ($isTp) {
                $hitTp = true;
                $exitPrice = $tpPrice;
                break;
            }

            // Stop Loss Price calculation
            if ($stopLossPct > 0) {
                $maxLossUsdt = $cumMargin * ($stopLossPct / 100.0);
                $slPrice = $isLong
                    ? max(0.0, $avgCost - $maxLossUsdt / $cumCoins)
                    : $avgCost + $maxLossUsdt / $cumCoins;

                $isSl = $isLong ? ($c['low'] <= $slPrice) : ($c['high'] >= $slPrice);
                if ($isSl) {
                    $hitSl = true;
                    $exitPrice = $slPrice;
                    break;
                }
            }
        }

        if (!$hitTp && !$hitSl && !$liquidated) {
            // Closed at end of candles horizon
            $exitPrice = $candles[$nCandles - 1]['close'];
        }

        // PnL Calculation
        $rawPnlUsdt = $isLong
            ? ($exitPrice - $avgCost) * $cumCoins
            : ($avgCost - $exitPrice) * $cumCoins;

        $feeUsdt = ($cumEntryVal + ($exitPrice * $cumCoins)) * $feePct;
        $netPnlUsdt = $rawPnlUsdt - $feeUsdt;
        $pnlPct = $cumMargin > 0 ? ($netPnlUsdt / $cumMargin) * 100.0 : 0.0;

        $trades[] = [
            'entryIdx'    => $i,
            'exitIdx'     => $exitIdx,
            'duration'    => $exitIdx - $i + 1,
            'nEntries'    => $filledCount,
            'avgCost'     => $avgCost,
            'exitPrice'   => $exitPrice,
            'netPnlUsdt'  => $netPnlUsdt,
            'pnlPct'      => $pnlPct,
            'hitTp'       => $hitTp,
            'hitSl'       => $hitSl,
            'liquidated'  => $liquidated,
            'cumMargin'   => $cumMargin
        ];

        // Advance to next cycle (non-overlapping step)
        $i = max($i + $stepInterval, $exitIdx + 1);
    }

    // Summary Statistics
    $totalTrades = count($trades);
    if ($totalTrades === 0) {
        echo json_encode(['status' => 'ok', 'summary' => ['totalTrades' => 0]]);
        exit;
    }

    $wins = 0;
    $losses = 0;
    $liquidations = 0;
    $totalPnlPct = 0.0;
    $totalPnlUsdt = 0.0;
    $pnls = [];
    $totalEntries = 0;
    $totalDuration = 0;
    $maxMarginUsed = 0.0;

    foreach ($trades as $t) {
        if ($t['liquidated']) {
            $liquidations++;
            $losses++;
        } elseif ($t['netPnlUsdt'] > 0) {
            $wins++;
        } else {
            $losses++;
        }
        $totalPnlPct += $t['pnlPct'];
        $totalPnlUsdt += $t['netPnlUsdt'];
        $pnls[] = $t['pnlPct'];
        $totalEntries += $t['nEntries'];
        $totalDuration += $t['duration'];
        if ($t['cumMargin'] > $maxMarginUsed) $maxMarginUsed = $t['cumMargin'];
    }

    $winRate = ($wins / $totalTrades) * 100.0;
    $avgPnlPct = $totalPnlPct / $totalTrades;

    // Sharpe Ratio
    $meanPnl = $avgPnlPct;
    $variance = 0.0;
    foreach ($pnls as $p) {
        $variance += pow($p - $meanPnl, 2);
    }
    $stdPnl = $totalTrades > 1 ? sqrt($variance / ($totalTrades - 1)) : 0.0;
    $sharpe = $stdPnl > 0 ? ($meanPnl / $stdPnl) : 0.0;

    // Max Drawdown %
    $cum = 0.0;
    $peak = 0.0;
    $maxDd = 0.0;
    foreach ($pnls as $p) {
        $cum += $p;
        if ($cum > $peak) $peak = $cum;
        $dd = $peak - $cum;
        if ($dd > $maxDd) $maxDd = $dd;
    }

    echo json_encode([
        'status'  => 'ok',
        'symbol'  => $symbol,
        'coin'    => $coin,
        'candles' => $nCandles,
        'summary' => [
            'calcMode'      => $calcMode,
            'deposit'       => $deposit,
            'totalTrades'   => $totalTrades,
            'wins'          => $wins,
            'losses'        => $losses,
            'liquidations'  => $liquidations,
            'winRate'       => round($winRate, 2),
            'totalPnlPct'   => round($totalPnlPct, 2),
            'totalPnlUsdt'  => round($totalPnlUsdt, 2),
            'avgPnlPct'     => round($avgPnlPct, 2),
            'maxDrawdown'   => round($maxDd, 2),
            'sharpeRatio'   => round($sharpe, 2),
            'avgEntries'    => round($totalEntries / $totalTrades, 2),
            'avgHoldCandles'=> round($totalDuration / $totalTrades, 1),
            'maxMarginUsed' => round($maxMarginUsed, 2),
        ],
        'trades'  => array_slice($trades, -50) // last 50 trades
    ]);
    exit;
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backtest Custom DCA Bot — PHP Strategy Tester</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg: #0f1117;
      --surface: #1a1d2b;
      --surface2: #242838;
      --border: #2d3148;
      --text: #e2e4eb;
      --text2: #8b8fa7;
      --accent: #5b7aff;
      --green: #3dd68c;
      --red: #f6465d;
      --yellow: #f0b90b;
      --radius: 8px;
      --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      padding: 20px;
      min-height: 100vh;
    }
    .container { max-width: 1350px; margin: 0 auto; }
    
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }
    h1 { font-size: 22px; font-weight: 600; }
    h1 span { color: var(--accent); }
    .badge {
      background: rgba(91,122,255,0.15);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      border: 1px solid rgba(91,122,255,0.3);
    }

    .main-grid {
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 20px;
    }
    @media (max-width: 1024px) {
      .main-grid { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px;
      margin-bottom: 20px;
    }

    .card-title {
      font-size: 14px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      margin-bottom: 14px;
      display: flex;
      justify-content: space-between;
    }

    .form-group { margin-bottom: 10px; display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .form-group label { font-size: 11px; color: var(--text2); font-weight: 600; text-transform: uppercase; white-space: nowrap; }
    .form-group input, .form-group select {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 13px;
      color: var(--text);
      outline: none;
      width: 100%;
      box-sizing: border-box;
      min-width: 0;
    }
    .form-group input:focus, .form-group select:focus { border-color: var(--accent); }
    .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; min-width: 0; }
    .form-row.single { grid-template-columns: 1fr; }

    .btn {
      background: var(--accent);
      color: #fff;
      border: none;
      border-radius: 6px;
      padding: 10px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      width: 100%;
      margin-top: 10px;
      transition: opacity 0.2s;
    }
    .btn:hover { opacity: 0.9; }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .summary-card {
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
    }
    .summary-card .label { font-size: 11px; color: var(--text2); text-transform: uppercase; }
    .summary-card .val { font-size: 18px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
    .summary-card .val.green { color: var(--green); }
    .summary-card .val.red { color: var(--red); }
    .summary-card .val.accent { color: var(--accent); }

    .table-container {
      overflow-x: auto;
      max-height: 450px;
      overflow-y: auto;
      border-radius: 6px;
      border: 1px solid var(--border);
    }
    table { width: 100%; border-collapse: collapse; font-size: 12px; }
    thead th {
      background: var(--surface2);
      padding: 10px 12px;
      color: var(--text2);
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      text-align: right;
      position: sticky;
      top: 0;
      z-index: 2;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    thead th:first-child { text-align: center; }
    tbody td {
      padding: 8px 12px;
      text-align: right;
      border-bottom: 1px solid rgba(45,49,72,0.4);
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    tbody td:first-child { text-align: center; color: var(--text2); }
    tbody tr:hover { background: rgba(91,122,255,0.05); }

    .loader {
      display: inline-block;
      width: 14px;
      height: 14px;
      border: 2px solid rgba(255,255,255,0.3);
      border-radius: 50%;
      border-top-color: #fff;
      animation: spin 0.8s linear infinite;
    }
    @keyframes spin { to { transform: rotate(360deg); } }
  </style>
</head>
<body>

<div class="container">
  <header>
    <div>
      <h1><span>PHP</span> Backtest Custom DCA Bot</h1>
      <div style="font-size: 12px; color: var(--text2); margin-top: 2px;">
        Симуляция прибыльности стратегии кастомного бота на истории свечей Bybit
      </div>
    </div>
    <div class="badge">PHP Strategy Backtester</div>
  </header>

  <div class="main-grid">
    <!-- Form Settings -->
    <div class="card">
      <div class="card-title">
        <span>Параметры теста</span>
        <span id="loadingSpinner" style="display:none"><span class="loader"></span></span>
      </div>

      <form id="backtestForm" onsubmit="runBacktest(event)">
        <div class="form-row">
          <div class="form-group">
            <label>Монета</label>
            <select id="coin">
              <option value="HYPE" selected>HYPE / USDT</option>
              <option value="UNI">UNI / USDT</option>
              <option value="SOL">SOL / USDT</option>
              <option value="ETH">ETH / USDT</option>
              <option value="LINK">LINK / USDT</option>
              <option value="MNT">MNT / USDT</option>
            </select>
          </div>
          <div class="form-group">
            <label>Направление</label>
            <select id="direction">
              <option value="long" selected>Long (Покупка)</option>
              <option value="short">Short (Продажа)</option>
            </select>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>Период истории</label>
            <select id="days">
              <option value="3">3 дня</option>
              <option value="7">7 дней</option>
              <option value="14">14 дней</option>
              <option value="21">21 день</option>
              <option value="30">30 дней</option>
              <option value="90" selected>90 дней</option>
              <option value="180">180 дней</option>
              <option value="365">365 дней</option>
            </select>
          </div>
          <div class="form-group">
            <label>Таймфрейм</label>
            <select id="interval">
              <option value="D">1 день (D)</option>
              <option value="240">4 часа (4h)</option>
              <option value="60" selected>1 час (1h)</option>
              <option value="15">15 минут (15m)</option>
            </select>
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>Режим расчета</label>
            <select id="calcMode" onchange="toggleCalcMode()">
              <option value="deposit" selected>Депозит (USDT)</option>
              <option value="qty">1-й объем (в монетах)</option>
            </select>
          </div>
          <div class="form-group" id="depositGroup">
            <label>Депозит (USDT)</label>
            <input type="number" id="deposit" value="500" step="10" min="10">
          </div>
          <div class="form-group" id="baseQtyGroup" style="display:none;">
            <label>1-й объем (base_qty)</label>
            <input type="number" id="baseQty" value="0.19" step="0.001" min="0.0001">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>Плечо (x)</label>
            <input type="number" id="leverage" value="1" min="1" max="100">
          </div>
          <div class="form-group">
            <label>Safety ордеров</label>
            <input type="number" id="maxOrders" value="7" min="1" max="20">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>% перекрытия (%)</label>
            <input type="number" id="coveragePct" value="2.28" step="0.01">
          </div>
          <div class="form-group">
            <label>Кэф. шага цены</label>
            <input type="number" id="priceScale" value="1.0228" step="0.0001">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>Кэф. объема (маржи)</label>
            <input type="number" id="multiplier" value="1.10" step="0.01">
          </div>
          <div class="form-group">
            <label>Take Profit (%)</label>
            <input type="number" id="targetProfit" value="1.0" step="0.1">
          </div>
        </div>

        <div class="form-row">
          <div class="form-group">
            <label>Stop Loss (%)</label>
            <input type="number" id="stopLoss" value="20" step="1">
          </div>
        </div>

        <button type="submit" class="btn">🚀 Запустить бэктест</button>
      </form>
    </div>

    <!-- Results Overview -->
    <div>
      <div class="summary-grid" id="summaryContainer">
        <div class="summary-card">
          <div class="label">Всего сделок</div>
          <div class="val" id="resTrades">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Win Rate</div>
          <div class="val accent" id="resWinRate">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Общий PnL (%)</div>
          <div class="val green" id="resTotalPnl">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Общий PnL (USDT)</div>
          <div class="val green" id="resTotalUsdt">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Max Drawdown</div>
          <div class="val red" id="resMaxDd">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Sharpe Ratio</div>
          <div class="val" id="resSharpe">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Ликвидаций</div>
          <div class="val red" id="resLiqs">—</div>
        </div>
        <div class="summary-card">
          <div class="label">Сред. исполн. ордеров</div>
          <div class="val" id="resAvgEntries">—</div>
        </div>
      </div>

      <div class="card">
        <div class="card-title">История последних сделок (Backtest Log)</div>
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Ордеров</th>
                <th>Сред. цена</th>
                <th>Цена выхода</th>
                <th>Свечей удержания</th>
                <th>Результат</th>
                <th>PnL USDT</th>
                <th>PnL %</th>
              </tr>
            </thead>
            <tbody id="tradesBody">
              <tr><td colspan="8" style="text-align:center; color: var(--text2);">Нажмите "Запустить бэктест" для расчета</td></tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
function toggleCalcMode() {
  const mode = document.getElementById('calcMode').value;
  const depositGrp = document.getElementById('depositGroup');
  const baseQtyGrp = document.getElementById('baseQtyGroup');
  if (mode === 'deposit') {
    depositGrp.style.display = 'flex';
    baseQtyGrp.style.display = 'none';
  } else {
    depositGrp.style.display = 'none';
    baseQtyGrp.style.display = 'flex';
  }
}

async function runBacktest(e) {
  if (e) e.preventDefault();

  const spinner = document.getElementById('loadingSpinner');
  spinner.style.display = 'inline-block';

  const params = new URLSearchParams({
    api: 'run_backtest',
    coin: document.getElementById('coin').value,
    direction: document.getElementById('direction').value,
    days: document.getElementById('days').value,
    interval: document.getElementById('interval').value,
    calcMode: document.getElementById('calcMode').value,
    deposit: document.getElementById('deposit').value,
    baseQty: document.getElementById('baseQty').value,
    leverage: document.getElementById('leverage').value,
    maxOrders: document.getElementById('maxOrders').value,
    coveragePct: document.getElementById('coveragePct').value,
    priceScale: document.getElementById('priceScale').value,
    multiplier: document.getElementById('multiplier').value,
    targetProfit: document.getElementById('targetProfit').value,
    stopLoss: document.getElementById('stopLoss').value,
  });

  try {
    const res = await fetch('?' + params.toString());
    const data = await res.json();

    if (data.status === 'ok') {
      const s = data.summary;
      document.getElementById('resTrades').textContent = s.totalTrades;
      document.getElementById('resWinRate').textContent = s.winRate + '%';
      document.getElementById('resTotalPnl').textContent = (s.totalPnlPct > 0 ? '+' : '') + s.totalPnlPct + '%';
      document.getElementById('resTotalUsdt').textContent = (s.totalPnlUsdt > 0 ? '+' : '') + s.totalPnlUsdt + ' USDT';
      document.getElementById('resMaxDd').textContent = s.maxDrawdown + '%';
      document.getElementById('resSharpe').textContent = s.sharpeRatio;
      document.getElementById('resLiqs').textContent = s.liquidations;
      document.getElementById('resAvgEntries').textContent = s.avgEntries;

      let html = '';
      data.trades.forEach((t, idx) => {
        const resText = t.liquidated ? '<span style="color:var(--red);font-weight:600;">LIQUIDATED</span>' :
                        (t.hitTp ? '<span style="color:var(--green);">TP Hit</span>' :
                        (t.hitSl ? '<span style="color:var(--red);">SL Hit</span>' : 'Closed'));

        html += `<tr>
          <td>${idx + 1}</td>
          <td>${t.nEntries}</td>
          <td>$${t.avgCost.toFixed(4)}</td>
          <td>$${t.exitPrice.toFixed(4)}</td>
          <td>${t.duration}</td>
          <td>${resText}</td>
          <td style="color:${t.netPnlUsdt >= 0 ? 'var(--green)' : 'var(--red)'}; font-weight:600;">${t.netPnlUsdt >= 0 ? '+' : ''}${t.netPnlUsdt.toFixed(2)} $</td>
          <td style="color:${t.pnlPct >= 0 ? 'var(--green)' : 'var(--red)'};">${t.pnlPct >= 0 ? '+' : ''}${t.pnlPct.toFixed(2)}%</td>
        </tr>`;
      });
      document.getElementById('tradesBody').innerHTML = html;
    } else {
      alert('Ошибка бэктеста: ' + data.message);
    }
  } catch(err) {
    console.error(err);
    alert('Ошибка сети или сервера.');
  } finally {
    spinner.style.display = 'none';
  }
}

// Initial run on page load
runBacktest();
</script>

</body>
</html>
