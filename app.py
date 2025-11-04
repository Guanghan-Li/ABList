from __future__ import annotations

import importlib
import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, date
from typing import Any, Dict, Iterable, List, Optional, Tuple

import hashlib

from flask import Flask, jsonify, render_template, request

from flask import abort

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
_file_lock = threading.Lock()  # Legacy fallback path
VALID_LIST_TYPES = ("A", "B", "PA", "PB")
VALID_LIST_TYPES_SET = set(VALID_LIST_TYPES)

USE_ORJSON_ENV = (os.getenv("USE_ORJSON", "auto") or "auto").strip().lower()
try:
    import orjson  # type: ignore
except Exception:  # pragma: no cover - fallback when orjson missing
    orjson = None

USE_ORJSON = bool(orjson) and USE_ORJSON_ENV != "false"

ENABLE_HTTP_CACHE = (os.getenv("ENABLE_HTTP_CACHE", "true") or "true").strip().lower() not in {"0", "false", "no"}


def _resolve_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


PRICE_TTL_SECONDS = max(1, _resolve_int_env("PRICE_TTL_SECONDS", 15))

DATA_PATH = os.getenv("STOCKS_PATH")
if DATA_PATH:
    DATA_PATH = os.path.abspath(DATA_PATH)
else:
    DATA_PATH = STOCKS_FILE

_state_lock = threading.RLock()
stocks: Dict[str, Dict[str, Any]] = {}
by_list: Dict[str, List[str]] = {lt: [] for lt in VALID_LIST_TYPES}
rendered_lists: Dict[str, List[Dict[str, Any]]] = {lt: [] for lt in VALID_LIST_TYPES}
id_index: Dict[str, str] = {}
state_version: int = 0
_legacy_mode = False


def _ensure_file() -> None:
    """Ensure the backing JSON file exists so legacy tools keep working."""
    path = DATA_PATH or STOCKS_FILE
    if os.path.exists(path):
        return
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[]")


def _json_loads(data: bytes) -> Any:
    if not data:
        return []
    if USE_ORJSON and orjson is not None:
        return orjson.loads(data)
    return json.loads(data.decode("utf-8"))


def _json_dumps(data: Any) -> bytes:
    if USE_ORJSON and orjson is not None:
        return orjson.dumps(data, option=orjson.OPT_INDENT_2)
    return json.dumps(data, indent=2).encode("utf-8")


def _normalize_list_type(list_type: Optional[str]) -> str:
    lt = (list_type or "").strip().upper()
    if lt not in VALID_LIST_TYPES_SET:
        return "B"
    return lt


def _stock_api_module():
    # Lazy import to avoid pulling heavy dependencies (pandas, numpy) during tests
    return importlib.import_module("stock_api")


def fetch_price_history(*args, **kwargs):
    return _stock_api_module().fetch_price_history(*args, **kwargs)


def compute_sma(*args, **kwargs):
    return _stock_api_module().compute_sma(*args, **kwargs)


def compute_ema(*args, **kwargs):
    return _stock_api_module().compute_ema(*args, **kwargs)


def compute_rsi(*args, **kwargs):
    return _stock_api_module().compute_rsi(*args, **kwargs)


def fetch_news(*args, **kwargs):
    return _stock_api_module().fetch_news(*args, **kwargs)


def fetch_overview(*args, **kwargs):
    return _stock_api_module().fetch_overview(*args, **kwargs)


def get_current_prices(*args, **kwargs):
    return _stock_api_module().get_current_prices(*args, **kwargs)


def calculate_percent_change(*args, **kwargs):
    return _stock_api_module().calculate_percent_change(*args, **kwargs)


