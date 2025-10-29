(function () {
  const singleListContainer = document.getElementById('singleListContainer');
  const viewSelector = document.getElementById('viewSelector');
  const weekSelector = document.getElementById('weekSelector');
  const listTitle = document.getElementById('listTitle');
  const addStockForm = document.getElementById('addStockForm');
  const formMessage = document.getElementById('formMessage');
  const refreshBtn = document.getElementById('refreshBtn');
  const lastUpdated = document.getElementById('lastUpdated');

  const editModal = document.getElementById('editModal');
  const editForm = document.getElementById('editStockForm');

  const editId = document.getElementById('edit_id');
  const editSymbol = document.getElementById('edit_symbol');
  const editInitialPrice = document.getElementById('edit_initial_price');
  const editDateSpotted = document.getElementById('edit_date_spotted');
  const editDateBought = document.getElementById('edit_date_bought');
  const editListType = document.getElementById('edit_list_type');
  const editReason = document.getElementById('edit_reason');

  const LIST_TITLES = {
    A: 'A List (Trade soon)',
    B: 'B List (Wait)',
    PA: 'Stored A',
    PB: 'Stored B',
  };

  function listTitleFor(type) {
    return LIST_TITLES[type] || 'Stocks';
  }

  function isActiveList(type) {
    return type === 'A' || type === 'B';
  }

  function isStoredList(type) {
    return type === 'PA' || type === 'PB';
  }

  function toStoredType(type) {
    if (type === 'A') return 'PA';
    if (type === 'B') return 'PB';
    return type;
  }

  function toActiveType(type) {
    if (type === 'PA') return 'A';
    if (type === 'PB') return 'B';
    return type;
  }

  function setTodayDate(input) {
    try {
      const today = new Date();
      const yyyy = today.getFullYear();
      const mm = String(today.getMonth() + 1).padStart(2, '0');
      const dd = String(today.getDate()).padStart(2, '0');
      input.value = `${yyyy}-${mm}-${dd}`;
    } catch (_) {}
  }

  const dateSpottedInput = document.getElementById('date_spotted');
  const dateBoughtInput = document.getElementById('date_bought');
  if (dateSpottedInput) setTodayDate(dateSpottedInput);

  function formatPercent(change) {
    if (change === null || change === undefined || isNaN(change)) return '—';
    const sign = change > 0 ? '+' : '';
    return `${sign}${change.toFixed(2)}%`;
  }

  function calculateDuration(dateStr) {
    if (!dateStr) return '—';
    const parsed = new Date(`${dateStr}T00:00:00`);
    if (Number.isNaN(parsed.getTime())) return '—';
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const diffMs = today.getTime() - parsed.getTime();
    const diffDays = Math.floor(diffMs / 86400000);
    return diffDays >= 0 ? diffDays : '—';
  }

  function renderStockTable(stocks, listType) {
    const label = listTitleFor(listType);
    if (!stocks || stocks.length === 0) {
      return `<div class="empty">No stocks in ${escapeHtml(label)}.</div>`;
    }

    const activeList = isActiveList(listType);
    const storedList = isStoredList(listType);

    const rows = stocks.map((s) => {
      const id = s.id;
      const symbol = s.symbol || '';
      const initial = s.initial_price != null ? Number(s.initial_price).toFixed(2) : '—';
      const reason = s.reason || '';
      const date = s.date_spotted || '';
      const dateBought = s.date_bought || '';
      const duration = calculateDuration(dateBought);
      const actionButtons = [
        `<button class="btn btn-small btn-edit" data-action="edit" data-id="${id}">Edit</button>`,
      ];
      if (activeList) {
        actionButtons.push(`<button class="btn btn-small btn-secondary" data-action="store" data-id="${id}">Store</button>`);
      } else if (storedList) {
        actionButtons.push(`<button class="btn btn-small btn-secondary" data-action="restore" data-id="${id}">Return</button>`);
      }
      actionButtons.push(`<button class="btn btn-small btn-danger" data-action="delete" data-id="${id}">Delete</button>`);
      return `
        <tr data-id="${id}" data-symbol="${symbol}" data-list-type="${listType}" data-date-spotted="${date}" data-date-bought="${dateBought}">
          <td class="col-symbol"><a href="/stocks/${encodeURIComponent(symbol)}">${symbol}</a></td>
          <td>${initial}</td>
          <td class="col-current">—</td>
          <td class="col-change">—</td>
          <td class="col-reason">${escapeHtml(reason)}</td>
          <td>${date}</td>
          <td>${dateBought}</td>
          <td class="col-duration">${duration}</td>
          <td class="col-actions">
            ${actionButtons.join(' ')}
          </td>
        </tr>
      `;
    }).join('');

    return `
      <table class="table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Initial Price</th>
            <th>Current Price</th>
            <th>% Change</th>
            <th>Reason</th>
            <th>Date Spotted</th>
            <th>Date Bought</th>
            <th>Duration (days)</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${rows}
        </tbody>
      </table>
    `;
  }

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.innerText = str || '';
    return div.innerHTML;
  }

  async function loadStocks() {
    singleListContainer.innerHTML = loader();
    try {
      const selectedWeek = weekSelector?.value || '';
      let url = '/api/stocks';
      if (selectedWeek) {
        const params = new URLSearchParams({ week: selectedWeek });
        url = `${url}?${params.toString()}`;
      }
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to load stocks');
      const data = await res.json();
      const selected = (viewSelector?.value || 'A').toUpperCase();
      const list = Array.isArray(data?.[selected]) ? data[selected] : [];
      listTitle.textContent = listTitleFor(selected);
      singleListContainer.innerHTML = renderStockTable(list, selected);
    } catch (err) {
      singleListContainer.innerHTML = errorBox('Could not load list.');
    }
  }

  async function loadWeeks() {
    if (!weekSelector) return;
    try {
      const res = await fetch('/api/weeks');
      if (!res.ok) throw new Error('Failed to load weeks');
      const weeks = await res.json();
      const previousValue = weekSelector.value;
      while (weekSelector.options.length > 1) {
        weekSelector.remove(1);
      }
      if (Array.isArray(weeks)) {
        weeks.forEach((week) => {
          if (!week || !week.week_end) return;
          const option = document.createElement('option');
          option.value = week.week_end;
          option.textContent = week.week_label || `Week of ${week.week_end}`;
          weekSelector.appendChild(option);
        });
      }
      if (previousValue) {
        const hasValue = Array.from(weekSelector.options).some((opt) => opt.value === previousValue);
        weekSelector.value = hasValue ? previousValue : '';
      } else {
        weekSelector.value = '';
      }
    } catch (err) {
      console.error(err);
    }
  }

  function loader() {
    return `<div class="loader">Loading…</div>`;
  }

  function errorBox(msg) {
    return `<div class="error">${escapeHtml(msg)}</div>`;
  }

  async function updatePrices() {
    setRefreshing(true);
    try {
      const res = await fetch('/api/stocks/prices');
      if (!res.ok) throw new Error('Failed to fetch prices');
      const list = await res.json();
      const now = new Date().toLocaleTimeString();
      lastUpdated.textContent = `Last updated: ${now}`;
      applyPrices(list);
    } catch (err) {
      // leave a subtle note
      lastUpdated.textContent = 'Last updated: failed';
    } finally {
      setRefreshing(false);
    }
  }

  function setRefreshing(isRefreshing) {
    if (isRefreshing) {
      refreshBtn.setAttribute('disabled', 'true');
      refreshBtn.textContent = 'Refreshing…';
    } else {
      refreshBtn.removeAttribute('disabled');
      refreshBtn.textContent = 'Refresh Prices';
    }
  }

  function applyPrices(priceList) {
    if (!Array.isArray(priceList)) return;
    const bySymbol = Object.create(null);
    for (const it of priceList) {
      if (!it || !it.symbol) continue;
      bySymbol[it.symbol] = it;
    }
    const rows = singleListContainer.querySelectorAll('tbody tr');
    rows.forEach((row) => {
      const symbol = row.getAttribute('data-symbol');
      const priceCell = row.querySelector('.col-current');
      const changeCell = row.querySelector('.col-change');
      const info = bySymbol[symbol];
      if (!info) return;
      const cp = info.current_price;
      const pct = info.percent_change;
      priceCell.textContent = (cp == null || isNaN(cp)) ? '—' : Number(cp).toFixed(2);
      changeCell.textContent = formatPercent(pct);
      changeCell.classList.remove('positive-change', 'negative-change');
      if (pct != null && !isNaN(pct)) {
        if (pct > 0) changeCell.classList.add('positive-change');
        if (pct < 0) changeCell.classList.add('negative-change');
      }
    });
  }

  async function changeListType(id, targetType) {
    if (!id || !targetType) return;
    try {
      const res = await fetch(`/api/stocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ list_type: targetType }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to update');
      }
      await loadStocks();
    } catch (err) {
      alert(err.message || 'Error updating');
    }
  }

  addStockForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    formMessage.textContent = '';
    const payload = {
      symbol: (document.getElementById('symbol').value || '').trim().toUpperCase(),
      initial_price: document.getElementById('initial_price').value,
      reason: document.getElementById('reason').value,
      date_spotted: document.getElementById('date_spotted').value,
      date_bought: dateBoughtInput?.value || '',
      list_type: document.getElementById('list_type').value,
    };
    if (!payload.symbol) {
      formMessage.textContent = 'Symbol is required';
      return;
    }
    if (isNaN(Number(payload.initial_price))) {
      formMessage.textContent = 'Initial price must be a number';
      return;
    }
    const btn = addStockForm.querySelector('button[type="submit"]');
    btn.disabled = true;
    btn.textContent = 'Adding…';
    try {
      const res = await fetch('/api/stocks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to add stock');
      }
      await loadStocks();
      await loadWeeks();
      addStockForm.reset();
      setTodayDate(document.getElementById('date_spotted'));
      if (dateBoughtInput) dateBoughtInput.value = '';
      formMessage.textContent = 'Stock added.';
    } catch (err) {
      formMessage.textContent = err.message || 'Error adding stock';
    } finally {
      btn.disabled = false;
      btn.textContent = 'Add Stock';
    }
  });

  function openModal() {
    document.body.classList.add('modal-open');
    editModal.classList.remove('hidden');
    editModal.setAttribute('aria-hidden', 'false');
  }
  function closeModal() {
    document.body.classList.remove('modal-open');
    editModal.classList.add('hidden');
    editModal.setAttribute('aria-hidden', 'true');
  }
  editModal.addEventListener('click', (e) => {
    if (e.target.matches('[data-close-modal]')) closeModal();
  });

  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = btn.getAttribute('data-id');
    if (!action || !id) return;
    const row = btn.closest('tr');
    const currentType = row?.getAttribute('data-list-type') || (viewSelector?.value || 'A');

    if (action === 'edit') {
      // Find row data to prefill
      if (!row) return;
      const cells = row.querySelectorAll('td');
      editId.value = id;
      editSymbol.value = row.getAttribute('data-symbol') || '';
      editInitialPrice.value = (cells[1]?.textContent || '').replace(/,/g, '');
      editReason.value = row.querySelector('.col-reason')?.textContent || '';
      editDateSpotted.value = row.getAttribute('data-date-spotted') || cells[5]?.textContent || '';
      if (editDateBought) {
        editDateBought.value = row.getAttribute('data-date-bought') || cells[6]?.textContent || '';
      }
      // Use the row's current list type
      editListType.value = currentType;
      openModal();
    } else if (action === 'delete') {
      if (!confirm('Delete this stock?')) return;
      try {
        const res = await fetch(`/api/stocks/${id}`, { method: 'DELETE' });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.error || 'Failed to delete');
        }
        await loadStocks();
        await updatePrices();
      } catch (err) {
        alert(err.message || 'Error deleting');
      }
    } else if (action === 'store') {
      const targetType = toStoredType(currentType);
      if (targetType !== currentType) await changeListType(id, targetType);
    } else if (action === 'restore') {
      const targetType = toActiveType(currentType);
      if (targetType !== currentType) await changeListType(id, targetType);
    }
  });

  editForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = editId.value;
    const payload = {
      symbol: (editSymbol.value || '').trim().toUpperCase(),
      initial_price: editInitialPrice.value,
      date_spotted: editDateSpotted.value,
      date_bought: editDateBought?.value || '',
      list_type: editListType.value,
      reason: editReason.value,
    };
    if (!payload.symbol) return alert('Symbol is required');
    if (isNaN(Number(payload.initial_price))) return alert('Initial price must be a number');

    const submitBtn = editForm.querySelector('button[type="submit"]');
    submitBtn.disabled = true;
    submitBtn.textContent = 'Saving…';
    try {
      const res = await fetch(`/api/stocks/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || 'Failed to update');
      }
      await loadStocks();
      await updatePrices();
      closeModal();
    } catch (err) {
      alert(err.message || 'Error updating');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Save Changes';
    }
  });

  refreshBtn.addEventListener('click', () => updatePrices());
  viewSelector?.addEventListener('change', async () => {
    await loadStocks();
    await updatePrices();
  });
  weekSelector?.addEventListener('change', async () => {
    await loadStocks();
    await updatePrices();
  });

  // Initial load
  (async function init() {
    await loadStocks();
    await loadWeeks();
    await updatePrices();
    setInterval(updatePrices, 60000); // 60s auto-refresh
  })();
})();
