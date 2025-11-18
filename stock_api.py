from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import time
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf


ALLOWED_INTERVALS = {"1d", "1wk"}
DEFAULT_INTERVAL = "1d"
MAX_NEWS_ITEMS = 10


def _normalize_symbol(symbol: str) -> str:
    sym = (symbol or "").strip().upper()
    if not sym:
        raise ValueError("symbol is required")
    return sym


def _validate_interval(interval: Optional[str]) -> str:
    iv = (interval or DEFAULT_INTERVAL).strip().lower()
    if iv not in ALLOWED_INTERVALS:
        raise ValueError("interval must be 1d or 1wk")
    return iv


def _history_period_for(interval: str) -> str:
    # Weekly history benefits from a slightly longer pull to ensure 4y coverage
    return "5y" if interval == "1wk" else "4y"


def _history_to_records(history: pd.DataFrame) -> List[Dict[str, Optional[float]]]:
    if history.empty:
        return []
    data = history.reset_index()
    records: List[Dict[str, Optional[float]]] = []
    for _, row in data.iterrows():
        # yfinance returns Timestamp for the index. Convert to ISO date string.
        raw_date = row.get("Date")
        if isinstance(raw_date, (pd.Timestamp, datetime)):
            date_str = raw_date.tz_localize(None).strftime("%Y-%m-%d")
        else:
            try:
                date_str = pd.to_datetime(raw_date).strftime("%Y-%m-%d")
            except Exception:
                date_str = None
        records.append({
            "date": date_str,
            "open": _safe_float(row.get("Open")),
            "high": _safe_float(row.get("High")),
            "low": _safe_float(row.get("Low")),
            "close": _safe_float(row.get("Close")),
            "adj_close": _safe_float(row.get("Adj Close")),
            "volume": _safe_float(row.get("Volume")),
        })
    return records


def _safe_float(value: Optional[float]) -> Optional[float]:
    try:
        if value is None or (isinstance(value, float) and np.isnan(value)):
            return None
        return float(value)
    except Exception:
        return None


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        text = str(value).strip()
    except Exception:
        return None
    return text or None


def _timestamp_to_iso(value: Any) -> Optional[str]:
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (np.integer, int)):
            timestamp = int(value)
        elif isinstance(value, (np.floating, float)):
            timestamp = float(value)
        else:
            timestamp = float(str(value))
        if timestamp <= 0:
            return None
        dt = datetime.fromtimestamp(timestamp)
        return dt.isoformat()
    except Exception:
        return None


def _extract_thumbnail_url(article: Dict[str, Any]) -> Optional[str]:
    thumbnail = article.get("thumbnail")
    if isinstance(thumbnail, dict):
        resolutions = thumbnail.get("resolutions")
        if isinstance(resolutions, list):
            for entry in resolutions:
                if isinstance(entry, dict):
                    url = entry.get("url")
                    if url:
                        return str(url)
    elif isinstance(thumbnail, list):
        for entry in thumbnail:
            if isinstance(entry, dict):
                url = entry.get("url")
                if url:
                    return str(url)
    return None


def _download_history(symbol: str, interval: str) -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    period = _history_period_for(interval)
    history = ticker.history(period=period, interval=interval, auto_adjust=False)
    # Ensure numeric columns are floats
    return history.astype(float, errors="ignore")


def fetch_price_history(symbol: str, interval: Optional[str] = None) -> Dict:
    sym = _normalize_symbol(symbol)
    iv = _validate_interval(interval)
    try:
        history = _download_history(sym, iv)
    except Exception:
        history = pd.DataFrame()
    return {
        "symbol": sym,
        "interval": iv,
        "points": _history_to_records(history),
    }


def _series_from_history(history: pd.DataFrame) -> pd.Series:
    if history.empty or "Close" not in history.columns:
        return pd.Series(dtype=float)
    series = history["Close"].astype(float, errors="ignore")
    return series


def compute_sma(symbol: str, interval: str, windows: Iterable[int]) -> Dict:
    sym = _normalize_symbol(symbol)
    iv = _validate_interval(interval)
    history = pd.DataFrame()
    try:
        history = _download_history(sym, iv)
    except Exception:
        history = pd.DataFrame()
    close_series = _series_from_history(history)
    records = []
    if close_series.empty:
        return {"symbol": sym, "interval": iv, "indicators": records}
    for window in windows:
        if window <= 0:
            continue
        sma_series = close_series.rolling(window=window, min_periods=1).mean()
        records.append({
            "type": "sma",
            "window": window,
            "values": _series_to_list(sma_series, history)
        })
    return {"symbol": sym, "interval": iv, "indicators": records}


def compute_ema(symbol: str, interval: str, windows: Iterable[int]) -> Dict:
    sym = _normalize_symbol(symbol)
    iv = _validate_interval(interval)
    history = pd.DataFrame()
    try:
        history = _download_history(sym, iv)
    except Exception:
        history = pd.DataFrame()
    close_series = _series_from_history(history)
    records = []
    if close_series.empty:
        return {"symbol": sym, "interval": iv, "indicators": records}
    for window in windows:
        if window <= 0:
            continue
        ema_series = close_series.ewm(span=window, adjust=False).mean()
        records.append({
            "type": "ema",
            "window": window,
            "values": _series_to_list(ema_series, history)
        })
    return {"symbol": sym, "interval": iv, "indicators": records}


