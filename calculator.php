<?php
/**
 * Bybit / Multi-Coin Martingale DCA Bot Calculator
 * Works on simple PHP web hosting (PHP 7.4+ or PHP 8.x)
 * No database or extra extensions required (uses cURL or file_get_contents).
 */

// --- BACKEND API HANDLER ---
if (isset($_GET['api'])) {
    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');

    $action = $_GET['api'];
    
    // Allowed coins mapping to Bybit Linear Futures symbols
    $allowedCoins = [
        'UNI'  => 'UNIUSDT',
        'HYPE' => 'HYPEUSDT',
        'SOL'  => 'SOLUSDT',
        'LINK' => 'LINKUSDT',
        'ETH'  => 'ETHUSDT',
        'MNT'  => 'MNTUSDT',
        'GRAM' => 'GRAMUSDT',
    ];

    if ($action === 'coins') {
        echo json_encode(['status' => 'ok', 'coins' => array_keys($allowedCoins)]);
        exit;
    }

    if ($action === 'history') {
        $coin = strtoupper($_GET['coin'] ?? 'SOL');
        $days = (int)($_GET['days'] ?? 90);

        if (!isset($allowedCoins[$coin])) {
            echo json_encode(['status' => 'error', 'message' => 'Неподдерживаемая монета']);
            exit;
        }

        $symbol = $allowedCoins[$coin];
        $limit = min(max($days, 1), 1000);

        // Fetch daily klines from Bybit V5 Public API
        $url = "https://api.bybit.com/v5/market/kline?category=linear&symbol={$symbol}&interval=D&limit={$limit}";

        $json = @file_get_contents($url);
        if (!$json) {
            // Fallback via cURL if file_get_contents disabled by hosting provider
            $ch = curl_init();
            curl_setopt($ch, CURLOPT_URL, $url);
            curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
            curl_setopt($ch, CURLOPT_TIMEOUT, 5);
            curl_setopt($ch, CURLOPT_USERAGENT, 'Mozilla/5.0');
            $json = curl_exec($ch);
            curl_close($ch);
        }

        $data = json_decode($json, true);
        if (($data['retCode'] ?? -1) !== 0 || empty($data['result']['list'])) {
            echo json_encode(['status' => 'error', 'message' => 'Не удалось получить данные с биржи']);
            exit;
        }

        // Bybit format: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        $list = $data['result']['list'];
        $candles = [];
        $highMax = -INF;
        $lowMin = INF;

        foreach ($list as $k) {
            $time = (int)$k[0];
            $open = (float)$k[1];
            $high = (float)$k[2];
            $low = (float)$k[3];
            $close = (float)$k[4];
            $vol = (float)$k[5];

            if ($high > $highMax) $highMax = $high;
            if ($low < $lowMin) $lowMin = $low;

            $candles[] = [
                'time'  => $time,
                'open'  => $open,
                'high'  => $high,
                'low'   => $low,
                'close' => $close,
                'vol'   => $vol
            ];
        }

        // Latest current price
        $currentPrice = (float)($list[0][4] ?? 0);

        echo json_encode([
            'status'       => 'ok',
            'symbol'       => $symbol,
            'coin'         => $coin,
            'currentPrice' => $currentPrice,
            'highMax'      => $highMax,
            'lowMin'       => $lowMin,
            'days'         => count($candles),
            'candles'      => array_reverse($candles) // Chronological order
        ]);
        exit;
    }

    echo json_encode(['status' => 'error', 'message' => 'Неизвестное действие']);
    exit;
}
?>
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Калькулятор Futures Martingale DCA Bot (PHP)</title>
  <!-- Chart.js for history preview -->
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
      --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
    }
    * { margin:0; padding:0; box-sizing:border-box; }
    body {
      font-family: var(--font);
      background: var(--bg);
      color: var(--text);
      padding: 20px;
      min-height: 100vh;
    }
    .container { max-width: 1400px; margin: 0 auto; }
    
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
      flex-wrap: wrap;
      gap: 10px;
    }
    h1 { font-size: 22px; font-weight: 600; color: var(--text); }
    h1 span { color: var(--accent); }
    .badge {
      background: rgba(91,122,255,0.15);
      color: var(--accent);
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 12px;
      font-weight: 600;
      border: 1px solid rgba(91,122,255,0.3);
    }

    /* Grid Layout */
    .top-section {
      display: grid;
      grid-template-columns: 340px 1fr;
      gap: 20px;
      margin-bottom: 20px;
    }
    @media (max-width: 1024px) {
      .top-section { grid-template-columns: 1fr; }
    }

    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 18px;
    }

    .card-title {
      font-size: 14px;
      font-weight: 600;
      color: var(--text);
      margin-bottom: 14px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    /* Inputs Form */
    .form-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    .form-grid.full { grid-template-columns: 1fr; }
    .input-group { display: flex; flex-direction: column; gap: 4px; }
    .input-group label {
      font-size: 11px;
      color: var(--text2);
      font-weight: 600;
      text-transform: uppercase;
    }
    .input-group input, .input-group select {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px 10px;
      font-size: 14px;
      color: var(--text);
      outline: none;
      transition: border-color 0.15s;
      width: 100%;
    }
    .input-group input:focus, .input-group select:focus { border-color: var(--accent); }
    .input-group .hint { font-size: 10px; color: var(--text2); }

    /* Market Info & Chart */
    .market-stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .stat-box {
      background: var(--surface2);
      border: 1px solid var(--border);
      padding: 10px;
      border-radius: 6px;
    }
    .stat-box .title { font-size: 10px; color: var(--text2); text-transform: uppercase; }
    .stat-box .val { font-size: 15px; font-weight: 600; margin-top: 2px; }

    .chart-container {
      position: relative;
      height: 420px;
      width: 100%;
    }

    /* Summary Cards */
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }
    .summary-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px 14px;
    }
    .summary-card .label { font-size: 11px; color: var(--text2); text-transform: uppercase; font-weight: 600; }
    .summary-card .value { font-size: 18px; font-weight: 700; margin-top: 4px; font-variant-numeric: tabular-nums; }
    .summary-card .value.green { color: var(--green); }
    .summary-card .value.red { color: var(--red); }
    .summary-card .value.accent { color: var(--accent); }
    .summary-card .sub { font-size: 11px; color: var(--text2); margin-top: 2px; }

    /* Orders Table */
    .table-wrap {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      overflow: hidden;
    }
    .table-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 12px 18px;
      border-bottom: 1px solid var(--border);
    }
    .table-scroll {
      overflow-x: auto;
      max-height: 550px;
      overflow-y: auto;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    thead th {
      background: var(--surface2);
      padding: 10px 12px;
      font-weight: 600;
      color: var(--text2);
      font-size: 11px;
      text-transform: uppercase;
      position: sticky;
      top: 0;
      z-index: 1;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    tbody td {
      padding: 8px 12px;
      text-align: right;
      font-variant-numeric: tabular-nums;
      border-bottom: 1px solid rgba(45,49,72,0.4);
      white-space: nowrap;
    }
    tbody td:first-child { text-align: center; color: var(--text2); }
    tbody td:nth-child(2) { text-align: left; font-weight: 600; }
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
      <h1><span>PHP</span> DCA Martingale Bot Calculator</h1>
      <div style="font-size: 12px; color: var(--text2); margin-top: 2px;">
        Калькулятор сетки и риск-менеджмента для фьючерсных ботов Bybit
      </div>
    </div>
    <div class="badge">Standalone PHP Host</div>
  </header>

  <div class="top-section">
    <!-- Sidebar / Settings -->
    <div class="card">
      <div class="card-title">
        <span>Настройки Бота</span>
        <span id="loadingSpinner" style="display:none"><span class="loader"></span></span>
      </div>

      <div class="form-grid full" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Тип расчета бота</label>
          <select id="botMode" onchange="toggleBotMode()">
            <option value="bybit">Bybit Futures Martingale (Фикс. депозит USDT)</option>
            <option value="custom">Custom DCA Bot (Фикс. base_qty в монетах)</option>
          </select>
        </div>
      </div>

      <div class="form-grid full" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Пресеты настроек</label>
          <div style="display:flex; gap:6px;">
            <button type="button" onclick="loadHypeFastPreset()" style="background:var(--surface2); color:var(--text); border:1px solid var(--border); padding:6px 10px; border-radius:6px; font-size:12px; cursor:pointer; flex:1;">
              ⚡ HYPE Calibrated Fast (8 ord)
            </button>
          </div>
        </div>
      </div>

      <div class="form-grid full" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Выберите монету</label>
          <select id="coinSelect" onchange="fetchHistory()">
            <option value="UNI">UNI / USDT</option>
            <option value="HYPE" selected>HYPE / USDT</option>
            <option value="SOL">SOL / USDT</option>
            <option value="LINK">LINK / USDT</option>
            <option value="ETH">ETH / USDT</option>
            <option value="MNT">MNT / USDT</option>
            <option value="GRAM">GRAM / USDT</option>
          </select>
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Период истории</label>
          <select id="daysSelect" onchange="fetchHistory()">
            <option value="3">3 дня</option>
            <option value="7">7 дней</option>
            <option value="14">14 дней</option>
            <option value="21">21 день</option>
            <option value="30">30 дней</option>
            <option value="90">90 дней</option>
            <option value="180">180 дней</option>
            <option value="365" selected>365 дней</option>
            <option value="540">540 дней</option>
          </select>
        </div>
        <div class="input-group">
          <label>Режим цены входа</label>
          <select id="entryMode" onchange="toggleEntryMode()">
            <option value="auto">Авто (Текущая)</option>
            <option value="manual">Вручную</option>
          </select>
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Цена входа ($)</label>
          <input type="number" id="entryPrice" value="100" step="any" readonly>
        </div>
        <div class="input-group">
          <label>Направление</label>
          <select id="direction">
            <option value="long">Long (Покупка)</option>
            <option value="short">Short (Продажа)</option>
          </select>
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Депозит (USDT)</label>
          <input type="number" id="investment" value="500" step="10">
        </div>
        <div class="input-group">
          <label>Плечо (x)</label>
          <input type="number" id="leverage" value="1" min="1" max="100">
        </div>
      </div>

      <div class="form-grid" id="baseQtyGroup" style="display:none; margin-bottom: 10px;">
        <div class="input-group">
          <label>Тип 1-го объема</label>
          <select id="baseQtyType" onchange="toggleBaseQtyType()">
            <option value="percent" selected>% от депозита</option>
            <option value="coins">В монетах (units)</option>
          </select>
        </div>
        <div class="input-group">
          <label id="baseQtyLabel">1-й объем (% от депозита)</label>
          <input type="number" id="baseQty" value="2.0" step="0.01">
          <div class="hint" id="baseQtyHint">Размер 1-го ордера в % от депозита</div>
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Кол-во safety ордеров</label>
          <input type="number" id="maxOrders" value="6" min="1" max="50">
          <div class="hint">Не считая 1-й (всего = +1)</div>
        </div>
        <div class="input-group">
          <label id="priceDevLabel">Шаг цены (%)</label>
          <input type="number" id="priceDeviation" value="2" step="0.01">
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Кэф. шага цены</label>
          <input type="number" id="priceScale" value="1.0" step="0.1" min="1">
        </div>
        <div class="input-group">
          <label>Кэф. объема (маржи)</label>
          <input type="number" id="multiplier" value="1.5" step="0.1" min="1">
        </div>
      </div>

      <div class="form-grid" style="margin-bottom: 10px;">
        <div class="input-group">
          <label>Режим Take Profit</label>
          <select id="tpType">
            <option value="investment">% от депозита</option>
            <option value="price">% от сред. цены</option>
          </select>
        </div>
        <div class="input-group">
          <label>Профит (%)</label>
          <input type="number" id="targetProfit" value="3" step="0.1">
        </div>
      </div>

      <div class="form-grid">
        <div class="input-group">
          <label>Стоп-лосс (%)</label>
          <input type="number" id="stopLoss" value="20" step="1">
        </div>
        <div class="input-group">
          <label>MMR (%)</label>
          <input type="number" id="mmr" value="0.5" step="0.1">
        </div>
      </div>
    </div>

    <!-- Main Chart & History Card -->
    <div class="card">
      <div class="card-title">
        <span id="chartTitle">График монеты & Исторический диапазон</span>
        <span id="marketStatus" style="font-size: 11px; color: var(--text2); font-weight: normal;"></span>
      </div>

      <div class="market-stats">
        <div class="stat-box">
          <div class="title">Текущая цена</div>
          <div class="val" id="statCurrentPrice">—</div>
        </div>
        <div class="stat-box">
          <div class="title">Макс за период</div>
          <div class="val" style="color: var(--green);" id="statHigh">—</div>
        </div>
        <div class="stat-box">
          <div class="title">Мин за период</div>
          <div class="val" style="color: var(--red);" id="statLow">—</div>
        </div>
        <div class="stat-box">
          <div class="title">Кол-во свечей</div>
          <div class="val" id="statCandles">—</div>
        </div>
      </div>

      <div class="chart-container">
        <canvas id="priceChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Summary Cards -->
  <div class="summary-grid" id="summaryGrid"></div>

  <!-- Detailed Table -->
  <div class="table-wrap">
    <div class="table-header">
      <div style="font-weight: 600;">Таблица исполнения сетки ордеров</div>
      <div style="font-size: 12px; color: var(--text2);">Расчет по формулам Bybit Futures Martingale Bot</div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Цена ордера</th>
            <th>Δ от входа %</th>
            <th>Маржа USDT</th>
            <th>Монеты</th>
            <th>Номинал USDT</th>
            <th>Маржа всего</th>
            <th>Монет всего</th>
            <th>Сред. цена</th>
            <th>Цена ликв.</th>
            <th>До ликв. %</th>
            <th>Цена TP</th>
            <th>Профит USDT</th>
            <th>ROI %</th>
          </tr>
        </thead>
        <tbody id="tableBody"></tbody>
      </table>
    </div>
  </div>
</div>

<script>
let marketData = null;
let priceChart = null;
let currentGridRows = [];

const inputIds = [
  'entryPrice', 'investment', 'leverage', 'maxOrders', 'priceDeviation',
  'priceScale', 'multiplier', 'targetProfit', 'stopLoss', 'mmr',
  'direction', 'tpType', 'baseQty', 'baseQtyType'
];

function toggleEntryMode() {
  const mode = document.getElementById('entryMode').value;
  const entryInput = document.getElementById('entryPrice');
  if (mode === 'manual') {
    entryInput.removeAttribute('readonly');
    entryInput.focus();
  } else {
    entryInput.setAttribute('readonly', 'true');
    if (marketData && marketData.currentPrice) {
      entryInput.value = marketData.currentPrice;
    }
  }
  calculateAndRender();
}

function toggleBaseQtyType() {
  const type = document.getElementById('baseQtyType') ? document.getElementById('baseQtyType').value : 'percent';
  const labelEl = document.getElementById('baseQtyLabel');
  const hintEl = document.getElementById('baseQtyHint');
  if (type === 'percent') {
    if (labelEl) labelEl.textContent = '1-й объем (% от депозита)';
    if (hintEl) hintEl.textContent = '% от депозита на 1-й ордер';
  } else {
    if (labelEl) labelEl.textContent = '1-й объем (base_qty в монетах)';
    if (hintEl) hintEl.textContent = 'Размер 1-го ордера в монетах';
  }
  calculateAndRender();
}

async function fetchHistory() {
  const coin = document.getElementById('coinSelect').value;
  const days = document.getElementById('daysSelect').value;
  const spinner = document.getElementById('loadingSpinner');

  spinner.style.display = 'inline-block';
  try {
    const resp = await fetch(`?api=history&coin=${coin}&days=${days}`);
    const data = await resp.json();

    if (data.status === 'ok') {
      marketData = data;
      document.getElementById('statCurrentPrice').textContent = '$' + fmt(data.currentPrice, 4);
      document.getElementById('statHigh').textContent = '$' + fmt(data.highMax, 4);
      document.getElementById('statLow').textContent = '$' + fmt(data.lowMin, 4);
      document.getElementById('statCandles').textContent = data.days + ' дн.';

      if (document.getElementById('entryMode').value === 'auto') {
        document.getElementById('entryPrice').value = data.currentPrice;
      }

      calculateAndRender();
    } else {
      alert('Ошибка API: ' + data.message);
    }
  } catch (e) {
    console.error('Fetch error:', e);
  } finally {
    spinner.style.display = 'none';
  }
}

function renderChart(data, gridRows = []) {
  const ctx = document.getElementById('priceChart').getContext('2d');
  currentGridRows = gridRows;
  
  const labels = data.candles.map(c => {
    const d = new Date(c.time);
    return (d.getMonth() + 1) + '/' + d.getDate();
  });
  const prices = data.candles.map(c => c.close);
  const nPoints = prices.length;

  const datasets = [{
    label: `${data.coin}/USDT Цена закрытия`,
    data: prices,
    borderColor: '#5b7aff',
    backgroundColor: 'rgba(91, 122, 255, 0.08)',
    fill: true,
    borderWidth: 2,
    pointRadius: 0,
    tension: 0.1
  }];

  if (gridRows && gridRows.length > 0) {
    // Add Red entry lines for each order
    gridRows.forEach((r, idx) => {
      datasets.push({
        label: r.level === 0 ? 'Вход (Старт)' : `Ордер #${r.level}`,
        data: Array(nPoints).fill(r.price),
        borderColor: '#f6465d', // Red color for entry orders
        borderWidth: r.level === 0 ? 2 : 1,
        borderDash: r.level === 0 ? [] : [4, 4],
        pointRadius: 0,
        fill: false
      });

      // Add Blue TP lines for each order stage
      datasets.push({
        label: `TP #${r.level} ($${r.tpPrice.toFixed(4)})`,
        data: Array(nPoints).fill(r.tpPrice),
        borderColor: '#3a86ff', // Blue color for TP target lines
        borderWidth: 1,
        borderDash: [2, 2],
        pointRadius: 0,
        fill: false
      });
    });
  }

  if (priceChart) {
    priceChart.destroy();
  }

  priceChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: datasets
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          position: 'top',
          labels: {
            color: '#8b8fa7',
            font: { size: 10 },
            filter: function(item) {
              // Limit legend clutter: show price line, main start entry line and last TP line
              return item.text.includes('Цена') || item.text.includes('Старт') || item.text.includes('TP #0') || item.text.includes(`TP #${gridRows.length-1}`);
            }
          }
        },
        tooltip: {
          mode: 'index',
          intersect: false
        }
      },
      scales: {
        x: {
          grid: { color: 'rgba(45,49,72,0.3)' },
          ticks: { color: '#8b8fa7', maxTicksLimit: 10 }
        },
        y: {
          grid: { color: 'rgba(45,49,72,0.3)' },
          ticks: { color: '#8b8fa7' }
        }
      }
    }
  });
}

