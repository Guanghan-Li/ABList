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
DATA_ROOT_ENV = os.getenv("STOCKS_PATH")
if DATA_ROOT_ENV:
    DATA_ROOT = os.path.abspath(DATA_ROOT_ENV)
else:
    DATA_ROOT = os.path.join(BASE_DIR, "data")
INDEX_FILE = os.path.join(DATA_ROOT, "index.json")
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

DATA_PATH = INDEX_FILE

_state_lock = threading.RLock()


def _default_index() -> Dict[str, Any]:
    return {
        "by_id": {},
        "by_symbol": {},
        "weeks": {},
        "latest_week": None,
        "state_version": 0,
    }


def _ensure_file() -> None:
    """Ensure the index file exists so tooling can persist data."""
    os.makedirs(DATA_ROOT, exist_ok=True)
    if os.path.exists(INDEX_FILE):
        return
    with open(INDEX_FILE, "w", encoding="utf-8") as fh:
        json.dump(_default_index(), fh, indent=2)


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


def _load_index() -> Dict[str, Any]:
    _ensure_file()
    try:
        with open(INDEX_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        data = _default_index()
    merged = _default_index()
    merged.update({k: data.get(k, v) for k, v in merged.items()})
    # Ensure nested structures exist
    if not isinstance(merged["by_id"], dict):
        merged["by_id"] = {}
    if not isinstance(merged["by_symbol"], dict):
        merged["by_symbol"] = {}
    if not isinstance(merged["weeks"], dict):
        merged["weeks"] = {}
    if not isinstance(merged.get("state_version"), int):
        merged["state_version"] = 0
    return merged


def _save_index(state: Dict[str, Any]) -> None:
    payload = dict(state)
    _atomic_write_json(INDEX_FILE, payload)


_index_state = _load_index()
state_version = _index_state.get("state_version", 0)


def _bump_state_version() -> None:
    global state_version
    state_version = (state_version or 0) + 1
    _index_state["state_version"] = state_version


def _normalize_symbol_key(symbol: Optional[str]) -> str:
    return (symbol or "").strip().upper()


def _week_dir(week_end: str) -> str:
    return os.path.join(DATA_ROOT, week_end)


def _list_file_path(week_end: str, list_type: str) -> str:
    return os.path.join(_week_dir(week_end), f"{list_type}.json")


def _ensure_week_entry(week_end: str) -> None:
    week_end = week_end.strip()
    if not week_end:
        return
    weeks = _index_state["weeks"]
    if week_end not in weeks:
        weeks[week_end] = {
            "label": _format_week_label(_parse_date(week_end)),
            "lists": {lt: {"count": 0} for lt in VALID_LIST_TYPES},
        }
    else:
        lists = weeks[week_end].setdefault("lists", {})
        for lt in VALID_LIST_TYPES:
            lists.setdefault(lt, {"count": 0})
    latest = _index_state.get("latest_week")
    if not latest or week_end > latest:
        _index_state["latest_week"] = week_end


def _available_weeks(desc: bool = True) -> List[str]:
    weeks = sorted(_index_state["weeks"].keys(), reverse=desc)
    return weeks


def _latest_week() -> str:
    latest = _index_state.get("latest_week")
    if latest:
        return latest
    today = datetime.utcnow().date()
    week = _calculate_week_info(today)["week_end"]
    _ensure_week_entry(week)
    _bump_state_version()
    _save_index(_index_state)
    return week


def _load_list_records(week_end: str, list_type: str) -> List[Dict[str, Any]]:
    path = _list_file_path(week_end, list_type)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "rb") as fh:
            data = _json_loads(fh.read())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, list):
        return [record for record in data if isinstance(record, dict)]
    return []


def _save_list_records(week_end: str, list_type: str, records: List[Dict[str, Any]]) -> None:
    os.makedirs(_week_dir(week_end), exist_ok=True)
    path = _list_file_path(week_end, list_type)
    _atomic_write_json(path, records)


def _index_record(record: Dict[str, Any], week_end: str, list_type: str) -> None:
    stock_id = str(record.get("id") or "").strip()
    if not stock_id:
        return
    symbol = _normalize_symbol_key(record.get("symbol"))
    meta = {
        "week": week_end,
        "list": list_type,
        "symbol": symbol,
        "date_added": record.get("date_added"),
        "date_spotted": record.get("date_spotted"),
    }
    _index_state["by_id"][stock_id] = meta
    if symbol:
        entries = _index_state["by_symbol"].setdefault(symbol, [])
        filtered = [entry for entry in entries if entry.get("id") != stock_id]
        filtered.append({"id": stock_id, **meta})
        filtered.sort(key=lambda item: (item.get("week"), item.get("date_added") or item.get("week")))
        _index_state["by_symbol"][symbol] = filtered


