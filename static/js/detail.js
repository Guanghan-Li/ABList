(function () {
  // Constants
  const ZOOM_CONSTANTS = {
    IN_FACTOR: 1.4,
    OUT_FACTOR: 0.7,
    MAX_ZOOM_DIVISOR: 200,
    MIN_SPAN_MS: 24 * 60 * 60 * 1000, // 1 day in milliseconds
  };

  const RSI_THRESHOLDS = {
    OVERBOUGHT: 70,
    OVERSOLD: 30,
  };

  const DEBOUNCE_DELAY = 50; // ms

  // Symbol validation
  const symbol = (window.STOCK_SYMBOL || '').trim().toUpperCase();
  if (!symbol) {
    console.error('Missing stock symbol for detail view.');
    return;
  }

  // DOM elements with null checks
  const elements = {
    intervalSelect: document.getElementById('intervalSelect'),
    lastUpdatedAt: document.getElementById('lastUpdatedAt'),
    priceCanvas: document.getElementById('priceChart'),
    priceValueEl: document.getElementById('currentPrice'),
    priceCurrencyEl: document.getElementById('priceCurrency'),
    companyNameEl: document.getElementById('companyName'),
    summaryNameEl: document.getElementById('summaryName'),
    summarySectorEl: document.getElementById('summarySector'),
    summaryIndustryEl: document.getElementById('summaryIndustry'),
    summaryMarketCapEl: document.getElementById('summaryMarketCap'),
    summaryWebsiteEl: document.getElementById('summaryWebsite'),
    summaryDescriptionEl: document.getElementById('summaryDescription'),
    overlayForm: document.getElementById('overlayForm'),
    overlayTypeEl: document.getElementById('overlayType'),
    overlayWindowEl: document.getElementById('overlayWindow'),
    overlayColorEl: document.getElementById('overlayColor'),
    overlayListEl: document.getElementById('overlayList'),
    rsiToggle: document.getElementById('rsiToggle'),
    rsiPeriodEl: document.getElementById('rsiPeriod'),
    rsiApplyBtn: document.getElementById('rsiApply'),
    rsiWrapper: document.getElementById('rsiChartWrapper'),
    rsiCanvas: document.getElementById('rsiChart'),
    newsListEl: document.getElementById('newsList'),
    metricDateSpottedEl: document.getElementById('metricDateSpotted'),
    metricPriceSpottedEl: document.getElementById('metricPriceSpotted'),
    metricPriceChangeEl: document.getElementById('metricPriceChange'),
    zoomInBtn: document.getElementById('zoomInBtn'),
    zoomOutBtn: document.getElementById('zoomOutBtn'),
    resetZoomBtn: document.getElementById('resetZoomBtn'),
  };

  // Verify required elements exist
  const requiredElements = ['intervalSelect', 'priceCanvas', 'lastUpdatedAt'];
  const missingElements = requiredElements.filter(key => !elements[key]);

  if (missingElements.length > 0) {
    console.error('Missing required elements:', missingElements);
    return;
  }

  // State management
  const state = {
    charts: {
      price: null,
      rsi: null,
    },
    data: {
      currentInterval: elements.intervalSelect?.value || '1d',
      baseHistory: [],
      defaultTimeRange: null,
      cachedPricePoints: null,
      lastHistoryRef: null,
    },
    ui: {
      shouldResetZoom: false,
      overlayCounter: 1,
      overlays: new Map(),
    },
    requests: {
      currentHistory: null,
      overlayRefresh: null,
    },
    timers: {
      chartUpdate: null,
    },
  };

  const overlayTemplate = {
    type: 'sma',
    window: 20,
    color: '#2563eb',
  };

  // Cleanup function to prevent memory leaks
  function cleanup() {
    if (state.charts.price) {
      state.charts.price.destroy();
      state.charts.price = null;
    }
    if (state.charts.rsi) {
      state.charts.rsi.destroy();
      state.charts.rsi = null;
    }
  }

  // Register cleanup on page unload
  window.addEventListener('beforeunload', cleanup);

  // Wait for all deferred scripts to load before initializing
  function initializeApp() {
    // Check if Chart.js is loaded
    if (typeof Chart === 'undefined') {
      console.error('Chart.js library not loaded');
      return;
    }

    // Initialize
    initCharts();
    bindEvents();
    loadOverview();
    loadSnapshot();
    loadHistory();
    loadNews();
  }

  // Run initialization when DOM and all deferred scripts are ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeApp);
  } else {
    // DOM already loaded, check if libraries are ready
    if (typeof Chart !== 'undefined') {
      initializeApp();
    } else {
      // Wait for window load event to ensure all deferred scripts are loaded
      window.addEventListener('load', initializeApp);
    }
  }

  async function loadOverview() {
    if (!elements.companyNameEl || !elements.summaryDescriptionEl) return;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/overview`);
      if (!res.ok) throw new Error('Failed to fetch overview');
      const data = await res.json();
      const name = data.longName || data.shortName || symbol;
      if (elements.companyNameEl) elements.companyNameEl.textContent = name;
      if (elements.summaryNameEl) elements.summaryNameEl.textContent = name;
      if (elements.summarySectorEl) elements.summarySectorEl.textContent = data.sector || '—';
      if (elements.summaryIndustryEl) elements.summaryIndustryEl.textContent = data.industry || '—';
      if (elements.summaryMarketCapEl) elements.summaryMarketCapEl.textContent = formatMarketCap(data.market_cap) || '—';
      if (elements.summaryWebsiteEl) {
        elements.summaryWebsiteEl.innerHTML = data.website
          ? `<a href="${escapeHtml(data.website)}" target="_blank" rel="noopener">${escapeHtml(data.website)}</a>`
          : '—';
      }
      if (elements.summaryDescriptionEl) {
        elements.summaryDescriptionEl.textContent = data.longBusinessSummary || 'No company summary available.';
      }

      if (data.last_price != null && elements.priceValueEl) {
        elements.priceValueEl.textContent = formatPrice(data.last_price);
      } else if (elements.priceValueEl) {
        elements.priceValueEl.textContent = '—';
      }
      if (elements.priceCurrencyEl) {
        elements.priceCurrencyEl.textContent = data.currency ? data.currency.toUpperCase() : '';
      }
    } catch (err) {
      console.error('Error loading overview:', err);
      if (elements.companyNameEl) elements.companyNameEl.textContent = 'Overview unavailable';
      if (elements.summaryDescriptionEl) elements.summaryDescriptionEl.textContent = 'Could not load company summary.';
    }
  }

  async function loadSnapshot() {
    if (!elements.metricDateSpottedEl || !elements.metricPriceSpottedEl || !elements.metricPriceChangeEl) return;
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
    if (!elements.metricDateSpottedEl || !elements.metricPriceSpottedEl || !elements.metricPriceChangeEl) return;
    const dateLabel = snapshot?.date_spotted || snapshot?.date_added;
    const formattedDate = formatDate(dateLabel);
    elements.metricDateSpottedEl.textContent = formattedDate || '—';

    const initialPrice = snapshot?.initial_price;
    elements.metricPriceSpottedEl.textContent = initialPrice != null && !Number.isNaN(Number(initialPrice))
      ? formatPrice(initialPrice)
      : '—';

    updatePercentDisplay(elements.metricPriceChangeEl, snapshot?.percent_change);

    if (snapshot?.current_price != null && !Number.isNaN(Number(snapshot.current_price)) && elements.priceValueEl) {
      elements.priceValueEl.textContent = formatPrice(snapshot.current_price);
    }
  }

  async function loadHistory() {
    const interval = state.data.currentInterval;
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/history?interval=${encodeURIComponent(interval)}`);
      if (!res.ok) throw new Error('Failed to fetch price history');
      const data = await res.json();
      state.data.baseHistory = Array.isArray(data.points) ? data.points : [];
      state.ui.shouldResetZoom = true;
      updatePriceChart();
      if (elements.lastUpdatedAt) {
        elements.lastUpdatedAt.textContent = `Updated: ${new Date().toLocaleString()}`;
      }
      await refreshAllOverlays();
      if (elements.rsiToggle?.checked) {
        await loadRsi(true);
      }
    } catch (err) {
      console.error(err);
      state.data.baseHistory = [];
      state.ui.shouldResetZoom = true;
      updatePriceChart();
      if (elements.lastUpdatedAt) {
        elements.lastUpdatedAt.textContent = 'Updated: failed to load';
      }
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
      if (elements.newsListEl) {
        elements.newsListEl.innerHTML = '<li class="muted">No news available right now.</li>';
      }
    }
  }

  function initCharts() {
    if (!elements.priceCanvas) return;
    registerZoomPlugin();
    state.charts.price = new Chart(elements.priceCanvas, {
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
    if (!state.charts.price) return;
    const datasets = [];
    const pricePoints = mapHistoryToPoints(state.data.baseHistory, 'close');
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
    state.ui.overlays.forEach((overlay) => {
      if (overlay.dataset) {
        datasets.push(overlay.dataset);
      }
    });
    state.charts.price.data.datasets = datasets;
    if (state.ui.shouldResetZoom) {
      state.data.defaultTimeRange = computeDefaultRange(pricePoints);
      resetZoomInternal();
      state.ui.shouldResetZoom = false;
    } else {
      state.charts.price.update();
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
    if (!state.charts.price) return;
    const canUsePlugin = typeof state.charts.price.resetZoom === 'function';
    if (canUsePlugin) {
      try {
        state.charts.price.resetZoom();
      } catch (err) {
        console.warn('Falling back to manual zoom reset', err);
        manualResetZoom();
      }
      state.charts.price.update('none');
      return;
    }
    manualResetZoom();
    state.charts.price.update('none');
  }

  function zoomChartByFactor(factor) {
    if (!state.charts.price) return;
    if (typeof state.charts.price.zoom === 'function') {
      try {
        state.charts.price.zoom(factor);
        state.charts.price.update('none');
        return;
      } catch (err) {
        try {
          state.charts.price.zoom({ x: { factor } });
          state.charts.price.update('none');
          return;
        } catch (innerErr) {
          console.warn('Zoom plugin call failed, using manual zoom', innerErr);
        }
      }
    }
    manualZoom(factor);
  }

  function manualZoom(factor) {
    if (!state.charts.price || !state.data.defaultTimeRange) return;
    const scale = state.charts.price.scales?.x;
    if (!scale) return;
    const currentRange = getCurrentRange(scale);
    if (!currentRange) return;
    const { min: currentMin, max: currentMax } = currentRange;
    const defaultRange = state.data.defaultTimeRange.max - state.data.defaultTimeRange.min;
    if (!Number.isFinite(defaultRange) || defaultRange <= 0) return;
    const currentSpan = currentMax - currentMin;
    if (!Number.isFinite(currentSpan) || currentSpan <= 0) return;
    const targetSpan = currentSpan / factor;
    const minSpan = Math.max(defaultRange / ZOOM_CONSTANTS.MAX_ZOOM_DIVISOR, ZOOM_CONSTANTS.MIN_SPAN_MS);
    const nextSpan = Math.max(minSpan, targetSpan);
    if (nextSpan >= defaultRange) {
      manualResetZoom();
      state.charts.price.update('none');
      return;
    }
    const center = currentMin + currentSpan / 2;
    let nextMin = center - nextSpan / 2;
    let nextMax = center + nextSpan / 2;

    if (nextMin < state.data.defaultTimeRange.min) {
      const diff = state.data.defaultTimeRange.min - nextMin;
      nextMin += diff;
      nextMax += diff;
    }
    if (nextMax > state.data.defaultTimeRange.max) {
      const diff = nextMax - state.data.defaultTimeRange.max;
      nextMin -= diff;
      nextMax -= diff;
    }
    nextMin = Math.max(state.data.defaultTimeRange.min, nextMin);
    nextMax = Math.min(state.data.defaultTimeRange.max, nextMax);
    if (nextMax <= nextMin) {
      manualResetZoom();
      state.charts.price.update('none');
      return;
    }
    setScaleRange(nextMin, nextMax);
    state.charts.price.update('none');
  }

  function manualResetZoom() {
    const xOptions = state.charts.price?.options?.scales?.x;
    if (!xOptions) return;
    if (state.data.defaultTimeRange) {
      xOptions.min = state.data.defaultTimeRange.min;
      xOptions.max = state.data.defaultTimeRange.max;
    } else {
      delete xOptions.min;
      delete xOptions.max;
    }
  }

  function setScaleRange(min, max) {
    const xOptions = state.charts.price?.options?.scales?.x;
    if (!xOptions) return;
    xOptions.min = min;
    xOptions.max = max;
  }

  function getCurrentRange(scale) {
    if (!scale) return null;
    const resolvedMin = Number.isFinite(scale.min) ? scale.min : state.data.defaultTimeRange?.min;
    const resolvedMax = Number.isFinite(scale.max) ? scale.max : state.data.defaultTimeRange?.max;
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
    const overlayPromises = Array.from(state.ui.overlays.values()).map(async (overlay) => {
      const indicator = await fetchIndicatorData(overlay.type, overlay.window);
      overlay.dataset = buildOverlayDataset(overlay, indicator);
    });
    await Promise.allSettled(overlayPromises);
    updatePriceChart();
  }

  async function fetchIndicatorData(indicatorType, period) {
    const url = `/api/stocks/${encodeURIComponent(symbol)}/indicators?type=${indicatorType}&interval=${encodeURIComponent(state.data.currentInterval)}&windows=${period}`;
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
    if (elements.intervalSelect) {
      elements.intervalSelect.addEventListener('change', async (event) => {
        state.data.currentInterval = event.target.value || '1d';
        await loadHistory();
      });
    }

    if (elements.overlayForm) {
      elements.overlayForm.addEventListener('submit', async (event) => {
        event.preventDefault();
        const indicator = (elements.overlayTypeEl?.value || overlayTemplate.type).toLowerCase();
        const period = parseInt(elements.overlayWindowEl?.value, 10);
        const color = elements.overlayColorEl?.value || overlayTemplate.color;
        if (!Number.isInteger(period) || period <= 0) {
          alert('Period must be a positive integer.');
          return;
        }
        try {
          const values = await fetchIndicatorData(indicator, period);
          const id = state.ui.overlayCounter++;
          const overlayDef = { id, type: indicator, window: period, color };
          overlayDef.dataset = buildOverlayDataset(overlayDef, values);
          state.ui.overlays.set(id, overlayDef);
          appendOverlayListItem(overlayDef);
          updatePriceChart();
        } catch (err) {
          console.error(err);
          alert(err.message || 'Unable to add indicator.');
        }
      });
    }

    if (elements.rsiToggle) {
      elements.rsiToggle.addEventListener('change', async () => {
        if (elements.rsiToggle.checked) {
          await loadRsi(true);
        } else {
          hideRsiChart();
        }
      });
    }

    if (elements.rsiApplyBtn) {
      elements.rsiApplyBtn.addEventListener('click', async (event) => {
        event.preventDefault();
        if (elements.rsiToggle && !elements.rsiToggle.checked) {
          elements.rsiToggle.checked = true;
        }
        await loadRsi(false);
      });
    }

    elements.zoomInBtn?.addEventListener('click', () => {
      zoomChartByFactor(ZOOM_CONSTANTS.IN_FACTOR);
    });
    elements.zoomOutBtn?.addEventListener('click', () => {
      zoomChartByFactor(ZOOM_CONSTANTS.OUT_FACTOR);
    });
    elements.resetZoomBtn?.addEventListener('click', () => {
      resetZoomInternal();
    });
  }

  async function loadRsi(autoToggle) {
    if (!elements.rsiPeriodEl) return;
    const period = parseInt(elements.rsiPeriodEl.value, 10);
    if (!Number.isInteger(period) || period <= 1) {
      alert('RSI period must be greater than 1.');
      return;
    }
    try {
      const res = await fetch(`/api/stocks/${encodeURIComponent(symbol)}/rsi?interval=${encodeURIComponent(state.data.currentInterval)}&period=${period}`);
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to fetch RSI');
      }
      const data = await res.json();
      const values = Array.isArray(data.values) ? data.values : [];
      renderRsiChart(values, period);
    } catch (err) {
      console.error(err);
      if (autoToggle && elements.rsiToggle) {
        elements.rsiToggle.checked = false;
      }
      alert(err.message || 'Unable to load RSI data.');
    }
  }

  function hideRsiChart() {
    if (elements.rsiWrapper) {
      elements.rsiWrapper.classList.add('hidden');
    }
    if (state.charts.rsi) {
      state.charts.rsi.destroy();
      state.charts.rsi = null;
    }
  }

  function renderRsiChart(values, period) {
    if (!elements.rsiWrapper || !elements.rsiCanvas) return;
    elements.rsiWrapper.classList.remove('hidden');
    const points = mapIndicatorToPoints(values);
    if (state.charts.rsi) {
      state.charts.rsi.data.datasets = buildRsiDatasets(points, period);
      state.charts.rsi.update();
      return;
    }
    state.charts.rsi = new Chart(elements.rsiCanvas, {
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
    const upper = buildHorizontalLineDataset(`RSI ${RSI_THRESHOLDS.OVERBOUGHT}`, RSI_THRESHOLDS.OVERBOUGHT, points, '#f97316');
    const lower = buildHorizontalLineDataset(`RSI ${RSI_THRESHOLDS.OVERSOLD}`, RSI_THRESHOLDS.OVERSOLD, points, '#0ea5e9');
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
    if (!elements.overlayListEl) return;
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
      state.ui.overlays.delete(overlay.id);
      li.remove();
      updatePriceChart();
    });
    elements.overlayListEl.appendChild(li);
  }

  function renderNews(articles) {
    if (!elements.newsListEl) return;
    if (!articles.length) {
      elements.newsListEl.innerHTML = '<li class="muted">No recent articles found.</li>';
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
    elements.newsListEl.innerHTML = items.join('');
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
