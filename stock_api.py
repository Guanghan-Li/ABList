from __future__ import annotations

from typing import Dict, Iterable, Optional, List

import time
import yfinance as yf


def get_current_prices(symbols: Iterable[str]) -> Dict[str, Optional[float]]:
    """
    Fetch current prices for a list of stock symbols in a single batch call when possible.

    Returns mapping of uppercased symbol -> last price (float) or None if unavailable.
    """
    symbol_list = [s.strip().upper() for s in symbols if s and s.strip()]
    if not symbol_list:
        return {}

    prices: Dict[str, Optional[float]] = {s: None for s in symbol_list}

    try:
        # yfinance Tickers supports batch fetching
        tickers = yf.Tickers(" ".join(symbol_list))
        # Try fast price field via ticker.info-like behavior using .fast_info or .info fallbacks
        # We will attempt to use .fast_info.last_price which is typically available
        for sym in symbol_list:
            try:
                t = tickers.tickers.get(sym)
                if t is None:
                    continue
                # Prefer fast_info if available
                last_price = None
                if hasattr(t, "fast_info") and getattr(t.fast_info, "last_price", None) is not None:
                    last_price = float(t.fast_info.last_price)
                else:
                    # Fallback to history recent close/price
                    hist = t.history(period="1d", interval="1m")
                    if not hist.empty:
                        last_price = float(hist["Close"].iloc[-1])
                prices[sym] = last_price
                # Tiny delay to be gentle when many symbols
                time.sleep(0.02)
            except Exception:
                # On per-symbol failure, keep None
                prices[sym] = None
                continue
    except Exception:
        # Fallback: try symbol-by-symbol if batch failed entirely
        for sym in symbol_list:
            try:
                t = yf.Ticker(sym)
                last_price = None
                if hasattr(t, "fast_info") and getattr(t.fast_info, "last_price", None) is not None:
                    last_price = float(t.fast_info.last_price)
                else:
                    hist = t.history(period="1d", interval="1m")
                    if not hist.empty:
                        last_price = float(hist["Close"].iloc[-1])
                prices[sym] = last_price
                time.sleep(0.05)
            except Exception:
                prices[sym] = None
                continue

    return prices


def calculate_percent_change(initial_price: Optional[float], current_price: Optional[float]) -> Optional[float]:
    if initial_price in (None, 0) or current_price in (None, 0):
        return None
    try:
        return ((current_price - initial_price) / initial_price) * 100.0
    except Exception:
        return None


def get_stock_info(symbol: str) -> Dict:
    """Optional: detailed info for a single symbol for future enhancements."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}


def _safe_float(val) -> Optional[float]:
    try:
        if val is None:
            return None
        return float(val)
    except Exception:
        return None


def get_price_history(symbol: str, interval: str = "daily", years: int = 4) -> List[Dict]:
    """
    Fetch OHLCV price history for up to `years` back.
    interval: 'daily' -> 1d, 'weekly' -> 1wk
    Returns list of {date, open, high, low, close, volume} with ISO date strings.
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    yf_interval = "1d" if interval == "daily" else "1wk"
    # yfinance supports start/end or period. Use start/end for precise years.
    import datetime as _dt
    end_dt = _dt.datetime.utcnow()
    start_dt = end_dt - _dt.timedelta(days=int(years * 365.25))
    try:
        t = yf.Ticker(sym)
        hist = t.history(start=start_dt, end=end_dt, interval=yf_interval, auto_adjust=False)
        if hist is None or hist.empty:
            return []
        # Reset index to have DatetimeIndex as a column
        hist = hist.reset_index()
        out: List[Dict] = []
        for _, row in hist.iterrows():
            # yfinance may use 'Date' or 'Datetime' column name depending on interval
            dt_val = row.get("Date") or row.get("Datetime") or row.get("date")
            try:
                if hasattr(dt_val, "to_pydatetime"):
                    dt_val = dt_val.to_pydatetime()
            except Exception:
                pass
            try:
                iso = (dt_val or end_dt).isoformat()
            except Exception:
                iso = end_dt.isoformat()
            out.append({
                "date": iso,
                "open": _safe_float(row.get("Open")),
                "high": _safe_float(row.get("High")),
                "low": _safe_float(row.get("Low")),
                "close": _safe_float(row.get("Close")),
                "volume": _safe_float(row.get("Volume")),
            })
        return out
    except Exception:
        return []


def get_recent_news(symbol: str, limit: int = 8) -> List[Dict]:
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    try:
        t = yf.Ticker(sym)
        items = getattr(t, "news", []) or []
        news_list: List[Dict] = []
        for it in items[:limit]:
            if not isinstance(it, dict):
                continue
            # Normalize fields across yfinance versions
            title = it.get("title") or it.get("headline")
            link = it.get("link") or it.get("url")
            publisher = it.get("publisher") or it.get("source")
            ts = it.get("providerPublishTime") or it.get("published_at") or it.get("time_published")
            try:
                ts = int(ts)
            except Exception:
                ts = None
            news_list.append({
                "title": title,
                "link": link,
                "publisher": publisher,
                "published_ts": ts,
            })
        return news_list
    except Exception:
        return []


def get_company_profile(symbol: str) -> Dict:
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}
    try:
        t = yf.Ticker(sym)
        profile: Dict = {"symbol": sym}
        # Fast fields
        try:
            fi = getattr(t, "fast_info", None)
            if fi is not None:
                profile.update({
                    "last_price": _safe_float(getattr(fi, "last_price", None)),
                    "market_cap": _safe_float(getattr(fi, "market_cap", None)),
                    "previous_close": _safe_float(getattr(fi, "previous_close", None)),
                    "currency": getattr(fi, "currency", None),
                })
        except Exception:
            pass
        # Rich info
        try:
            info = t.info
            if isinstance(info, dict):
                for k in (
                    "longName",
                    "shortName",
                    "sector",
                    "industry",
                    "country",
                    "website",
                    "longBusinessSummary",
                    "exchange",
                    "currency",
                ):
                    if k in info:
                        profile[k] = info[k]
        except Exception:
            pass
        return profile
    except Exception:
        return {"symbol": sym}