def _remove_index_entry(stock_id: str) -> None:
    stock_id = str(stock_id or "").strip()
    if not stock_id:
        return
    entry = _index_state["by_id"].pop(stock_id, None)
    if not entry:
        return
    symbol = entry.get("symbol")
    if symbol:
        normalized = _normalize_symbol_key(symbol)
        entries = _index_state["by_symbol"].get(normalized, [])
        _index_state["by_symbol"][normalized] = [item for item in entries if item.get("id") != stock_id]


def _update_week_counts(week_end: str, list_type: str, count: int) -> None:
    _ensure_week_entry(week_end)
    week_info = _index_state["weeks"][week_end]
    week_info.setdefault("lists", {})
    week_info["lists"].setdefault(list_type, {})
    week_info["lists"][list_type]["count"] = count


def _rebuild_index_from_files() -> bool:
    found = False
    if not os.path.isdir(DATA_ROOT):
        return False
    entries = sorted(os.listdir(DATA_ROOT))
    _index_state["by_id"] = {}
    _index_state["by_symbol"] = {}
    _index_state["weeks"] = {}
    for entry in entries:
        week_path = os.path.join(DATA_ROOT, entry)
        if not os.path.isdir(week_path):
            continue
        try:
            _parse_date(entry)
        except Exception:
            continue
        _ensure_week_entry(entry)
        for lt in VALID_LIST_TYPES:
            records = _load_list_records(entry, lt)
            if not records:
                continue
            found = True
            _update_week_counts(entry, lt, len(records))
            for record in records:
                _index_record(record, entry, lt)
    if found:
        _save_index(_index_state)
    return found


