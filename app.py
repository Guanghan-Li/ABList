from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional

from flask import Flask, jsonify, render_template, request

from stock_api import calculate_percent_change, get_current_prices

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STOCKS_FILE = os.path.join(BASE_DIR, "stocks.json")
_file_lock = threading.Lock()
VALID_LIST_TYPES = ("A", "B", "PA", "PB")
VALID_LIST_TYPES_SET = set(VALID_LIST_TYPES)


def _ensure_file():
    if not os.path.exists(STOCKS_FILE):
        with _file_lock:
            if not os.path.exists(STOCKS_FILE):
                with open(STOCKS_FILE, "w", encoding="utf-8") as f:
                    json.dump([], f)


def _read_stocks() -> List[Dict]:
    _ensure_file()
    stocks: List[Dict] = []
    with _file_lock:
        try:
            with open(STOCKS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    stocks = data
        except FileNotFoundError:
            stocks = []
        except json.JSONDecodeError:
            stocks = []

    migrated = False
    today = datetime.utcnow().date()
    for stock in stocks:
        if not isinstance(stock, dict):
            continue
        has_date_added = bool(stock.get("date_added"))
        has_week_end = bool(stock.get("week_end"))
        has_week_label = bool(stock.get("week_label"))
        if has_date_added and has_week_end and has_week_label:
            continue
        source_date_str = stock.get("date_spotted") or None
        base_date = _parse_date(source_date_str) if source_date_str else today
        week_info = _calculate_week_info(base_date)
        if not has_date_added:
            stock["date_added"] = base_date.isoformat()
        if not has_week_end:
            stock["week_end"] = week_info["week_end"]
        if not has_week_label:
            stock["week_label"] = week_info["week_label"]
        migrated = True

    if migrated:
        _write_stocks(stocks)

    return stocks


def _write_stocks(stocks: List[Dict]) -> None:
    with _file_lock:
        with open(STOCKS_FILE, "w", encoding="utf-8") as f:
            json.dump(stocks, f, indent=2)


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


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/stocks", methods=["GET"])
def get_stocks():
    stocks = _read_stocks()
    week_filter = (request.args.get("week") or "").strip()
    if week_filter:
        stocks = [s for s in stocks if (s.get("week_end") or "").strip() == week_filter]
    grouped = {lt: [] for lt in VALID_LIST_TYPES}
    for s in stocks:
        lt = (s.get("list_type") or "").strip().upper()
        if lt not in grouped:
            lt = "B"
        grouped[lt].append(s)
    return jsonify(grouped)


@app.route("/api/weeks", methods=["GET"])
def get_weeks():
    stocks = _read_stocks()
    weeks: Dict[str, str] = {}
    for stock in stocks:
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

    stocks = _read_stocks()
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
    stocks.append(stock)
    _write_stocks(stocks)

    # After creation, fetch the new stock's current price only
    prices = get_current_prices([symbol])
    current_price = prices.get(symbol)
    pct = calculate_percent_change(initial_price, current_price)

    return jsonify({"stock": stock, "current_price": current_price, "percent_change": pct})


@app.route("/api/stocks/<stock_id>", methods=["PUT"])
def update_stock(stock_id: str):
    payload = request.get_json(force=True, silent=True) or {}
    stocks = _read_stocks()

    found = None
    for s in stocks:
        if s.get("id") == stock_id:
            found = s
            break
    if not found:
        return jsonify({"error": "stock not found"}), 404

    # Update editable fields if provided
    if "symbol" in payload:
        sym = (payload.get("symbol") or "").strip().upper()
        if not sym:
            return jsonify({"error": "symbol cannot be empty"}), 400
        found["symbol"] = sym
    if "initial_price" in payload:
        try:
            found["initial_price"] = float(payload.get("initial_price"))
        except Exception:
            return jsonify({"error": "initial_price must be a number"}), 400
    if "reason" in payload:
        found["reason"] = (payload.get("reason") or "").strip()
    if "date_spotted" in payload:
        found["date_spotted"] = (payload.get("date_spotted") or "").strip()
    if "date_bought" in payload:
        found["date_bought"] = (payload.get("date_bought") or "").strip()
    if "list_type" in payload:
        lt = (payload.get("list_type") or "B").strip().upper()
        if lt not in VALID_LIST_TYPES_SET:
            lt = "B"
        found["list_type"] = lt

    reference_str = found.get("date_spotted") or found.get("date_added")
    base_date = _parse_date(reference_str)
    week_info = _calculate_week_info(base_date)
    found["week_end"] = week_info["week_end"]
    found["week_label"] = week_info["week_label"]
    if not found.get("date_added"):
        found["date_added"] = base_date.isoformat()

    _write_stocks(stocks)
    return jsonify(found)


@app.route("/api/stocks/<stock_id>", methods=["DELETE"])
def delete_stock(stock_id: str):
    stocks = _read_stocks()
    new_stocks = [s for s in stocks if s.get("id") != stock_id]
    if len(new_stocks) == len(stocks):
        return jsonify({"error": "stock not found"}), 404
    _write_stocks(new_stocks)
    return jsonify({"success": True})


@app.route("/api/stocks/prices", methods=["GET"])
def get_prices():
    stocks = _read_stocks()
    symbols = [s.get("symbol") for s in stocks if s.get("symbol")]
    prices = get_current_prices(symbols)

    result = []
    sym_to_initial: Dict[str, Optional[float]] = {s["symbol"]: s.get("initial_price") for s in stocks}
    for sym in symbols:
        current = prices.get(sym)
        initial = sym_to_initial.get(sym)
        pct = calculate_percent_change(initial, current)
        result.append({
            "symbol": sym,
            "current_price": current,
            "percent_change": pct,
        })
    return jsonify(result)


if __name__ == "__main__":
    # Create data file on first run
    _ensure_file()
    app.run(host="127.0.0.1", port=5000, debug=True)
