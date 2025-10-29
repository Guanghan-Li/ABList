(function () {
  const symbol = (window.__STOCK_SYMBOL__ || '').toUpperCase();
  const timeframeSelect = document.getElementById('timeframe');
  const toggleSMA = document.getElementById('toggleSMA');
  const toggleEMA = document.getElementById('toggleEMA');
  const toggleRSI = document.getElementById('toggleRSI');
  const smaPeriodInput = document.getElementById('smaPeriod');
  const emaPeriodInput = document.getElementById('emaPeriod');
  const rsiPeriodInput = document.getElementById('rsiPeriod');
  const overviewBox = document.getElementById('overviewBox');
  const newsList = document.getElementById('newsList');
  const lastUpdatedNote = document.getElementById('lastUpdatedNote');
  const rsiCard = document.getElementById('rsiCard');

  let priceChart, rsiChart;
  let priceSeries = []; // { date, close }

  function clampInt(value, min, max, fallback) {
    const n = parseInt(String(value), 10);
    if (Number.isNaN(n)) return fallback;
    return Math.min(max, Math.max(min, n));
  }

  function toLabels(series) {
    return series.map(p => p.date);
  }
  function toCloses(series) {
    return series.map(p => (typeof p.close === 'number' ? p.close : NaN));
  }

  function computeSMA(values, period) {
    const p = clampInt(period, 2, 400, 20);
    const out = new Array(values.length).fill(null);
    let sum = 0;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      sum += v;
      if (i >= p) sum -= values[i - p];
      if (i >= p - 1) out[i] = sum / p;
    }
    return out;
  }

  function computeEMA(values, period) {
    const p = clampInt(period, 2, 400, 50);
    const out = new Array(values.length).fill(null);
    const k = 2 / (p + 1);
    let prev = null;
    for (let i = 0; i < values.length; i++) {
      const v = values[i];
      if (prev == null) {
        // seed with SMA of first p values
        if (i >= p - 1) {
          let sum = 0;
          for (let j = i - p + 1; j <= i; j++) sum += values[j];
          prev = sum / p;
          out[i] = prev;
        }
      } else {
        prev = v * k + prev * (1 - k);
        out[i] = prev;
      }
    }
    return out;
  }

  function computeRSI(values, period) {
    const p = clampInt(period, 2, 200, 14);
    const out = new Array(values.length).fill(null);
    let gain = 0, loss = 0;
    for (let i = 1; i <= p; i++) {
      const ch = values[i] - values[i - 1];
      if (ch > 0) gain += ch; else loss -= ch;
    }
    gain /= p; loss /= p;
    let rs = loss === 0 ? 100 : gain / loss;
    out[p] = 100 - (100 / (1 + rs));
    for (let i = p + 1; i < values.length; i++) {
      const ch = values[i] - values[i - 1];
      const g = Math.max(0, ch);
      const l = Math.max(0, -ch);
      gain = (gain * (p - 1) + g) / p;
      loss = (loss * (p - 1) + l) / p;
      rs = loss === 0 ? 100 : gain / loss;
      out[i] = 100 - (100 / (1 + rs));
    }
    return out;
  }

  function renderPriceChart() {
    const ctx = document.getElementById('priceChart');
    if (!ctx) return;
    const labels = toLabels(priceSeries);
    const closes = toCloses(priceSeries);

    const datasets = [
      { label: 'Close', data: closes, borderColor: '#2563eb', pointRadius: 0, tension: 0.15 },
    ];

    if (toggleSMA.checked) {
      const period = clampInt(smaPeriodInput.value, 2, 400, 20);
      datasets.push({ label: `SMA ${period}` , data: computeSMA(closes, period), borderColor: '#10b981', pointRadius: 0, tension: 0.15 });
    }
    if (toggleEMA.checked) {
      const period = clampInt(emaPeriodInput.value, 2, 400, 50);
      datasets.push({ label: `EMA ${period}` , data: computeEMA(closes, period), borderColor: '#ef4444', pointRadius: 0, tension: 0.15 });
    }

    if (priceChart) priceChart.destroy();
    priceChart = new Chart(ctx, {
      type: 'line',
      data: { labels, datasets },
      options: {
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: true } },
        scales: {
          x: { display: true },
          y: { display: true, ticks: { callback: (v) => String(v) } },
        },
      },
    });
  }

  function renderRSIChart() {
    const ctx = document.getElementById('rsiChart');
    if (!ctx) return;
    if (!toggleRSI.checked) {
      if (rsiChart) rsiChart.destroy();
      rsiCard.style.display = 'none';
      return;
    }
    rsiCard.style.display = '';
    const labels = toLabels(priceSeries);
    const closes = toCloses(priceSeries);
    const period = clampInt(rsiPeriodInput.value, 2, 200, 14);
    const rsi = computeRSI(closes, period);

    if (rsiChart) rsiChart.destroy();
    rsiChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          { label: `RSI ${period}`, data: rsi, borderColor: '#7c3aed', pointRadius: 0, tension: 0.15 },
          { label: '70', data: new Array(labels.length).fill(70), borderColor: '#f59e0b', pointRadius: 0, borderDash: [6, 6], tension: 0 },
          { label: '30', data: new Array(labels.length).fill(30), borderColor: '#f59e0b', pointRadius: 0, borderDash: [6, 6], tension: 0 },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { display: true } },
        scales: { y: { min: 0, max: 100 } },
      },
    });
  }

  async function loadOverview() {
    overviewBox.innerHTML = '<div class="loader">Loading…</div>';
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/overview`);
      if (!res.ok) throw new Error('overview');
      const o = await res.json();
      const price = o.last_price != null ? Number(o.last_price).toFixed(2) : '—';
      overviewBox.innerHTML = `
        <p><strong>${o.name || symbol}</strong></p>
        <p>Sector: ${o.sector || '—'}</p>
        <p>Industry: ${o.industry || '—'}</p>
        <p>Exchange: ${o.exchange || '—'} | Currency: ${o.currency || '—'}</p>
        <p>Market Cap: ${formatMarketCap(o.market_cap)}</p>
        <p>Current Price: <strong>${price}</strong></p>
        <p style="white-space: pre-wrap;">${(o.summary || '—')}</p>
      `;
    } catch (_) {
      overviewBox.innerHTML = '<div class="error">Could not load overview.</div>';
    }
  }

  function formatMarketCap(val) {
    if (val == null || isNaN(val)) return '—';
    const n = Number(val);
    const abs = Math.abs(n);
    if (abs >= 1e12) return (n / 1e12).toFixed(2) + 'T';
    if (abs >= 1e9) return (n / 1e9).toFixed(2) + 'B';
    if (abs >= 1e6) return (n / 1e6).toFixed(2) + 'M';
    if (abs >= 1e3) return (n / 1e3).toFixed(2) + 'K';
    return String(n.toFixed(0));
  }

  async function loadNews() {
    newsList.innerHTML = '<li class="loader">Loading…</li>';
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/news?limit=10`);
      if (!res.ok) throw new Error('news');
      const items = await res.json();
      if (!Array.isArray(items) || items.length === 0) {
        newsList.innerHTML = '<li class="muted">No recent news.</li>';
        return;
      }
      newsList.innerHTML = items.map(it => `
        <li class="news-item">
          <a href="${it.link}" target="_blank" rel="noopener noreferrer">${escapeHtml(it.title)}</a>
          <div class="muted-small">${escapeHtml(it.publisher || '')} • ${escapeHtml(it.published_at || '')}</div>
        </li>
      `).join('');
    } catch (_) {
      newsList.innerHTML = '<li class="error">Failed to load news.</li>';
    }
  }

  async function loadHistory() {
    const tf = (timeframeSelect?.value || 'daily').toLowerCase();
    lastUpdatedNote.textContent = 'Loading…';
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/history?tf=${encodeURIComponent(tf)}`);
      if (!res.ok) throw new Error('history');
      const data = await res.json();
      const hist = Array.isArray(data.history) ? data.history : [];
      priceSeries = hist.map(h => ({ date: h.date, close: h.close }));
      renderPriceChart();
      renderRSIChart();
      lastUpdatedNote.textContent = `Loaded ${priceSeries.length} points (${tf}).`;
    } catch (_) {
      lastUpdatedNote.textContent = 'Failed to load history';
    }
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.innerText = String(str || '');
    return div.innerHTML;
  }

  function hookEvents() {
    timeframeSelect.addEventListener('change', async () => { await loadHistory(); });
    toggleSMA.addEventListener('change', () => { renderPriceChart(); });
    toggleEMA.addEventListener('change', () => { renderPriceChart(); });
    toggleRSI.addEventListener('change', () => { renderRSIChart(); });
    smaPeriodInput.addEventListener('change', () => { renderPriceChart(); });
    emaPeriodInput.addEventListener('change', () => { renderPriceChart(); });
    rsiPeriodInput.addEventListener('change', () => { renderRSIChart(); });
  }

  (async function init() {
    if (!symbol) return;
    hookEvents();
    await Promise.all([loadOverview(), loadNews(), loadHistory()]);
  })();
})();
