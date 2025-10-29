(function () {
  const symbol = (window.STOCK_SYMBOL || '').trim().toUpperCase();
  const priceCanvas = document.getElementById('priceChart');
  const rsiCanvas = document.getElementById('rsiChart');
  const tfSelect = document.getElementById('tfSelect');
  const smaToggle = document.getElementById('smaToggle');
  const emaToggle = document.getElementById('emaToggle');
  const rsiToggle = document.getElementById('rsiToggle');
  const smaPeriodInput = document.getElementById('smaPeriod');
  const emaPeriodInput = document.getElementById('emaPeriod');
  const rsiPeriodInput = document.getElementById('rsiPeriod');
  const refreshBtn = document.getElementById('refreshBtn');
  const currentPriceEl = document.getElementById('currentPrice');
  const nameLine = document.getElementById('nameLine');
  const sectorLine = document.getElementById('sectorLine');
  const summaryEl = document.getElementById('longSummary');
  const siteLine = document.getElementById('siteLine');
  const newsList = document.getElementById('newsList');
  const notesBox = document.getElementById('notesBox');

  if (!symbol) return;

  let priceChart = null;
  let rsiChart = null;
  let series = []; // {t: Date, close: number}

  async function fetchJson(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`Request failed: ${res.status}`);
    return await res.json();
  }

  function toColorVar(name, fallback) {
    const s = getComputedStyle(document.documentElement);
    return s.getPropertyValue(name) || fallback;
  }

  // Technical indicators
  function computeSMA(values, period) {
    const out = new Array(values.length).fill(null);
    if (!Array.isArray(values) || period <= 1) return out;
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      sum += (v == null || isNaN(v)) ? 0 : v;
      if (i >= period) {
        const remove = values[i - period];
        sum -= (remove == null || isNaN(remove)) ? 0 : remove;
      }
      if (i >= period - 1) {
        out[i] = sum / period;
      }
    }
    return out;
  }

  function computeEMA(values, period) {
    const out = new Array(values.length).fill(null);
    if (!Array.isArray(values) || period <= 1) return out;
    const k = 2 / (period + 1);
    let prev = null;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (v == null || isNaN(v)) {
        out[i] = prev;
        continue;
      }
      if (prev == null) {
        // Seed EMA with first non-null value
        prev = v;
      } else {
        prev = v * k + prev * (1 - k);
      }
      out[i] = prev;
    }
    return out;
  }

  function computeRSI(values, period) {
    const out = new Array(values.length).fill(null);
    if (!Array.isArray(values) || period <= 1) return out;
    let avgGain = 0, avgLoss = 0;
    // Initialize with first period's gains/losses
    for (let i = 1; i <= period && i < values.length; i++) {
      const change = (values[i] ?? 0) - (values[i - 1] ?? 0);
      if (change > 0) avgGain += change; else avgLoss -= change;
    }
    avgGain /= period; avgLoss /= period;
    out[period] = (avgLoss === 0) ? 100 : 100 - (100 / (1 + (avgGain / avgLoss)));
    // Wilder's smoothing
    for (let i = period + 1; i < values.length; i++) {
      const change = (values[i] ?? 0) - (values[i - 1] ?? 0);
      const gain = Math.max(0, change);
      const loss = Math.max(0, -change);
      avgGain = (avgGain * (period - 1) + gain) / period;
      avgLoss = (avgLoss * (period - 1) + loss) / period;
      const rs = (avgLoss === 0) ? Infinity : (avgGain / avgLoss);
      out[i] = 100 - (100 / (1 + rs));
    }
    return out;
  }

  function buildDatasets() {
    const times = series.map(p => p.t);
    const closes = series.map(p => p.close);

    const datasets = [
      {
        label: 'Close',
        data: series.map(p => ({ x: p.t, y: p.close })),
        borderColor: toColorVar('--primary', '#1f77b4'),
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 0,
      },
    ];

    if (smaToggle.checked) {
      const n = Math.max(2, Math.min(400, Number(smaPeriodInput.value || 20)));
      const sma = computeSMA(closes, n);
      datasets.push({
        label: `SMA (${n})`,
        data: sma.map((v, i) => ({ x: times[i], y: v })),
        borderColor: '#888',
        backgroundColor: 'transparent',
        borderWidth: 1,
        pointRadius: 0,
      });
    }

    if (emaToggle.checked) {
      const n = Math.max(2, Math.min(400, Number(emaPeriodInput.value || 50)));
      const ema = computeEMA(closes, n);
      datasets.push({
        label: `EMA (${n})`,
        data: ema.map((v, i) => ({ x: times[i], y: v })),
        borderColor: '#d62728',
        backgroundColor: 'transparent',
        borderWidth: 1,
        pointRadius: 0,
      });
    }

    return datasets;
  }

  function ensurePriceChart() {
    if (priceChart) priceChart.destroy();
    priceChart = new Chart(priceCanvas.getContext('2d'), {
      type: 'line',
      data: { datasets: buildDatasets() },
      options: {
        animation: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { type: 'time', time: { unit: 'month' } },
          y: { ticks: { callback: (v) => Number(v).toFixed(2) } },
        },
        plugins: { legend: { display: true } },
      },
    });
  }

  function ensureRsiChart() {
    const show = rsiToggle.checked;
    rsiCanvas.style.display = show ? 'block' : 'none';
    if (!show) {
      if (rsiChart) { rsiChart.destroy(); rsiChart = null; }
      return;
    }
    const period = Math.max(2, Math.min(100, Number(rsiPeriodInput.value || 14)));
    const times = series.map(p => p.t);
    const closes = series.map(p => p.close);
    const rsi = computeRSI(closes, period);
    if (rsiChart) rsiChart.destroy();
    rsiChart = new Chart(rsiCanvas.getContext('2d'), {
      type: 'line',
      data: {
        datasets: [
          {
            label: `RSI (${period})`,
            data: rsi.map((v, i) => ({ x: times[i], y: v })),
            borderColor: '#9467bd',
            backgroundColor: 'transparent',
            borderWidth: 1,
            pointRadius: 0,
          },
        ],
      },
      options: {
        animation: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { type: 'time', time: { unit: 'month' } },
          y: { min: 0, max: 100 },
        },
        plugins: { legend: { display: true } },
      },
    });
  }

  async function loadHistory() {
    const interval = (tfSelect.value || 'daily');
    const data = await fetchJson(`/api/stock/${encodeURIComponent(symbol)}/history?interval=${encodeURIComponent(interval)}&years=4`);
    const prices = Array.isArray(data?.prices) ? data.prices : [];
    series = prices.map(p => ({ t: new Date(p.date), close: Number(p.close) }));
    ensurePriceChart();
    ensureRsiChart();
  }

  async function loadQuote() {
    try {
      const data = await fetchJson(`/api/stock/${encodeURIComponent(symbol)}/quote`);
      const cp = data?.current_price;
      if (cp != null && !isNaN(cp)) {
        currentPriceEl.textContent = `· $${Number(cp).toFixed(2)}`;
      } else {
        currentPriceEl.textContent = '· —';
      }
    } catch (_) {
      currentPriceEl.textContent = '· —';
    }
  }

  function fmtTs(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleString();
    } catch { return ''; }
  }

  async function loadNews() {
    newsList.innerHTML = '';
    try {
      const data = await fetchJson(`/api/stock/${encodeURIComponent(symbol)}/news?limit=8`);
      const items = Array.isArray(data?.news) ? data.news : [];
      if (items.length === 0) {
        newsList.innerHTML = '<li class="muted">No recent news available.</li>';
        return;
      }
      for (const it of items) {
        const li = document.createElement('li');
        li.className = 'news-item';
        const a = document.createElement('a');
        a.href = it.link || '#';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = it.title || '(no title)';
        const meta = document.createElement('div');
        meta.className = 'muted-small';
        meta.textContent = [it.publisher, fmtTs(it.published_ts)].filter(Boolean).join(' · ');
        li.appendChild(a);
        li.appendChild(meta);
        newsList.appendChild(li);
      }
    } catch (_) {
      newsList.innerHTML = '<li class="muted">Failed to load news.</li>';
    }
  }

  async function loadProfile() {
    nameLine.textContent = '—';
    sectorLine.textContent = '—';
    summaryEl.textContent = '—';
    siteLine.textContent = '';
    try {
      const p = await fetchJson(`/api/stock/${encodeURIComponent(symbol)}/profile`);
      const name = p.longName || p.shortName || symbol;
      nameLine.textContent = name;
      const sec = [p.sector, p.industry, p.exchange].filter(Boolean).join(' · ');
      sectorLine.textContent = sec || '—';
      const sum = p.longBusinessSummary || '';
      summaryEl.textContent = sum || '—';
      if (p.website) {
        siteLine.innerHTML = `<a href="${p.website}" target="_blank" rel="noopener noreferrer">${p.website}</a>`;
      }
    } catch (_) {
      // keep defaults
    }
  }

  async function loadLocalNotes() {
    try {
      const d = await fetchJson(`/api/stock/${encodeURIComponent(symbol)}/local`);
      const parts = [];
      if (d.initial_price != null && !isNaN(d.initial_price)) {
        parts.push(`Initial Price: $${Number(d.initial_price).toFixed(2)}`);
      }
      if (d.date_spotted) parts.push(`Spotted: ${d.date_spotted}`);
      if (d.date_bought) parts.push(`Bought: ${d.date_bought}`);
      if (d.reason) parts.push('', d.reason);
      notesBox.textContent = parts.join('\n');
    } catch (_) {
      notesBox.textContent = '—';
    }
  }

  function bindControls() {
    tfSelect.addEventListener('change', async () => {
      await loadHistory();
    });
    [smaToggle, emaToggle, smaPeriodInput, emaPeriodInput].forEach(el => {
      el.addEventListener('change', () => {
        ensurePriceChart();
      });
    });
    [rsiToggle, rsiPeriodInput].forEach(el => {
      el.addEventListener('change', () => {
        ensureRsiChart();
      });
    });
    refreshBtn.addEventListener('click', async () => {
      await Promise.all([loadQuote(), loadHistory(), loadNews(), loadProfile(), loadLocalNotes()]);
    });
  }

  (async function init() {
    bindControls();
    await Promise.all([loadQuote(), loadHistory(), loadNews(), loadProfile(), loadLocalNotes()]);
  })();
})();