function toggleBotMode() {
  const botMode = document.getElementById('botMode').value;
  const labelEl = document.getElementById('priceDevLabel');
  if (botMode === 'custom') {
    document.getElementById('baseQtyGroup').style.display = 'grid';
    if (labelEl) labelEl.textContent = '% перекрытия (%)';
    toggleBaseQtyType();
  } else {
    document.getElementById('baseQtyGroup').style.display = 'none';
    if (labelEl) labelEl.textContent = 'Шаг цены (%)';
  }
  calculateAndRender();
}

function loadHypeFastPreset() {
  document.getElementById('botMode').value = 'custom';
  toggleBotMode();
  document.getElementById('coinSelect').value = 'HYPE';
  document.getElementById('direction').value = 'long';
  if (document.getElementById('baseQtyType')) {
    document.getElementById('baseQtyType').value = 'coins';
    toggleBaseQtyType();
  }
  document.getElementById('baseQty').value = 0.19;
  document.getElementById('leverage').value = 1;
  document.getElementById('maxOrders').value = 7; // 8 total orders (1 initial + 7 safety)
  document.getElementById('priceDeviation').value = 2.28;
  document.getElementById('priceScale').value = 1.0228;
  document.getElementById('multiplier').value = 1.10;
  document.getElementById('tpType').value = 'price';
  document.getElementById('targetProfit').value = 1.0;
  document.getElementById('stopLoss').value = 20;
  
  fetchHistory();
}

