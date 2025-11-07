import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import app as app_module
from app import app


@pytest.fixture()
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_stock_detail_page(client):
    response = client.get("/stocks/AAPL")
    assert response.status_code == 200
    assert b"AAPL" in response.data


def test_search_returns_matches(client, monkeypatch):
    sample = [
        {"id": "1", "symbol": "AAPL", "list_type": "A"},
        {"id": "2", "symbol": "AMZN", "list_type": "B"},
        {"id": "3", "symbol": "MSFT", "list_type": "PA"},
    ]
    monkeypatch.setattr("app._read_stocks", lambda *args, **kwargs: sample)

    response = client.get("/api/stocks/search?q=a")
    assert response.status_code == 200
    data = response.get_json()
    assert {item["symbol"] for item in data["results"]} >= {"AAPL", "AMZN"}


def test_search_handles_empty_query(client, monkeypatch):
    monkeypatch.setattr("app._read_stocks", lambda *args, **kwargs: [])

    response = client.get("/api/stocks/search?q=")
    assert response.status_code == 200
    data = response.get_json()
    assert data["results"] == []


def test_search_rejects_overlong_query(client):
    response = client.get("/api/stocks/search?q=" + ("A" * 40))
    assert response.status_code == 400


def test_history_success(client, monkeypatch):
    def fake_fetch(symbol, interval):
        assert symbol == "MSFT"
        assert interval == "1d"
        return {"symbol": symbol, "interval": interval, "points": [{"date": "2024-01-01", "close": 310.0}]}

    monkeypatch.setattr("app.fetch_price_history", fake_fetch)
    response = client.get("/api/stocks/MSFT/history?interval=1d")
    assert response.status_code == 200
    data = response.get_json()
    assert data["points"][0]["close"] == 310.0


def test_history_invalid_interval(client, monkeypatch):
    def fake_fetch(symbol, interval):
        raise ValueError("interval must be 1d or 1wk")

    monkeypatch.setattr("app.fetch_price_history", fake_fetch)
    response = client.get("/api/stocks/MSFT/history?interval=5m")
    assert response.status_code == 400
    data = response.get_json()
    assert "error" in data


def test_indicators_require_windows(client):
    response = client.get("/api/stocks/AAPL/indicators?type=sma")
    assert response.status_code == 400


def test_indicators_invalid_type(client):
    response = client.get("/api/stocks/AAPL/indicators?type=macd&windows=12")
    assert response.status_code == 400


def test_indicators_success(client, monkeypatch):
    def fake_compute(symbol, interval, windows):
        assert symbol == "AAPL"
        assert interval == "1d"
        assert windows == [20]
        return {
            "symbol": symbol,
            "interval": interval,
            "indicators": [
                {
                    "type": "sma",
                    "window": 20,
                    "values": [{"date": "2024-01-01", "value": 150.0}],
                }
            ],
        }

    monkeypatch.setattr("app.compute_sma", fake_compute)
    response = client.get("/api/stocks/AAPL/indicators?type=sma&interval=1d&windows=20")
    assert response.status_code == 200
    data = response.get_json()
    assert data["indicators"][0]["window"] == 20


def test_rsi_requires_valid_period(client):
    response = client.get("/api/stocks/AAPL/rsi?period=abc")
    assert response.status_code == 400


def test_rsi_success(client, monkeypatch):
    def fake_rsi(symbol, interval, period):
        assert symbol == "AAPL"
        assert interval == "1d"
        assert period == 14
        return {
            "symbol": symbol,
            "interval": interval,
            "period": period,
            "values": [{"date": "2024-01-01", "value": 55.0}],
        }

    monkeypatch.setattr("app.compute_rsi", fake_rsi)
    response = client.get("/api/stocks/AAPL/rsi?interval=1d&period=14")
    assert response.status_code == 200
    data = response.get_json()
    assert data["values"][0]["value"] == 55.0


def test_news_invalid_limit(client):
    response = client.get("/api/stocks/AAPL/news?limit=bad")
    assert response.status_code == 400


def test_news_success(client, monkeypatch):
    def fake_news(symbol, limit=10):
        assert symbol == "AAPL"
        assert limit == 5
        return [
            {
                "title": "Test Article",
                "link": "https://example.com",
                "publisher": "Example",
                "published_at": "2024-01-01T00:00:00",
            }
        ]

    monkeypatch.setattr("app.fetch_news", fake_news)
    response = client.get("/api/stocks/AAPL/news?limit=5")
    assert response.status_code == 200
    data = response.get_json()
    assert data["articles"][0]["title"] == "Test Article"


def test_overview_success(client, monkeypatch):
    def fake_overview(symbol):
        return {"symbol": symbol, "longName": "Sample Corp", "last_price": 123.45}

    monkeypatch.setattr("app.fetch_overview", fake_overview)
    response = client.get("/api/stocks/AAPL/overview")
    assert response.status_code == 200
    data = response.get_json()
    assert data["longName"] == "Sample Corp"


def test_snapshot_returns_latest_entry(client, monkeypatch):
    monkeypatch.setitem(app_module._index_state, "by_symbol", {})
    monkeypatch.setitem(app_module._index_state, "by_id", {})
    sample = [
        {
            "id": "1",
            "symbol": "AAPL",
            "initial_price": 120.0,
            "date_spotted": "2024-01-01",
            "date_added": "2024-01-02",
            "list_type": "A",
        },
        {
            "id": "2",
            "symbol": "AAPL",
            "initial_price": 130.0,
            "date_spotted": "2024-03-01",
            "date_added": "2024-03-02",
            "list_type": "B",
        },
    ]

    monkeypatch.setattr("app._read_stocks", lambda *args, **kwargs: sample)
    monkeypatch.setattr("app.get_current_prices", lambda symbols: {symbols[0]: 150.0})

    def fake_percent_change(initial, current):
        if initial in (None, 0) or current is None:
            return None
        return ((current - initial) / initial) * 100

    monkeypatch.setattr("app.calculate_percent_change", fake_percent_change)

    response = client.get("/api/stocks/AAPL/snapshot")
    assert response.status_code == 200
    data = response.get_json()
    assert data["initial_price"] == 130.0
    assert data["current_price"] == 150.0
    assert pytest.approx(data["percent_change"], rel=1e-6) == ((150.0 - 130.0) / 130.0) * 100
    assert data["list_type"] == "B"


def test_snapshot_not_found(client, monkeypatch):
    monkeypatch.setitem(app_module._index_state, "by_symbol", {})
    monkeypatch.setitem(app_module._index_state, "by_id", {})
    monkeypatch.setattr("app._read_stocks", lambda *args, **kwargs: [])
    response = client.get("/api/stocks/TSLA/snapshot")
    assert response.status_code == 404
    data = response.get_json()
    assert "error" in data
