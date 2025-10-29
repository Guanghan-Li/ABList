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


# --- New helpers for detailed stock page ---

def fetch_price_history(symbol: str, timeframe: str = "daily") -> List[Dict]:
    """
    Return up to ~4 years of price history for a symbol.
    timeframe: 'daily' | 'weekly'
    Output: list of { date: 'YYYY-MM-DD', open, high, low, close, volume }
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    try:
        ticker = yf.Ticker(sym)
        tf = (timeframe or "daily").lower()
        if tf == "weekly":
            hist = ticker.history(period="10y", interval="1wk")
        else:
            hist = ticker.history(period="5y", interval="1d")
        if hist is None or hist.empty:
            return []

        # Limit to last ~4 years
        try:
            # pandas-like filtering without importing pandas explicitly
            # hist has DatetimeIndex; we convert and slice by date threshold
            from datetime import datetime, timedelta

            cutoff = datetime.utcnow() - timedelta(days=365 * 4 + 14)
            hist = hist[hist.index >= cutoff]
        except Exception:
            pass

        result: List[Dict] = []
        for idx, row in hist.iterrows():
            try:
                dt_str = idx.strftime("%Y-%m-%d")
            except Exception:
                dt_str = str(idx)
            try:
                result.append({
                    "date": dt_str,
                    "open": float(row.get("Open", float("nan"))),
                    "high": float(row.get("High", float("nan"))),
                    "low": float(row.get("Low", float("nan"))),
                    "close": float(row.get("Close", float("nan"))),
                    "volume": float(row.get("Volume", 0.0)),
                })
            except Exception:
                # Skip malformed rows
                continue
        return result
    except Exception:
        return []


def fetch_company_overview(symbol: str) -> Dict:
    """
    Return basic company overview and pricing metadata.
    Keys: symbol, name, currency, exchange, sector, industry, summary, last_price, market_cap
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return {}
    overview: Dict = {"symbol": sym}
    try:
        t = yf.Ticker(sym)
        # Fast fields
        if hasattr(t, "fast_info"):
            fi = t.fast_info
            overview["last_price"] = _safe_float(getattr(fi, "last_price", None))
            overview["currency"] = getattr(fi, "currency", None)
            overview["exchange"] = getattr(fi, "exchange", None)
            overview["market_cap"] = _safe_float(getattr(fi, "market_cap", None))

        # Rich info
        info: Dict = {}
        try:
            # get_info preferred in newer yfinance
            if hasattr(t, "get_info"):
                info = t.get_info() or {}
            else:
                info = t.info or {}
        except Exception:
            try:
                info = t.info or {}
            except Exception:
                info = {}

        if isinstance(info, dict):
            overview["name"] = info.get("longName") or info.get("shortName")
            overview["sector"] = info.get("sector")
            overview["industry"] = info.get("industry")
            overview["summary"] = info.get("longBusinessSummary") or info.get("description")
            overview.setdefault("currency", info.get("currency"))
            overview.setdefault("exchange", info.get("exchange"))
            if overview.get("market_cap") is None and isinstance(info.get("marketCap"), (int, float)):
                overview["market_cap"] = float(info.get("marketCap"))
    except Exception:
        pass
    return overview


def fetch_latest_news(symbol: str, limit: int = 10) -> List[Dict]:
    """
    Return recent news items for the symbol.
    Each item: { title, link, publisher, published_at }
    """
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    items: List[Dict] = []
    try:
        t = yf.Ticker(sym)
        news_list = getattr(t, "news", None) or []
        if not isinstance(news_list, list):
            return []
        for raw in news_list[: max(1, int(limit))]:
            try:
                title = raw.get("title")
                link = raw.get("link") or raw.get("url")
                publisher = raw.get("publisher") or raw.get("provider")
                published = raw.get("providerPublishTime") or raw.get("published_at")
                if isinstance(published, (int, float)):
                    # seconds since epoch
                    import datetime as _dt
                    published_at = _dt.datetime.utcfromtimestamp(published).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    published_at = str(published) if published else None
                if not title or not link:
                    continue
                items.append({
                    "title": title,
                    "link": link,
                    "publisher": publisher,
                    "published_at": published_at,
                })
            except Exception:
                continue
    except Exception:
        return []
    return items


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None