def compute_rsi(symbol: str, interval: str, period: int) -> Dict:
    sym = _normalize_symbol(symbol)
    iv = _validate_interval(interval)
    if period <= 0:
        raise ValueError("period must be positive")
    history = pd.DataFrame()
    try:
        history = _download_history(sym, iv)
    except Exception:
        history = pd.DataFrame()
    close_series = _series_from_history(history)
    if close_series.empty:
        values: List[Optional[float]] = []
    else:
        delta = close_series.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        roll_up = up.ewm(alpha=1 / period, adjust=False).mean()
        roll_down = down.ewm(alpha=1 / period, adjust=False).mean()
        rs = roll_up / roll_down.replace({0: np.nan})
        rsi_series = 100 - (100 / (1 + rs))
        values = _series_to_list(rsi_series, history)
    return {
        "symbol": sym,
        "interval": iv,
        "period": period,
        "values": values,
    }


def _series_to_list(series: pd.Series, history: pd.DataFrame) -> List[Dict[str, Optional[float]]]:
    if series.empty:
        return []
    series = series.reset_index(drop=True)
    history = history.reset_index()
    values: List[Dict[str, Optional[float]]] = []
    for idx, value in enumerate(series):
        raw_date = history.loc[idx, "Date"] if idx < len(history) else None
        if isinstance(raw_date, (pd.Timestamp, datetime)):
            date_str = raw_date.tz_localize(None).strftime("%Y-%m-%d")
        else:
            try:
                date_str = pd.to_datetime(raw_date).strftime("%Y-%m-%d")
            except Exception:
                date_str = None
        values.append({
            "date": date_str,
            "value": None if value is None or (isinstance(value, float) and np.isnan(value)) else float(value),
        })
    return values


def fetch_overview(symbol: str) -> Dict:
    sym = _normalize_symbol(symbol)
    info: Dict[str, Optional[str]] = {"symbol": sym}
    try:
        ticker = yf.Ticker(sym)
        if hasattr(ticker, "fast_info"):
            fi = ticker.fast_info
            info.update({
                "last_price": _safe_float(getattr(fi, "last_price", None)),
                "currency": getattr(fi, "currency", None),
                "market_cap": _safe_float(getattr(fi, "market_cap", None)),
            })
        try:
            raw = ticker.info
            if isinstance(raw, dict):
                for key in ("longName", "shortName", "sector", "industry", "longBusinessSummary", "website"):
                    if key in raw and raw[key]:
                        info[key] = raw[key]
        except Exception:
            pass
    except Exception:
        pass
    return info


def fetch_news(symbol: str, limit: int = MAX_NEWS_ITEMS) -> List[Dict]:
    sym = _normalize_symbol(symbol)
    items: List[Dict] = []
    try:
        ticker = yf.Ticker(sym)
        raw_news: List[Dict[str, Any]] = []
        try:
            raw_news = ticker.news or []
        except Exception:
            raw_news = []
        if not raw_news:
            try:
                fetched = ticker.get_news()
                if isinstance(fetched, list):
                    raw_news = fetched
            except Exception:
                raw_news = []
        for article in raw_news[:limit]:
            if not isinstance(article, dict):
                continue

            # Handle new yfinance API structure where content is nested
            content = article.get("content", article)

            # Extract title
            title = _safe_str(content.get("title"))

            # Extract link - try clickThroughUrl first, then canonicalUrl, then fallback to old structure
            link = None
            click_through = content.get("clickThroughUrl")
            canonical = content.get("canonicalUrl")
            if isinstance(click_through, dict):
                link = _safe_str(click_through.get("url"))
            elif isinstance(canonical, dict):
                link = _safe_str(canonical.get("url"))
            else:
                link = _safe_str(content.get("link"))

            # Extract publisher
            publisher = None
            provider = content.get("provider")
            if isinstance(provider, dict):
                publisher = _safe_str(provider.get("displayName"))
            else:
                publisher = _safe_str(content.get("publisher"))

            # Extract type
            article_type = _safe_str(content.get("contentType") or content.get("type"))

            # Extract thumbnail - check new structure first
            thumbnail = None
            thumb_obj = content.get("thumbnail")
            if isinstance(thumb_obj, dict):
                thumbnail = _safe_str(thumb_obj.get("originalUrl"))
                if not thumbnail:
                    resolutions = thumb_obj.get("resolutions", [])
                    if isinstance(resolutions, list) and len(resolutions) > 0:
                        thumbnail = _safe_str(resolutions[0].get("url"))
            if not thumbnail:
                thumbnail = _extract_thumbnail_url(content)

            # Extract publish time - try new pubDate field first
            pub_time = content.get("pubDate")
            if pub_time:
                # pubDate is already in ISO format
                published_at = _safe_str(pub_time)
            else:
                # Fallback to old providerPublishTime (Unix timestamp)
                published_at = _timestamp_to_iso(content.get("providerPublishTime"))

            sanitized = {
                "title": title,
                "link": link,
                "publisher": publisher,
                "type": article_type,
                "thumbnail": thumbnail,
                "published_at": published_at,
            }
            if not (sanitized["title"] or sanitized["link"]):
                continue
            items.append(sanitized)
    except Exception:
        return items
    return items


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
    try:
        t = yf.Ticker(sym)
        info = {}
        # Prefer fast_info basic fields
        if hasattr(t, "fast_info"):
            fi = t.fast_info
            info.update({
                "last_price": getattr(fi, "last_price", None),
                "market_cap": getattr(fi, "market_cap", None),
                "previous_close": getattr(fi, "previous_close", None),
            })
        # Try to enrich via .info (can be slower)
        try:
            raw = t.info
            if isinstance(raw, dict):
                for key in ("longName", "shortName", "currency", "exchange"):
                    if key in raw:
                        info[key] = raw[key]
        except Exception:
            pass
        return info
    except Exception:
        return {}
