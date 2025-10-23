# Stock AB List Tracker

A simple web-based tool to track stocks in two categories: A (trade soon) and B (wait). It uses Flask for the backend, yfinance for live prices, and a local JSON file for persistence.

## Features
- Add, edit, and delete stocks
- Categorize into A (trade soon) and B (wait)
- Real-time price updates via yfinance
- Percent change tracking from initial price
- Persistent local storage in `stocks.json`
- Auto-refresh prices every 60 seconds
- Manual refresh button

## Installation

### Prerequisites
- Python 3.8+

### Setup
1. Clone or download this repository.
2. (Optional) Create and activate a virtual environment.
3. Install dependencies:

```bash
pip install -r requirements.txt
```

## Usage
1. Run the application:

```bash
python app.py
```

2. Open your browser at `http://localhost:5000`.
3. Add stocks by entering symbol, initial price, reason, date spotted, and list type (A/B).
4. Prices update automatically when adding stocks and every 60 seconds.
5. Edit by clicking Edit, modify fields, then save.
6. Delete by clicking Delete and confirming.
7. Use the Refresh button to update prices manually.

## Technology Stack
- Flask (Python)
- yfinance
- JSON file storage
- HTML/CSS/Vanilla JavaScript

## Data Storage
`stocks.json` is created automatically on first run and stores all stock data locally. You can back it up or put it under version control if desired (by removing it from `.gitignore`).

## API Endpoints
- `GET /api/stocks` — list all stocks grouped by list type
- `POST /api/stocks` — create a new stock
- `PUT /api/stocks/<id>` — update a stock
- `DELETE /api/stocks/<id>` — delete a stock
- `GET /api/stocks/prices` — get current prices and percent changes for all stocks

## Notes
- yfinance is used for price fetching and may have rate limits for excessive requests.
- Prices are fetched for all stocks whenever a new stock is added.
- Auto-refresh runs every 60 seconds.
- Stock symbols should be valid ticker symbols (e.g., AAPL, MSFT, GOOGL).