class PriceCache:
    def __init__(self, ttl_seconds: int = 15):
        self.ttl = max(1, ttl_seconds)
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._cache: Dict[str, Tuple[Optional[float], float]] = {}
        self._pending: set[str] = set()
        self._shutdown = False
        self._worker: Optional[threading.Thread] = None

    def stop(self) -> None:
        with self._cond:
            self._shutdown = True
            self._cond.notify_all()
        worker = self._worker
        if worker:
            worker.join(timeout=1)

    def get_many(self, symbols: Iterable[str], *, schedule_refresh: bool = True) -> Dict[str, Optional[float]]:
        now = time.time()
        results: Dict[str, Optional[float]] = {}
        unique_symbols = []
        seen = set()
        for sym in symbols:
            normalized = (sym or "").strip().upper()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique_symbols.append(normalized)

        to_refresh: set[str] = set()
        with self._cond:
            start_worker = False
            for sym in unique_symbols:
                cached = self._cache.get(sym)
                if cached and now - cached[1] <= self.ttl:
                    results[sym] = cached[0]
                else:
                    if cached:
                        results[sym] = cached[0]
                    if schedule_refresh and sym not in self._pending:
                        self._pending.add(sym)
                        to_refresh.add(sym)
                        start_worker = True
            if start_worker and not self._shutdown:
                self._ensure_worker_locked()
            if to_refresh and not self._shutdown:
                self._cond.notify()
        return results

    def prime(self, symbol: str, price: Optional[float]) -> None:
        normalized = (symbol or "").strip().upper()
        if not normalized:
            return
        with self._cond:
            self._cache[normalized] = (price, time.time())
            self._pending.discard(normalized)

    def _ensure_worker_locked(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        worker = threading.Thread(target=self._worker_loop, name="price-cache-worker", daemon=True)
        self._worker = worker
        worker.start()

    def _worker_loop(self) -> None:
        retry_delay = 1.0
        while True:
            with self._cond:
                while not self._pending and not self._shutdown:
                    self._cond.wait()
                if self._shutdown:
                    return
                batch = list(self._pending)
                self._pending.clear()
            if not batch:
                continue
            start = time.time()
            try:
                prices = get_current_prices(batch) or {}
            except Exception as exc:  # pragma: no cover - network failure path
                app.logger.exception("Price batch fetch failed for %d symbols: %s", len(batch), exc)
                time.sleep(min(retry_delay, 30.0))
                retry_delay = min(retry_delay * 2, 30.0)
                with self._cond:
                    self._pending.update(batch)
                continue

            retry_delay = 1.0
            fetched_at = time.time()
            with self._cond:
                for sym in batch:
                    price = prices.get(sym)
                    self._cache[sym] = (price, fetched_at)
            duration_ms = (time.time() - start) * 1000.0
            app.logger.info("Price cache refreshed %d symbols in %.1fms", len(batch), duration_ms)

def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    return sym


def _parse_windows_param(raw: Optional[str]) -> List[int]:
    if not raw:
        return []
    windows: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            windows.append(value)
    return windows


def _json_error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _build_rendered_entry(symbol: str, row: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(row)
    payload["symbol"] = symbol
    return payload


def _rebuild_indexes_from_stocks(source: Optional[Dict[str, Dict[str, Any]]] = None
                                ) -> Tuple[Dict[str, List[str]], Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
    base = source or stocks
    lists: Dict[str, List[str]] = {lt: [] for lt in VALID_LIST_TYPES}
    rendered: Dict[str, List[Dict[str, Any]]] = {lt: [] for lt in VALID_LIST_TYPES}
    ids: Dict[str, str] = {}
    for symbol, row in base.items():
        if not isinstance(row, dict):
            continue
        list_type = _normalize_list_type(row.get("list_type"))
        lists.setdefault(list_type, []).append(symbol)
        rendered.setdefault(list_type, []).append(_build_rendered_entry(symbol, row))
        row_id = row.get("id")
        if row_id:
            ids[str(row_id)] = symbol
    return lists, rendered, ids


def _legacy_read_stocks() -> List[Dict[str, Any]]:
    data: List[Dict[str, Any]] = []
    path = DATA_PATH or STOCKS_FILE
    if not os.path.exists(path):
        return data
    with _file_lock:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return data
    if isinstance(loaded, list):
        data = [item for item in loaded if isinstance(item, dict)]
    elif isinstance(loaded, dict):
        payload = loaded.get("stocks")
        if isinstance(payload, list):
            data = [item for item in payload if isinstance(item, dict)]
    return data


def _normalize_loaded_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        symbol = _normalize_symbol(row.get("symbol"))
    except ValueError:
        return None
    normalized: Dict[str, Any] = dict(row)
    normalized["symbol"] = symbol
    normalized["list_type"] = _normalize_list_type(normalized.get("list_type"))
    if "id" in normalized and normalized["id"] is None:
        normalized.pop("id")
    _apply_week_defaults(normalized)
    return normalized


def _load_stocks_from_disk() -> bool:
    global stocks, by_list, rendered_lists, id_index, state_version, _legacy_mode
    path = DATA_PATH or STOCKS_FILE
    if not os.path.exists(path):
        stocks = {}
        by_list, rendered_lists, id_index = _rebuild_indexes_from_stocks({})
        state_version = 1
        app.logger.info("Stock store initialized: no existing data file found at %s", path)
        return True
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        loaded = _json_loads(raw)
    except Exception as exc:  # pragma: no cover - fallback path
        _legacy_mode = True
        app.logger.exception("Stock store initialization failed; falling back to legacy disk reads: %s", exc)
        return False

    rows: List[Dict[str, Any]] = []
    if isinstance(loaded, list):
        rows = [item for item in loaded if isinstance(item, dict)]
    elif isinstance(loaded, dict):
        payload = loaded.get("stocks")
        if isinstance(payload, list):
            rows = [item for item in payload if isinstance(item, dict)]

    new_stocks: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        normalized = _normalize_loaded_row(row)
        if not normalized:
            continue
        new_stocks[normalized["symbol"]] = normalized

    stocks = new_stocks
    by_list_local, rendered_local, ids_local = _rebuild_indexes_from_stocks(new_stocks)
    by_list = by_list_local
    rendered_lists = rendered_local
    id_index = ids_local
    state_version = 1
    app.logger.info("Stock store initialized from %s with %d entries", path, len(stocks))
    return True


def _save_state(new_stocks: Dict[str, Dict[str, Any]]) -> None:
    global stocks, by_list, rendered_lists, id_index, state_version
    by_local, rendered_local, ids_local = _rebuild_indexes_from_stocks(new_stocks)
    stocks = new_stocks
    by_list = by_local
    rendered_lists = rendered_local
    id_index = ids_local
    state_version = state_version + 1 if state_version else 1


def _legacy_write_stocks(records: List[Dict[str, Any]]) -> None:
    path = DATA_PATH or STOCKS_FILE
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    with _file_lock:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2)


def _atomic_write_json(path: str, data: Any) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".stocks-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            payload = _json_dumps(data)
            tmp_file.write(payload)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def _legacy_upsert(row: Dict[str, Any], previous_symbol: Optional[str] = None) -> Dict[str, Any]:
    records = _legacy_read_stocks()
    target_symbol = previous_symbol or row.get("symbol")
    normalized_symbol = (target_symbol or "").strip().upper()
    replacement_symbol = (row.get("symbol") or "").strip().upper()
    updated = False
    for idx, existing in enumerate(records):
        if not isinstance(existing, dict):
            continue
        sym = (existing.get("symbol") or "").strip().upper()
        if sym in {normalized_symbol, replacement_symbol}:
            records[idx] = dict(row)
            updated = True
            break
    if not updated:
        records.append(dict(row))
    _legacy_write_stocks(records)
    return row


def _legacy_delete(symbol: str) -> Optional[Dict[str, Any]]:
    records = _legacy_read_stocks()
    normalized_symbol = (symbol or "").strip().upper()
    new_records = []
    removed = None
    for record in records:
        if not isinstance(record, dict):
            continue
        sym = (record.get("symbol") or "").strip().upper()
        if sym == normalized_symbol and removed is None:
            removed = record
            continue
        new_records.append(record)
    if removed is not None:
        _legacy_write_stocks(new_records)
    return removed


def _persist_state(new_stocks: Dict[str, Dict[str, Any]]) -> None:
    path = DATA_PATH or STOCKS_FILE
    payload = list(new_stocks.values())
    _atomic_write_json(path, payload)
    _save_state(new_stocks)
    app.logger.info("Atomic stock store write complete (%d records)", len(new_stocks))


def _upsert_stock(row: Dict[str, Any], previous_symbol: Optional[str] = None) -> Dict[str, Any]:
    sanitized = _normalize_loaded_row(row) or {}
    if not sanitized:
        raise ValueError("invalid stock payload")
    symbol = sanitized["symbol"]
    with _state_lock:
        if _legacy_mode:
            return _legacy_upsert(sanitized, previous_symbol)
        new_stocks = dict(stocks)
        if previous_symbol and previous_symbol in new_stocks and previous_symbol != symbol:
            new_stocks.pop(previous_symbol, None)
        new_stocks[symbol] = sanitized
        try:
            _persist_state(new_stocks)
        except Exception:
            app.logger.exception("Failed to persist stock %s", symbol)
            raise
    return sanitized


def _delete_stock(symbol: str) -> Optional[Dict[str, Any]]:
    normalized = (symbol or "").strip().upper()
    with _state_lock:
        if _legacy_mode:
            return _legacy_delete(normalized)
        if normalized not in stocks:
            return None
        new_stocks = dict(stocks)
        removed = new_stocks.pop(normalized, None)
        try:
            _persist_state(new_stocks)
        except Exception:
            app.logger.exception("Failed to delete stock %s", normalized)
            raise
    return removed


def _read_stocks() -> List[Dict[str, Any]]:
    if _legacy_mode:
        return _legacy_read_stocks()
    return [_build_rendered_entry(symbol, row) for symbol, row in stocks.items()]

_ORIGINAL_READ_STOCKS = _read_stocks


def _get_stock_by_id(stock_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    if not stock_id:
        return None
    if _legacy_mode:
        for row in _legacy_read_stocks():
            if not isinstance(row, dict):
                continue
            if str(row.get("id")) == str(stock_id):
                symbol = (row.get("symbol") or "").strip().upper()
                if not symbol:
                    return None
                row_copy = dict(row)
                row_copy["symbol"] = symbol
                return symbol, row_copy
        return None
    symbol = id_index.get(str(stock_id))
    if not symbol:
        return None
    row = stocks.get(symbol)
    if not row:
        return None
    return symbol, dict(row)


def _filter_rendered_items(items: Iterable[Dict[str, Any]], week_filter: str) -> List[Dict[str, Any]]:
    if not week_filter:
        return [dict(item) for item in items]
    target = week_filter.strip()
    return [
        dict(item)
        for item in items
        if (item.get("week_end") or "").strip() == target
    ]


def _flatten_rendered_lists(source: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for lst in VALID_LIST_TYPES:
        merged.extend([dict(item) for item in source.get(lst, [])])
    return merged


def _make_etag_token(*parts: Any) -> str:
    raw = ":".join(str(part) for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return digest


def _maybe_set_cache_headers(response, *etag_parts: Any):
    if not ENABLE_HTTP_CACHE:
        return response
    token = _make_etag_token(*etag_parts)
    response.set_etag(token, weak=True)
    response.headers["Cache-Control"] = "public, max-age=15, stale-while-revalidate=60"
    return response


def _parse_date(date_str: Optional[str]) -> date:
    if not date_str:
        return datetime.utcnow().date()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return datetime.utcnow().date()


def _get_week_end(date_obj: date) -> date:
    monday = date_obj - timedelta(days=date_obj.weekday())
    return monday + timedelta(days=6)


def _format_week_label(sunday_date: date) -> str:
    return f"Week of {sunday_date.strftime('%m/%d/%y')}"


def _calculate_week_info(date_str: Optional[object] = None) -> Dict[str, str]:
    if isinstance(date_str, datetime):
        base_date = date_str.date()
    elif isinstance(date_str, date):
        base_date = date_str
    else:
        base_date = _parse_date(date_str if isinstance(date_str, str) else None)
    week_end_date = _get_week_end(base_date)
    return {
        "week_end": week_end_date.isoformat(),
        "week_label": _format_week_label(week_end_date),
    }


def _apply_week_defaults(row: Dict[str, Any]) -> None:
    if not isinstance(row, dict):
        return
    has_date_added = bool(row.get("date_added"))
    has_week_end = bool(row.get("week_end"))
    has_week_label = bool(row.get("week_label"))
    if has_date_added and has_week_end and has_week_label:
        return
    source_date = row.get("date_spotted") or row.get("date_added")
    base_date = _parse_date(source_date if isinstance(source_date, str) else None)
    week_info = _calculate_week_info(base_date)
    if not has_date_added:
        row["date_added"] = base_date.isoformat()
    if not has_week_end:
        row["week_end"] = week_info["week_end"]
    if not has_week_label:
        row["week_label"] = week_info["week_label"]


if not _load_stocks_from_disk():
    app.logger.warning("Using legacy disk-backed stock access path; performance optimizations disabled.")

price_cache = PriceCache(ttl_seconds=PRICE_TTL_SECONDS)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stocks/<symbol>")
def stock_detail(symbol: str):
    try:
        sym = _normalize_symbol(symbol)
    except ValueError:
        abort(404)
    return render_template("stock_detail.html", symbol=sym)


@app.route("/api/stocks", methods=["GET"])
def get_stocks():
    week_filter = (request.args.get("week") or "").strip()
    list_filter_raw = request.args.get("list")
    if _legacy_mode:
        records = _legacy_read_stocks()
        if week_filter:
            records = [r for r in records if (r.get("week_end") or "").strip() == week_filter]
        grouped = {lt: [] for lt in VALID_LIST_TYPES}
        for record in records:
            lt = _normalize_list_type(record.get("list_type"))
            grouped.setdefault(lt, []).append(record)
        if list_filter_raw:
            lt = _normalize_list_type(list_filter_raw)
            grouped = {lt: grouped.get(lt, [])}
        return jsonify(grouped)

    if list_filter_raw:
        lt = _normalize_list_type(list_filter_raw)
        items = rendered_lists.get(lt, [])
        payload = {lt: _filter_rendered_items(items, week_filter)}
    else:
        payload = {
            lt: _filter_rendered_items(rendered_lists.get(lt, []), week_filter)
            for lt in VALID_LIST_TYPES
        }
    response = jsonify(payload)
    return _maybe_set_cache_headers(response, state_version, list_filter_raw or "all", week_filter or "all")


@app.route("/api/stocks/search", methods=["GET"])
def search_stocks():
    raw_query = request.args.get("q")
    query = (raw_query or "").strip().upper()
    if not query:
        return jsonify({"results": []})
    if len(query) > 32:
        return _json_error("query must be 32 characters or fewer", 400)

    list_filter_raw = request.args.get("list")
    universe = _read_stocks()
    if list_filter_raw:
        list_filter = _normalize_list_type(list_filter_raw)
        universe = [row for row in universe if _normalize_list_type(row.get("list_type")) == list_filter]

    matches: List[Dict[str, Optional[str]]] = []
    upper_query = query.upper()
    for stock in universe:
        if not isinstance(stock, dict):
            continue
        symbol = (stock.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        if upper_query in symbol:
            matches.append({
                "id": stock.get("id"),
                "symbol": symbol,
                "list_type": _normalize_list_type(stock.get("list_type")),
            })

    matches.sort(key=lambda item: item["symbol"])
    return jsonify({"results": matches[:10]})


@app.route("/api/weeks", methods=["GET"])
def get_weeks():
    if _legacy_mode:
        stocks_iterable = _legacy_read_stocks()
    else:
        stocks_iterable = _flatten_rendered_lists(rendered_lists)
    weeks: Dict[str, str] = {}
    for stock in stocks_iterable:
        if not isinstance(stock, dict):
            continue
        week_end = (stock.get("week_end") or "").strip()
        if not week_end:
            continue
        week_label = stock.get("week_label")
        if not week_label:
            week_label = _calculate_week_info(week_end)["week_label"]
        if week_end not in weeks:
            weeks[week_end] = week_label
    week_list = [{"week_end": key, "week_label": value} for key, value in weeks.items()]
    week_list.sort(key=lambda item: item["week_end"], reverse=True)
    return jsonify(week_list)


@app.route("/api/stocks", methods=["POST"])
def create_stock():
    payload = request.get_json(force=True, silent=True) or {}
    symbol = (payload.get("symbol") or "").strip().upper()
    initial_price = payload.get("initial_price")
    reason = (payload.get("reason") or "").strip()
    date_spotted = (payload.get("date_spotted") or "").strip()
    date_bought = (payload.get("date_bought") or "").strip()
    list_type = (payload.get("list_type") or "B").strip().upper()
    if list_type not in VALID_LIST_TYPES_SET:
        list_type = "B"

    if not symbol:
        return jsonify({"error": "symbol is required"}), 400
    try:
        initial_price = float(initial_price)
    except Exception:
        return jsonify({"error": "initial_price must be a number"}), 400

    today = datetime.utcnow().date()
    week_info = _calculate_week_info(today)
    stock = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "initial_price": initial_price,
        "reason": reason,
        "date_spotted": date_spotted,
        "date_bought": date_bought,
        "date_added": today.isoformat(),
        "week_end": week_info["week_end"],
        "week_label": week_info["week_label"],
        "list_type": list_type,
    }
    _apply_week_defaults(stock)

    try:
        stored = _upsert_stock(stock)
    except Exception:
        return _json_error("failed to persist stock", 500)

    prices = price_cache.get_many([symbol], schedule_refresh=False)
    current_price = prices.get(symbol)
    pct = calculate_percent_change(initial_price, current_price)

    return jsonify({"stock": stored, "current_price": current_price, "percent_change": pct})


@app.route("/api/stocks/<stock_id>", methods=["PUT"])
def update_stock(stock_id: str):
    payload = request.get_json(force=True, silent=True) or {}
    target = _get_stock_by_id(stock_id)
    if not target:
        return jsonify({"error": "stock not found"}), 404
    current_symbol, current_row = target
    updated = dict(current_row)

    # Update editable fields if provided
    if "symbol" in payload:
        sym = (payload.get("symbol") or "").strip().upper()
        if not sym:
            return jsonify({"error": "symbol cannot be empty"}), 400
        updated["symbol"] = sym
    if "initial_price" in payload:
        try:
            updated["initial_price"] = float(payload.get("initial_price"))
        except Exception:
            return jsonify({"error": "initial_price must be a number"}), 400
    if "reason" in payload:
        updated["reason"] = (payload.get("reason") or "").strip()
    if "date_spotted" in payload:
        updated["date_spotted"] = (payload.get("date_spotted") or "").strip()
    if "date_bought" in payload:
        updated["date_bought"] = (payload.get("date_bought") or "").strip()
    if "list_type" in payload:
        lt = (payload.get("list_type") or "B").strip().upper()
        if lt not in VALID_LIST_TYPES_SET:
            lt = "B"
        updated["list_type"] = lt

    _apply_week_defaults(updated)

    try:
        stored = _upsert_stock(updated, previous_symbol=current_symbol)
    except Exception:
        return _json_error("failed to persist stock changes", 500)

    return jsonify(stored)


@app.route("/api/stocks/<stock_id>", methods=["DELETE"])
def delete_stock(stock_id: str):
    target = _get_stock_by_id(stock_id)
    if not target:
        return jsonify({"error": "stock not found"}), 404
    symbol, _ = target
    try:
        _delete_stock(symbol)
    except Exception:
        return _json_error("failed to delete stock", 500)
    return jsonify({"success": True})


@app.route("/api/stocks/prices", methods=["GET"])
def get_prices():
    if _legacy_mode:
        records = _legacy_read_stocks()
        symbols = [(record.get("symbol") or "").strip().upper() for record in records if record.get("symbol")]
        sym_to_initial = {
            (record.get("symbol") or "").strip().upper(): record.get("initial_price")
            for record in records if record.get("symbol")
        }
    else:
        symbols = []
        for lt in VALID_LIST_TYPES:
            symbols.extend(by_list.get(lt, []))
        sym_to_initial = {
            symbol: stocks.get(symbol, {}).get("initial_price")
            for symbol in symbols
        }

    prices = price_cache.get_many(symbols)
    result = []
    for sym in symbols:
        if not sym:
            continue
        initial = sym_to_initial.get(sym)
        current = prices.get(sym)
        pct = calculate_percent_change(initial, current)
        result.append({
            "symbol": sym,
            "current_price": current,
            "percent_change": pct,
        })
    response = jsonify(result)
    if _legacy_mode:
        return response
    return _maybe_set_cache_headers(response, state_version, "prices")


@app.route("/api/prices", methods=["GET"])
def get_prices_cached():
    raw = request.args.get("symbols") or ""
    symbols = [s.strip().upper() for s in raw.split(",") if s and s.strip()]
    if not symbols:
        return jsonify({"prices": {}})
    prices = price_cache.get_many(symbols)
    ordered = {sym: prices.get(sym) for sym in symbols}
    response = jsonify({"prices": ordered})
    if _legacy_mode:
        return response
    return _maybe_set_cache_headers(response, state_version, "prices", ",".join(symbols))


@app.route("/api/stocks/<symbol>/overview", methods=["GET"])
def get_stock_overview(symbol: str):
    try:
        sym = _normalize_symbol(symbol)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    data = fetch_overview(sym)
    return jsonify(data)


@app.route("/api/stocks/<symbol>/snapshot", methods=["GET"])
def get_stock_snapshot(symbol: str):
    try:
        sym = _normalize_symbol(symbol)
    except ValueError as exc:
        return _json_error(str(exc), 400)

    use_hooked_read = _legacy_mode or (_read_stocks is not _ORIGINAL_READ_STOCKS)
    if use_hooked_read:
        source_rows = _legacy_read_stocks() if _legacy_mode else _read_stocks()
        candidates = [
            row for row in source_rows
            if isinstance(row, dict) and (row.get("symbol") or "").strip().upper() == sym
        ]
        if not candidates:
            return _json_error("stock not found", 404)

        def _sort_key(stock: Dict[str, Any]) -> date:
            reference = stock.get("date_added") or stock.get("date_spotted")
            return _parse_date(reference if isinstance(reference, str) else None)

        latest = dict(max(candidates, key=_sort_key))
    else:
        row = stocks.get(sym)
        if not row:
            return _json_error("stock not found", 404)
        latest = dict(row)

    initial_price = latest.get("initial_price")
    try:
        initial_price = float(initial_price) if initial_price is not None else None
    except (TypeError, ValueError):
        initial_price = None

    prices = price_cache.get_many([sym], schedule_refresh=False)
    current_price = prices.get(sym)
    if current_price is None:
        try:
            fresh = get_current_prices([sym]) or {}
            current_price = fresh.get(sym)
            price_cache.prime(sym, current_price)
        except Exception:
            current_price = None
    percent_change = calculate_percent_change(initial_price, current_price)

    payload = {
        "symbol": sym,
        "id": latest.get("id"),
        "list_type": latest.get("list_type"),
        "initial_price": initial_price,
        "date_spotted": latest.get("date_spotted"),
        "date_added": latest.get("date_added"),
        "current_price": current_price,
        "percent_change": percent_change,
    }
    return jsonify(payload)


@app.route("/api/stocks/<symbol>/history", methods=["GET"])
def get_stock_history(symbol: str):
    interval = request.args.get("interval") or "1d"
    try:
        sym = _normalize_symbol(symbol)
        data = fetch_price_history(sym, interval)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify(data)


@app.route("/api/stocks/<symbol>/indicators", methods=["GET"])
def get_stock_indicators(symbol: str):
    indicator_type = (request.args.get("type") or "sma").strip().lower()
    interval = request.args.get("interval") or "1d"
    windows_param = request.args.get("windows") or ""
    windows = _parse_windows_param(windows_param)
    if not windows:
        return _json_error("windows parameter must include at least one positive integer", 400)
    try:
        sym = _normalize_symbol(symbol)
        if indicator_type == "sma":
            data = compute_sma(sym, interval, windows)
        elif indicator_type == "ema":
            data = compute_ema(sym, interval, windows)
        else:
            return _json_error("type must be 'sma' or 'ema'", 400)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify(data)


@app.route("/api/stocks/<symbol>/rsi", methods=["GET"])
def get_stock_rsi(symbol: str):
    interval = request.args.get("interval") or "1d"
    period_raw = request.args.get("period") or "14"
    try:
        period = int(period_raw)
    except ValueError:
        return _json_error("period must be an integer", 400)
    try:
        sym = _normalize_symbol(symbol)
        data = compute_rsi(sym, interval, period)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify(data)


@app.route("/api/stocks/<symbol>/news", methods=["GET"])
def get_stock_news(symbol: str):
    limit_raw = request.args.get("limit")
    limit = None
    if limit_raw is not None:
        try:
            limit = max(1, int(limit_raw))
        except ValueError:
            return _json_error("limit must be an integer", 400)
    try:
        sym = _normalize_symbol(symbol)
        articles = fetch_news(sym, limit=limit or 10)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return jsonify({"symbol": sym, "articles": articles})


if __name__ == "__main__":
    # Create data file on first run
    _ensure_file()
    app.run(host="127.0.0.1", port=5000, debug=True)
