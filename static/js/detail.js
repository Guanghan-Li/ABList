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
  const metricDateSpottedEl = document.getElementById('metricDateSpotted');
  const metricPriceSpottedEl = document.getElementById('metricPriceSpotted');
  const metricPriceChangeEl = document.getElementById('metricPriceChange');
  const zoomInBtn = document.getElementById('zoomInBtn');
  const zoomOutBtn = document.getElementById('zoomOutBtn');
  const resetZoomBtn = document.getElementById('resetZoomBtn');

  let priceChart;
  let rsiChart;
  let currentInterval = intervalSelect?.value || '1d';
  let baseHistory = [];
  let overlayCounter = 1;
  const overlays = new Map(); // id -> overlay definition
  let defaultTimeRange = null;
  let shouldResetZoom = false;

  const overlayTemplate = {
    type: 'sma',
    window: 20,
    color: '#2563eb',
  };

  initCharts();
  bindEvents();
  loadOverview();
  loadSnapshot();
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

  async function loadSnapshot() {
    if (!metricDateSpottedEl || !metricPriceSpottedEl || !metricPriceChangeEl) return;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/snapshot`);
      if (res.status === 404) {
        renderSnapshot(null);
        return;
      }
      if (!res.ok) throw new Error('Failed to fetch stock snapshot');
      const data = await res.json();
      renderSnapshot(data);
    } catch (err) {
      console.error(err);
      renderSnapshot(null);
    }
  }

  function renderSnapshot(snapshot) {
    if (!metricDateSpottedEl || !metricPriceSpottedEl || !metricPriceChangeEl) return;
    const dateLabel = snapshot?.date_spotted || snapshot?.date_added;
    const formattedDate = formatDate(dateLabel);
    metricDateSpottedEl.textContent = formattedDate || '—';

    const initialPrice = snapshot?.initial_price;
    metricPriceSpottedEl.textContent = initialPrice != null && !Number.isNaN(Number(initialPrice))
      ? formatPrice(initialPrice)
      : '—';

    updatePercentDisplay(metricPriceChangeEl, snapshot?.percent_change);

    if (snapshot?.current_price != null && !Number.isNaN(Number(snapshot.current_price))) {
      priceValueEl.textContent = formatPrice(snapshot.current_price);
    }
  }

  async function loadHistory() {
    const interval = currentInterval;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/history?interval=${encodeURIComponent(interval)}`);
      if (!res.ok) throw new Error('Failed to fetch price history');
      const data = await res.json();
      baseHistory = Array.isArray(data.points) ? data.points : [];
      shouldResetZoom = true;
      updatePriceChart();
      lastUpdatedAt.textContent = `Updated: ${new Date().toLocaleString()}`;
      await refreshAllOverlays();
      if (rsiToggle.checked) {
        await loadRsi(true);
      }
    } catch (err) {
      console.error(err);
      baseHistory = [];
      shouldResetZoom = true;
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
    registerZoomPlugin();
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
          zoom: {
            limits: {
              x: { min: 'original', max: 'original' },
              y: { min: 'original', max: 'original' },
            },
            pan: {
              enabled: true,
              mode: 'x',
              modifierKey: 'shift',
            },
            zoom: {
              wheel: { enabled: true },
              pinch: { enabled: true },
              drag: {
                enabled: true,
                modifierKey: null,
                borderColor: '#2563eb',
                borderWidth: 1,
                backgroundColor: 'rgba(37, 99, 235, 0.12)',
              },
              mode: 'x',
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
    const pricePoints = mapHistoryToPoints(baseHistory, 'close');
    datasets.push({
      id: 'price',
      label: `${symbol} Close`,
      data: pricePoints,
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
    if (shouldResetZoom) {
      defaultTimeRange = computeDefaultRange(pricePoints);
      resetZoomInternal();
      shouldResetZoom = false;
    } else {
      priceChart.update();
    }
  }

  function computeDefaultRange(points) {
    if (!Array.isArray(points) || points.length === 0) {
      return null;
    }
    let min = null;
    let max = null;
    points.forEach((point) => {
      if (!point || !point.x) {
        return;
      }
      const value = point.x instanceof Date ? point.x.getTime() : new Date(point.x).getTime();
      if (Number.isNaN(value)) {
        return;
      }
      if (min === null || value < min) min = value;
      if (max === null || value > max) max = value;
    });
    if (min === null || max === null || max <= min) {
      return null;
    }
    return { min, max };
  }

  function resetZoomInternal() {
    if (!priceChart) return;
    const canUsePlugin = typeof priceChart.resetZoom === 'function';
    if (canUsePlugin) {
      try {
        priceChart.resetZoom();
      } catch (err) {
        console.warn('Falling back to manual zoom reset', err);
        manualResetZoom();
      }
      priceChart.update('none');
      return;
    }
    manualResetZoom();
    priceChart.update('none');
  }

  function zoomChartByFactor(factor) {
    if (!priceChart) return;
    if (typeof priceChart.zoom === 'function') {
      try {
        priceChart.zoom(factor);
        priceChart.update('none');
        return;
      } catch (err) {
        try {
          priceChart.zoom({ x: { factor } });
          priceChart.update('none');
          return;
        } catch (innerErr) {
          console.warn('Zoom plugin call failed, using manual zoom', innerErr);
        }
      }
    }
    manualZoom(factor);
  }

  function manualZoom(factor) {
    if (!priceChart || !defaultTimeRange) return;
    const scale = priceChart.scales?.x;
    if (!scale) return;
    const currentRange = getCurrentRange(scale);
    if (!currentRange) return;
    const { min: currentMin, max: currentMax } = currentRange;
    const defaultRange = defaultTimeRange.max - defaultTimeRange.min;
    if (!Number.isFinite(defaultRange) || defaultRange <= 0) return;
    const currentSpan = currentMax - currentMin;
    if (!Number.isFinite(currentSpan) || currentSpan <= 0) return;
    const targetSpan = currentSpan / factor;
    const minSpan = Math.max(defaultRange / 200, 24 * 60 * 60 * 1000); // avoid over-zooming beyond a day
    const nextSpan = Math.max(minSpan, targetSpan);
    if (nextSpan >= defaultRange) {
      manualResetZoom();
      priceChart.update('none');
      return;
    }
    const center = currentMin + currentSpan / 2;
    let nextMin = center - nextSpan / 2;
    let nextMax = center + nextSpan / 2;

    if (nextMin < defaultTimeRange.min) {
      const diff = defaultTimeRange.min - nextMin;
      nextMin += diff;
      nextMax += diff;
    }
    if (nextMax > defaultTimeRange.max) {
      const diff = nextMax - defaultTimeRange.max;
      nextMin -= diff;
      nextMax -= diff;
    }
    nextMin = Math.max(defaultTimeRange.min, nextMin);
    nextMax = Math.min(defaultTimeRange.max, nextMax);
    if (nextMax <= nextMin) {
      manualResetZoom();
      priceChart.update('none');
      return;
    }
    setScaleRange(nextMin, nextMax);
    priceChart.update('none');
  }

  function manualResetZoom() {
    const xOptions = priceChart?.options?.scales?.x;
    if (!xOptions) return;
    if (defaultTimeRange) {
      xOptions.min = defaultTimeRange.min;
      xOptions.max = defaultTimeRange.max;
    } else {
      delete xOptions.min;
      delete xOptions.max;
    }
  }

  function setScaleRange(min, max) {
    const xOptions = priceChart?.options?.scales?.x;
    if (!xOptions) return;
    xOptions.min = min;
    xOptions.max = max;
  }

  function getCurrentRange(scale) {
    if (!scale) return null;
    const resolvedMin = Number.isFinite(scale.min) ? scale.min : defaultTimeRange?.min;
    const resolvedMax = Number.isFinite(scale.max) ? scale.max : defaultTimeRange?.max;
    if (!Number.isFinite(resolvedMin) || !Number.isFinite(resolvedMax) || resolvedMax <= resolvedMin) {
      return null;
    }
    return { min: resolvedMin, max: resolvedMax };
  }

  function updatePercentDisplay(element, rawValue) {
    if (!element) return;
    element.classList.remove('positive-change', 'negative-change');
    if (rawValue == null || Number.isNaN(Number(rawValue))) {
      element.textContent = '—';
      return;
    }
    const numeric = Number(rawValue);
    element.textContent = formatPercent(numeric);
    if (numeric > 0) {
      element.classList.add('positive-change');
    } else if (numeric < 0) {
      element.classList.add('negative-change');
    }
  }

  function formatPercent(value) {
    if (value == null || Number.isNaN(Number(value))) return '—';
    const numeric = Number(value);
    const fixed = numeric.toFixed(2);
    const sign = numeric > 0 ? '+' : '';
    return `${sign}${fixed}%`;
  }

  function formatDate(value) {
    const parsed = parseDateValue(value);
    if (!parsed) return null;
    return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  }

  function parseDateValue(value) {
    if (!value) return null;
    const direct = new Date(value);
    if (!Number.isNaN(direct.getTime())) return direct;
    const fallback = new Date(`${value}T00:00:00`);
    if (!Number.isNaN(fallback.getTime())) return fallback;
    return null;
  }

  function registerZoomPlugin() {
    if (typeof Chart === 'undefined' || typeof Chart.register !== 'function') return;
    const plugin = resolveZoomPlugin();
    if (!plugin) return;
    try {
      Chart.register(plugin);
    } catch (err) {
      if (!err || typeof err.message !== 'string' || !err.message.includes('already registered')) {
        console.warn('Chart zoom plugin registration failed', err);
      }
    }
  }

  function resolveZoomPlugin() {
    const pluginSources = [
      window.chartjsPluginZoom,
      window.chartjsPluginZoom?.default,
      window.ChartZoom,
      window.ChartZoom?.default,
      window.Chart?.Zoom,
      window.Chart?.Zoom?.default,
      window['chartjs-plugin-zoom'],
      window['chartjs-plugin-zoom']?.default,
    ];
    for (const source of pluginSources) {
      if (source && typeof source === 'object' && source.id) {
        return source;
      }
    }
    return null;
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

    zoomInBtn?.addEventListener('click', () => {
      zoomChartByFactor(1.4);
    });
    zoomOutBtn?.addEventListener('click', () => {
      zoomChartByFactor(0.7);
    });
    resetZoomBtn?.addEventListener('click', () => {
      resetZoomInternal();
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