function calculateAndRender() {
  const botMode = document.getElementById('botMode').value;
  const direction = document.getElementById('direction').value;
  const entryPrice = +document.getElementById('entryPrice').value;
  const investment = +document.getElementById('investment').value;
  const baseQtyInput = +document.getElementById('baseQty').value;
  const baseQtyType = document.getElementById('baseQtyType') ? document.getElementById('baseQtyType').value : 'percent';
  const leverage = +document.getElementById('leverage').value;
  const maxOrders = +document.getElementById('maxOrders').value; // Safety orders count
  const priceDeviation = +document.getElementById('priceDeviation').value;
  const priceScale = +document.getElementById('priceScale').value || 1.0;
  const multiplier = +document.getElementById('multiplier').value;
  const tpType = document.getElementById('tpType').value;
  const targetProfit = +document.getElementById('targetProfit').value;
  const stopLoss = +document.getElementById('stopLoss').value;
  const mmr = (+document.getElementById('mmr').value || 0.5) / 100;

  if (!entryPrice || !leverage || !maxOrders) return;

  const isLong = direction === 'long';
  const N = maxOrders; 
  const totalOrders = N + 1; // 1 initial + N safety orders
  const m = multiplier;

  // Initial Margin formula
  let initMargin;
  if (botMode === 'bybit') {
    if (!investment) return;
    if (Math.abs(m - 1) < 1e-9) {
      initMargin = investment / totalOrders;
    } else {
      initMargin = investment * (m - 1) / (Math.pow(m, totalOrders) - 1);
    }
  }

  const rows = [];
  let totalCoins = 0;
  let totalMargin = 0;
  let totalEntryValue = 0;
  let prevPrice = entryPrice;

  // Determine step deviation per order
  // In custom mode, priceDeviation input is "% перекрытия" (total drop coverage)
  let baseStepDev = priceDeviation / 100;
  if (botMode === 'custom' && N > 0) {
    if (Math.abs(priceScale - 1) < 1e-6) {
      baseStepDev = (priceDeviation / 100) / N;
    } else {
      // Sum of geometric series for scale: scale^0 + scale^1 + ... + scale^(N-1) = (scale^N - 1) / (scale - 1)
      const sumScales = (Math.pow(priceScale, N) - 1) / (priceScale - 1);
      baseStepDev = (priceDeviation / 100) / sumScales;
    }
  }

  for (let i = 0; i < totalOrders; i++) {
    let price;
    if (i === 0) {
      price = entryPrice;
    } else {
      const currentStepDev = baseStepDev * Math.pow(priceScale, i - 1);
      price = isLong ? prevPrice * (1 - currentStepDev) : prevPrice * (1 + currentStepDev);
    }
    if (price <= 0) break;
    prevPrice = price;

    let coins, margin, notional;
    if (botMode === 'custom') {
      let firstOrderCoins;
      if (baseQtyType === 'percent') {
        const firstOrderUsdt = investment * (baseQtyInput / 100);
        firstOrderCoins = entryPrice > 0 ? firstOrderUsdt / entryPrice : 0;
      } else {
        firstOrderCoins = baseQtyInput;
      }
      coins = firstOrderCoins * Math.pow(m, i);
      notional = coins * price;
      margin = notional / leverage;
    } else {
      margin = initMargin * Math.pow(m, i);
      notional = margin * leverage;
      coins = notional / price;
    }

    totalCoins += coins;
    totalMargin += margin;
    totalEntryValue += coins * price;

    const avgCost = totalEntryValue / totalCoins;

    // Isolated Liquidation Price Calculation
    let liqPrice = 0;
    if (isLong) {
      liqPrice = (totalEntryValue - totalMargin) / (totalCoins * (1 - mmr));
    } else {
      liqPrice = (totalEntryValue + totalMargin) / (totalCoins * (1 + mmr));
    }
    if (liqPrice <= 0) liqPrice = 0;

    const priceChange = isLong
      ? (price / entryPrice - 1) * 100
      : (1 - price / entryPrice) * 100;

    const curDistToLiq = liqPrice > 0
      ? (isLong ? (price - liqPrice) / price * 100 : (liqPrice - price) / price * 100)
      : 0;

    // Take Profit Price & Profit
    const tpSign = isLong ? 1 : -1;
    let tpPrice, profitAtTp, roiOnInvestment;

    if (tpType === 'investment') {
      profitAtTp = investment * (targetProfit / 100);
      tpPrice = avgCost + tpSign * (profitAtTp / totalCoins);
      roiOnInvestment = targetProfit;
    } else {
      tpPrice = avgCost * (1 + tpSign * (targetProfit / 100));
      profitAtTp = totalCoins * Math.abs(tpPrice - avgCost);
      roiOnInvestment = investment > 0 ? (profitAtTp / investment) * 100 : 0;
    }

    rows.push({
      level: i,
      price,
      priceChange,
      margin,
      coins,
      notional,
      cumMargin: totalMargin,
      cumCoins: totalCoins,
      avgCost,
      liqPrice,
      curDistToLiq,
      tpPrice,
      profitAtTp,
      roiOnInvestment
    });
  }

  // Render Summary
  const last = rows[rows.length - 1];
  const stopLossUsdt = investment * stopLoss / 100;
  const slPrice = isLong
    ? Math.max(0, last.avgCost - stopLossUsdt / last.cumCoins)
    : last.avgCost + stopLossUsdt / last.cumCoins;

  document.getElementById('summaryGrid').innerHTML = `
    <div class="summary-card">
      <div class="label">Направление</div>
      <div class="value accent">${isLong ? 'Long' : 'Short'}</div>
      <div class="sub">${rows.length} ордеров (1+${maxOrders})</div>
    </div>
    <div class="summary-card">
      <div class="label">Общая Маржа</div>
      <div class="value accent">${fmt(last.cumMargin, 2)} USDT</div>
      <div class="sub">из ${fmt(investment, 2)} USDT депозита</div>
    </div>
    <div class="summary-card">
      <div class="label">Позиция</div>
      <div class="value">${fmt(last.cumCoins, 4)}</div>
      <div class="sub">номинал ${fmt(last.cumCoins * last.avgCost, 2)} USDT</div>
    </div>
    <div class="summary-card">
      <div class="label">Средняя цена</div>
      <div class="value">${fmt(last.avgCost, 4)}</div>
    </div>
    <div class="summary-card">
      <div class="label">Цена Ликвидации</div>
      <div class="value ${last.liqPrice > 0 ? 'red' : ''}">${last.liqPrice > 0 ? fmt(last.liqPrice, 4) : '—'}</div>
      <div class="sub">Запас: ${fmt(last.curDistToLiq, 2)}%</div>
    </div>
    <div class="summary-card">
      <div class="label">Цена Take Profit</div>
      <div class="value green">${fmt(last.tpPrice, 4)}</div>
      <div class="sub">Профит +${fmt(last.profitAtTp, 2)} USDT (${fmt(last.roiOnInvestment, 2)}%)</div>
    </div>
    <div class="summary-card">
      <div class="label">Цена Stop-Loss</div>
      <div class="value red">${fmt(slPrice, 4)}</div>
      <div class="sub">Убыток -${fmt(stopLossUsdt, 2)} USDT (-${stopLoss}%)</div>
    </div>
  `;

  // Render Table
  let html = '';
  rows.forEach(r => {
    html += `<tr>
      <td>${r.level === 0 ? 'Старт' : '#' + r.level}</td>
      <td style="font-weight:600;">$${fmt(r.price, 4)}</td>
      <td style="color:${r.priceChange < 0 ? 'var(--red)' : 'var(--green)'}">${r.priceChange > 0 ? '+' : ''}${fmt(r.priceChange, 2)}%</td>
      <td>${fmt(r.margin, 2)}</td>
      <td>${fmt(r.coins, 4)}</td>
      <td>${fmt(r.notional, 2)}</td>
      <td>${fmt(r.cumMargin, 2)}</td>
      <td>${fmt(r.cumCoins, 4)}</td>
      <td>$${fmt(r.avgCost, 4)}</td>
      <td style="color:var(--red);">$${r.liqPrice > 0 ? fmt(r.liqPrice, 4) : '—'}</td>
      <td>${r.curDistToLiq > 0 ? fmt(r.curDistToLiq, 2) + '%' : '—'}</td>
      <td style="color:var(--green);">$${fmt(r.tpPrice, 4)}</td>
      <td style="color:var(--green); font-weight:600;">+${fmt(r.profitAtTp, 2)}</td>
      <td style="color:var(--green);">${fmt(r.roiOnInvestment, 2)}%</td>
    </tr>`;
  });
  document.getElementById('tableBody').innerHTML = html;

  // Render chart with updated grid lines
  if (marketData) {
    renderChart(marketData, rows);
  }
}

function fmt(n, d = 2) {
  if (n === undefined || n === null || isNaN(n)) return '—';
  return Number(n).toLocaleString('ru-RU', { minimumFractionDigits: d, maximumFractionDigits: d });
}

// Add input event listeners for real-time recalculation
inputIds.forEach(id => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('input', calculateAndRender);
  if (el) el.addEventListener('change', calculateAndRender);
});

// Initial load
fetchHistory();
</script>

</body>
</html>
