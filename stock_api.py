from __future__ import annotations

from typing import Dict, Iterable, Optional

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
