(function () {
  const symbol = (window.STOCK_SYMBOL || '').trim().toUpperCase();
  if (!symbol) {
    console.error('Missing stock symbol for detail view.');
    return;
  }

  const intervalSelect = document.getElementById('intervalSelect');
  const lastUpdatedAt = document.getElementById('lastUpdatedAt');
  const priceCanvas = document.getElementById('priceChart');
  const priceValueEl = document.getElementById('currentPrice');
  const priceCurrencyEl = document.getElementById('priceCurrency');
  const companyNameEl = document.getElementById('companyName');
  const summaryNameEl = document.getElementById('summaryName');
  const summarySectorEl = document.getElementById('summarySector');
  const summaryIndustryEl = document.getElementById('summaryIndustry');
  const summaryMarketCapEl = document.getElementById('summaryMarketCap');
  const summaryWebsiteEl = document.getElementById('summaryWebsite');
  const summaryDescriptionEl = document.getElementById('summaryDescription');
  const overlayForm = document.getElementById('overlayForm');
  const overlayTypeEl = document.getElementById('overlayType');
  const overlayWindowEl = document.getElementById('overlayWindow');
  const overlayColorEl = document.getElementById('overlayColor');
  const overlayListEl = document.getElementById('overlayList');
  const rsiToggle = document.getElementById('rsiToggle');
  const rsiPeriodEl = document.getElementById('rsiPeriod');
  const rsiApplyBtn = document.getElementById('rsiApply');
  const rsiWrapper = document.getElementById('rsiChartWrapper');
  const rsiCanvas = document.getElementById('rsiChart');
  const newsListEl = document.getElementById('newsList');

  let priceChart;
  let rsiChart;
  let currentInterval = intervalSelect?.value || '1d';
  let baseHistory = [];
  let overlayCounter = 1;
  const overlays = new Map(); // id -> overlay definition

  const overlayTemplate = {
    type: 'sma',
    window: 20,
    color: '#2563eb',
  };

  initCharts();
  bindEvents();
  loadOverview();
  loadHistory();
  loadNews();

  async function loadOverview() {
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/overview`);
      if (!res.ok) throw new Error('Failed to fetch overview');
      const data = await res.json();
      const name = data.longName || data.shortName || symbol;
      companyNameEl.textContent = name;
      summaryNameEl.textContent = name;
      summarySectorEl.textContent = data.sector || '—';
      summaryIndustryEl.textContent = data.industry || '—';
      summaryMarketCapEl.textContent = formatMarketCap(data.market_cap) || '—';
      summaryWebsiteEl.innerHTML = data.website
        ? `<a href="${escapeHtml(data.website)}" target="_blank" rel="noopener">${escapeHtml(data.website)}</a>`
        : '—';
      summaryDescriptionEl.textContent = data.longBusinessSummary || 'No company summary available.';

      if (data.last_price != null) {
        priceValueEl.textContent = formatPrice(data.last_price);
      } else {
        priceValueEl.textContent = '—';
      }
      priceCurrencyEl.textContent = data.currency ? data.currency.toUpperCase() : '';
    } catch (err) {
      console.error(err);
      companyNameEl.textContent = 'Overview unavailable';
      summaryDescriptionEl.textContent = 'Could not load company summary.';
    }
  }

  async function loadHistory() {
    const interval = currentInterval;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/history?interval=${encodeURIComponent(interval)}`);
      if (!res.ok) throw new Error('Failed to fetch price history');
      const data = await res.json();
      baseHistory = Array.isArray(data.points) ? data.points : [];
      updatePriceChart();
      lastUpdatedAt.textContent = `Updated: ${new Date().toLocaleString()}`;
      await refreshAllOverlays();
      if (rsiToggle.checked) {
        await loadRsi(true);
      }
    } catch (err) {
      console.error(err);
      baseHistory = [];
      updatePriceChart();
      lastUpdatedAt.textContent = 'Updated: failed to load';
    }
  }

  async function loadNews() {
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/news`);
      if (!res.ok) throw new Error('Failed to fetch news');
      const payload = await res.json();
      const articles = Array.isArray(payload.articles) ? payload.articles : [];
      renderNews(articles);
    } catch (err) {
      console.error(err);
      newsListEl.innerHTML = '<li class="muted">No news available right now.</li>';
    }
  }

  function initCharts() {
    priceChart = new Chart(priceCanvas, {
      type: 'line',
      data: { datasets: [] },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: {
            type: 'time',
            time: { tooltipFormat: 'MMM dd, yyyy' },
            ticks: { autoSkip: true, maxRotation: 0 },
          },
          y: {
            ticks: { callback: (value) => formatCompactNumber(value) },
            grid: { color: 'rgba(148, 163, 184, 0.2)' },
          },
        },
        plugins: {
          legend: { display: true },
          tooltip: {
            callbacks: {
              label(context) {
                const val = context.parsed?.y;
                if (val == null) return context.dataset.label || '';
                return `${context.dataset.label || ''}: ${formatPrice(val)}`;
              },
            },
          },
        },
        elements: { point: { radius: 0 } },
      },
    });
  }

  function updatePriceChart() {
    if (!priceChart) return;
    const datasets = [];
    datasets.push({
      id: 'price',
      label: `${symbol} Close`,
      data: mapHistoryToPoints(baseHistory, 'close'),
      borderColor: '#1f2937',
      backgroundColor: 'rgba(96, 165, 250, 0.15)',
      fill: false,
      tension: 0.15,
      spanGaps: true,
    });
    overlays.forEach((overlay) => {
      if (overlay.dataset) {
        datasets.push(overlay.dataset);
      }
    });
    priceChart.data.datasets = datasets;
    priceChart.update();
  }

  async function refreshAllOverlays() {
    const overlayPromises = Array.from(overlays.values()).map(async (overlay) => {
      const indicator = await fetchIndicatorData(overlay.type, overlay.window);
      overlay.dataset = buildOverlayDataset(overlay, indicator);
    });
    await Promise.allSettled(overlayPromises);
    updatePriceChart();
  }

  async function fetchIndicatorData(indicatorType, period) {
    const url = `/api/stocks/${encodeURIComponent(symbol)}/indicators?type=${indicatorType}&interval=${encodeURIComponent(currentInterval)}&windows=${period}`;
    const res = await fetch(url);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.error || 'Failed to fetch indicator');
    }
    const payload = await res.json();
    const indicators = Array.isArray(payload.indicators) ? payload.indicators : [];
    return indicators[0]?.values || [];
  }

  function buildOverlayDataset(def, values) {
    return {
      id: `overlay-${def.id}`,
      label: `${def.type.toUpperCase()} (${def.window})`,
      data: mapIndicatorToPoints(values),
      borderColor: def.color,
      backgroundColor: def.color,
      borderWidth: 2,
      fill: false,
      tension: 0.1,
      spanGaps: true,
    };
  }

  function bindEvents() {
    intervalSelect.addEventListener('change', async (event) => {
      currentInterval = event.target.value || '1d';
      await loadHistory();
    });

    overlayForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      const indicator = (overlayTypeEl.value || overlayTemplate.type).toLowerCase();
      const period = parseInt(overlayWindowEl.value, 10);
      const color = overlayColorEl.value || overlayTemplate.color;
      if (!Number.isInteger(period) || period <= 0) {
        alert('Period must be a positive integer.');
        return;
      }
      try {
        const values = await fetchIndicatorData(indicator, period);
        const id = overlayCounter++;
        const overlayDef = { id, type: indicator, window: period, color };
        overlayDef.dataset = buildOverlayDataset(overlayDef, values);
        overlays.set(id, overlayDef);
        appendOverlayListItem(overlayDef);
        updatePriceChart();
      } catch (err) {
        console.error(err);
        alert(err.message || 'Unable to add indicator.');
      }
    });

    rsiToggle.addEventListener('change', async () => {
      if (rsiToggle.checked) {
        await loadRsi(true);
      } else {
        hideRsiChart();
      }
    });

    rsiApplyBtn.addEventListener('click', async (event) => {
      event.preventDefault();
      if (!rsiToggle.checked) {
        rsiToggle.checked = true;
      }
      await loadRsi(false);
    });
  }

  async function loadRsi(autoToggle) {
    const period = parseInt(rsiPeriodEl.value, 10);
    if (!Number.isInteger(period) || period <= 1) {
      alert('RSI period must be greater than 1.');
      return;
    }
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/rsi?interval=${encodeURIComponent(currentInterval)}&period=${period}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to fetch RSI');
      }
      const data = await res.json();
      const values = Array.isArray(data.values) ? data.values : [];
      renderRsiChart(values, period);
    } catch (err) {
      console.error(err);
      if (autoToggle) {
        rsiToggle.checked = false;
      }
      alert(err.message || 'Unable to load RSI data.');
    }
  }

  function hideRsiChart() {
    rsiWrapper.classList.add('hidden');
    if (rsiChart) {
      rsiChart.destroy();
      rsiChart = null;
    }
  }

  function renderRsiChart(values, period) {
    rsiWrapper.classList.remove('hidden');
    const points = mapIndicatorToPoints(values);
    if (rsiChart) {
      rsiChart.data.datasets = buildRsiDatasets(points, period);
      rsiChart.update();
      return;
    }
    rsiChart = new Chart(rsiCanvas, {
      type: 'line',
      data: { datasets: buildRsiDatasets(points, period) },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { type: 'time', time: { tooltipFormat: 'MMM dd, yyyy' } },
          y: {
            suggestedMin: 0,
            suggestedMax: 100,
            ticks: { stepSize: 10 },
          },
        },
        plugins: {
          legend: { display: true },
        },
        elements: { point: { radius: 0 } },
      },
    });
  }

  function buildRsiDatasets(points, period) {
    const base = {
      label: `RSI (${period})`,
      data: points,
      borderColor: '#7c3aed',
      backgroundColor: '#7c3aed',
      tension: 0.1,
      spanGaps: true,
    };
    const upper = buildHorizontalLineDataset('RSI 70', 70, points, '#f97316');
    const lower = buildHorizontalLineDataset('RSI 30', 30, points, '#0ea5e9');
    return [base, upper, lower];
  }

  function buildHorizontalLineDataset(label, value, referencePoints, color) {
    const data = referencePoints.map((pt) => ({ x: pt.x, y: value }));
    return {
      label,
      data,
      borderColor: color,
      borderWidth: 1,
      borderDash: [6, 6],
      pointRadius: 0,
      tension: 0,
    };
  }

  function appendOverlayListItem(overlay) {
    const li = document.createElement('li');
    li.className = 'overlay-item';
    li.dataset.id = overlay.id;
    li.innerHTML = `
      <span class="overlay-label">
        <span class="overlay-color" style="background:${overlay.color}"></span>
        ${overlay.type.toUpperCase()} (${overlay.window})
      </span>
      <button type="button" class="btn btn-small" data-action="remove">Remove</button>
    `;
    li.querySelector('button[data-action="remove"]').addEventListener('click', () => {
      overlays.delete(overlay.id);
      li.remove();
      updatePriceChart();
    });
    overlayListEl.appendChild(li);
  }

  function renderNews(articles) {
    if (!articles.length) {
      newsListEl.innerHTML = '<li class="muted">No recent articles found.</li>';
      return;
    }
    const items = articles.map((article) => {
      const title = escapeHtml(article.title || 'View article');
      const link = article.link ? escapeHtml(article.link) : '#';
      const publisher = escapeHtml(article.publisher || 'Unknown');
      const published = formatDateTime(article.published_at);
      return `
        <li>
          <a href="${link}" target="_blank" rel="noopener" class="news-title">${title}</a>
          <div class="news-meta muted">${publisher}${published ? ` · ${published}` : ''}</div>
        </li>
      `;
    });
    newsListEl.innerHTML = items.join('');
  }

  function mapHistoryToPoints(history, key) {
    if (!Array.isArray(history)) return [];
    return history
      .filter((item) => item && item.date && item[key] != null)
      .map((item) => ({ x: new Date(item.date), y: Number(item[key]) }));
  }

  function mapIndicatorToPoints(values) {
    if (!Array.isArray(values)) return [];
    return values
      .filter((item) => item && item.date)
      .map((item) => ({
        x: new Date(item.date),
        y: item.value == null ? null : Number(item.value),
      }));
  }

  function formatPrice(value) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    return Number(value).toFixed(2);
  }

  function formatCompactNumber(value) {
    if (value == null || Number.isNaN(Number(value))) return '';
    try {
      return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value));
    } catch (err) {
      return String(value);
    }
  }

  function formatMarketCap(value) {
    if (value == null) return null;
    try {
      return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 }).format(Number(value));
    } catch (err) {
      return String(value);
    }
  }

  function formatDateTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString();
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.innerText = str || '';
    return div.innerHTML;
  }
})();