def _load_grouped_lists(weeks: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    grouped = {lt: [] for lt in VALID_LIST_TYPES}
    for week in weeks:
        normalized_week = week.strip()
        if not normalized_week:
            continue
        for lt in VALID_LIST_TYPES:
            records = _load_list_records(normalized_week, lt)
            for record in records:
                entry = dict(record)
                entry["week_end"] = normalized_week
                grouped[lt].append(entry)
    return grouped


def _read_stocks(weeks: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    target_weeks = weeks or [_latest_week()]
    grouped = _load_grouped_lists(target_weeks)
    flattened: List[Dict[str, Any]] = []
    for lt in VALID_LIST_TYPES:
        flattened.extend(grouped.get(lt, []))
    return flattened


def _parse_weeks_param(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    entries: List[str] = []
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    for part in parts:
        if ".." in part:
            start, end = part.split("..", 1)
            entries.extend(_expand_week_range(start.strip(), end.strip()))
        elif ":" in part:
            start, end = part.split(":", 1)
            entries.extend(_expand_week_range(start.strip(), end.strip()))
        else:
            entries.append(part)
    normalized: List[str] = []
    seen = set()
    for week in entries:
        normalized_week = _normalize_week_value(week)
        if not normalized_week:
            continue
        if normalized_week not in seen:
            normalized.append(normalized_week)
            seen.add(normalized_week)
    return normalized


def _normalize_week_value(value: str) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = _parse_date(value)
    except Exception:
        return None
    week_end = _calculate_week_info(dt)["week_end"]
    return week_end


def _expand_week_range(start: str, end: str) -> List[str]:
    start_week = _normalize_week_value(start)
    end_week = _normalize_week_value(end)
    if not start_week or not end_week:
        return []
    start_date = _parse_date(start_week)
    end_date = _parse_date(end_week)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    weeks: List[str] = []
    current = start_date
    while current <= end_date:
        info = _calculate_week_info(current)
        week_end = info["week_end"]
        if week_end not in weeks:
            weeks.append(week_end)
        current += timedelta(days=7)
    if end_week not in weeks:
        weeks.append(end_week)
    return weeks


def _resolve_weeks_from_request() -> List[str]:
    raw = (request.args.get("week") or "").strip()
    parsed = _parse_weeks_param(raw)
    available = set(_available_weeks(desc=False))
    filtered = [week for week in parsed if week in available]
    if filtered:
        return filtered
    latest = _latest_week()
    return [latest]


def _remove_record_from_file(week_end: str, list_type: str, stock_id: str) -> bool:
    records = _load_list_records(week_end, list_type)
    if not records:
        return False
    updated = [record for record in records if str(record.get("id")) != str(stock_id)]
    if len(updated) == len(records):
        return False
    _save_list_records(week_end, list_type, updated)
    _update_week_counts(week_end, list_type, len(updated))
    return True


def _upsert_record(record: Dict[str, Any], previous: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    normalized = _normalize_loaded_row(record)
    if not normalized:
        raise ValueError("invalid stock payload")
    stock_id = normalized.get("id")
    if not stock_id:
        raise ValueError("stock id is required")
    target_week = normalized.get("week_end")
    if not target_week:
        target_week = _calculate_week_info(normalized.get("date_spotted") or normalized.get("date_added"))["week_end"]
        normalized["week_end"] = target_week
    target_list = _normalize_list_type(normalized.get("list_type"))
    normalized["list_type"] = target_list
    with _state_lock:
        _ensure_week_entry(target_week)
        records = _load_list_records(target_week, target_list)
        replaced = False
        for idx, existing in enumerate(records):
            if str(existing.get("id")) == str(stock_id):
                records[idx] = normalized
                replaced = True
                break
        if not replaced:
            records.append(normalized)
        _save_list_records(target_week, target_list, records)
        _update_week_counts(target_week, target_list, len(records))
        if previous and (
            previous.get("week") != target_week or _normalize_list_type(previous.get("list")) != target_list
        ):
            _remove_record_from_file(previous["week"], _normalize_list_type(previous["list"]), stock_id)
        _index_record(normalized, target_week, target_list)
        _bump_state_version()
        _save_index(_index_state)
    return normalized


def _delete_record(stock_id: str) -> bool:
    stock_id = str(stock_id or "").strip()
    if not stock_id:
        return False
    with _state_lock:
        entry = _index_state["by_id"].get(stock_id)
        if not entry:
            return False
        week = entry.get("week")
        list_type = entry.get("list")
        if not week or not list_type:
            return False
        removed = _remove_record_from_file(week, list_type, stock_id)
        if not removed:
            return False
        _remove_index_entry(stock_id)
        _bump_state_version()
        _save_index(_index_state)
        return True


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


def _normalize_loaded_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        symbol = _normalize_symbol(row.get("symbol"))
    except ValueError:
        return None
    normalized: Dict[str, Any] = dict(row)
    normalized["symbol"] = symbol
    normalized["list_type"] = _normalize_list_type(normalized.get("list_type"))
    if not normalized.get("id"):
        normalized["id"] = str(uuid.uuid4())
    _apply_week_defaults(normalized)
    return normalized


def _get_stock_by_id(stock_id: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    stock_id = str(stock_id or "").strip()
    if not stock_id:
        return None
    entry = _index_state["by_id"].get(stock_id)
    if not entry:
        return None
    week = entry.get("week")
    list_type = _normalize_list_type(entry.get("list"))
    if not week:
        return None
    records = _load_list_records(week, list_type)
    for record in records:
        if str(record.get("id")) == stock_id:
            return record.get("symbol"), dict(record)
    return None


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
    source_date = row.get("date_spotted") or row.get("date_added")
    base_date = _parse_date(source_date if isinstance(source_date, str) else None)
    week_info = _calculate_week_info(base_date)
    if not row.get("date_added"):
        row["date_added"] = base_date.isoformat()
    row["week_end"] = week_info["week_end"]
    row["week_label"] = week_info["week_label"]


def _initialize_storage() -> None:
    _ensure_file()
    if not _index_state["weeks"]:
        if not _rebuild_index_from_files():
            latest = _calculate_week_info(datetime.utcnow().date())["week_end"]
            _ensure_week_entry(latest)
            _save_index(_index_state)


_initialize_storage()
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


@app.route("/position_sizer_atr.html")
@app.route("/position-sizer")
def position_sizer():
    return render_template("position_sizer_atr.html")


@app.route("/api/stocks", methods=["GET"])
def get_stocks():
    weeks = _resolve_weeks_from_request()
    grouped = _load_grouped_lists(weeks)
    response = jsonify(grouped)
    return _maybe_set_cache_headers(response, state_version, ",".join(weeks))


@app.route("/api/stocks/search", methods=["GET"])
def search_stocks():
    raw_query = request.args.get("q")
    query = (raw_query or "").strip().upper()
    if not query:
        return jsonify({"results": []})
    if len(query) > 32:
        return _json_error("query must be 32 characters or fewer", 400)

    list_filter_raw = request.args.get("list")
    weeks = _resolve_weeks_from_request()
    universe = _read_stocks(weeks=weeks)
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
    weeks = []
    for week_end in _available_weeks(desc=True):
        info = _index_state["weeks"].get(week_end, {})
        week_label = info.get("label") or _format_week_label(_parse_date(week_end))
        weeks.append({"week_end": week_end, "week_label": week_label})
    return jsonify(weeks)


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

    stock = {
        "id": str(uuid.uuid4()),
        "symbol": symbol,
        "initial_price": initial_price,
        "reason": reason,
        "date_spotted": date_spotted,
        "date_bought": date_bought,
        "date_added": datetime.utcnow().date().isoformat(),
        "list_type": list_type,
    }
    _apply_week_defaults(stock)

    try:
        stored = _upsert_record(stock)
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
        prev_meta = _index_state["by_id"].get(stock_id, {})
        stored = _upsert_record(updated, previous=prev_meta)
    except Exception:
        return _json_error("failed to persist stock changes", 500)

    return jsonify(stored)


@app.route("/api/stocks/<stock_id>", methods=["DELETE"])
def delete_stock(stock_id: str):
    try:
        removed = _delete_record(stock_id)
    except Exception:
        removed = False
    if not removed:
        return jsonify({"error": "stock not found"}), 404
    return jsonify({"success": True})


@app.route("/api/stocks/prices", methods=["GET"])
def get_prices():
    weeks = _resolve_weeks_from_request()
    grouped = _load_grouped_lists(weeks)
    rows = []
    for lt in VALID_LIST_TYPES:
        rows.extend(grouped.get(lt, []))
    symbols = []
    sym_to_initial: Dict[str, Optional[float]] = {}
    id_to_symbol: Dict[str, str] = {}
    for row in rows:
        stock_id = str(row.get("id") or "").strip()
        symbol = _normalize_symbol_key(row.get("symbol"))
        if not stock_id or not symbol:
            continue
        id_to_symbol[stock_id] = symbol
        symbols.append(symbol)
        sym_to_initial[stock_id] = row.get("initial_price")
    prices = price_cache.get_many(symbols)
    result = []
    for stock_id, symbol in id_to_symbol.items():
        initial = sym_to_initial.get(stock_id)
        current = prices.get(symbol)
        pct = calculate_percent_change(initial, current)
        result.append({
            "id": stock_id,
            "symbol": symbol,
            "current_price": current,
            "percent_change": pct,
        })
    response = jsonify(result)
    return _maybe_set_cache_headers(response, state_version, "prices", ",".join(weeks))


@app.route("/api/prices", methods=["GET"])
def get_prices_cached():
    raw = request.args.get("symbols") or ""
    symbols = [s.strip().upper() for s in raw.split(",") if s and s.strip()]
    if not symbols:
        return jsonify({"prices": {}})
    prices = price_cache.get_many(symbols)
    ordered = {sym: prices.get(sym) for sym in symbols}
    response = jsonify({"prices": ordered})
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
    entries = _index_state["by_symbol"].get(sym)
    record = None

    def _entry_key(entry: Dict[str, Any]) -> Tuple[str, str]:
        reference = entry.get("date_spotted") or entry.get("date_added") or entry.get("week")
        return (reference, entry.get("week"))

    if entries:
        latest_entry = max(entries, key=_entry_key)
        week = latest_entry.get("week")
        list_type = _normalize_list_type(latest_entry.get("list"))
        stock_id = latest_entry.get("id")
        if week and list_type:
            records = _load_list_records(week, list_type)
            for candidate in records:
                if str(candidate.get("id")) == str(stock_id):
                    record = dict(candidate)
                    break
    else:
        # Fallback for tests or manual data edits: scan in-memory snapshot
        candidates = [
            row for row in _read_stocks()
            if isinstance(row, dict) and _normalize_symbol_key(row.get("symbol")) == sym
        ]
        if candidates:
            record = max(
                candidates,
                key=lambda stock: _parse_date(stock.get("week_end") or stock.get("date_added") or stock.get("date_spotted"))
            )

    if not record:
        return _json_error("stock not found", 404)

    initial_price = record.get("initial_price")
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
        "id": record.get("id"),
        "list_type": record.get("list_type"),
        "initial_price": initial_price,
        "date_spotted": record.get("date_spotted"),
        "date_added": record.get("date_added"),
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
