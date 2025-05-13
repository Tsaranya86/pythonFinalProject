import matplotlib
matplotlib.use('Agg') # Use 'Agg' backend for non-interactive plotting

import yfinance as yf
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, Response
from flask_socketio import SocketIO
from datetime import datetime, timedelta
import threading
import time
import logging
import matplotlib.pyplot as plt
import io
import requests

app = Flask(__name__)
socketio = SocketIO(app)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Cache for stock data
stock_cache = {}
last_fetch_time = {}

# List of top US companies by market cap
COMPANIES = {
    'AAPL': 'Apple', 'MSFT': 'Microsoft', 'NVDA': 'Nvidia', 'GOOGL': 'Alphabet',
    'AMZN': 'Amazon'
}

# Function to fetch stock data with caching
def fetch_stock_data(symbol, start_date, end_date):
    current_time = datetime.now()
    cache_key = f"{symbol}_{start_date}_{end_date}"
    
    if cache_key in stock_cache and (current_time - last_fetch_time.get(cache_key, current_time)).total_seconds() < 300:
        logger.info(f"Returning cached data for {symbol}")
        return stock_cache[cache_key]

    try:
        stock = yf.Ticker(symbol)
        df = stock.history(start=start_date, end=end_date)
        if df.empty:
            raise ValueError(f"No data found for {symbol}")

        df.reset_index(inplace=True)
        df['Date'] = df['Date'].dt.strftime('%Y-%m-%d %H:%M:%S')
        df = df[['Date', 'Open', 'High', 'Low', 'Close', 'Volume']]
        
        stock_cache[cache_key] = df
        last_fetch_time[cache_key] = current_time
        logger.info(f"Fetched data for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Failed to fetch data for {symbol}: {e}")
        return pd.DataFrame()

# Function to check significant price changes
def check_price_change(df, symbol, threshold=5):
    if len(df) < 2:
        return None
    latest_price = df['Close'].iloc[-1]
    previous_price = df['Close'].iloc[-2]
    percentage_change = ((latest_price - previous_price) / previous_price) * 100
    if abs(percentage_change) >= threshold:
        return f"🚨 {symbol} price changed by {percentage_change:.2f}% (Latest: ${latest_price:.2f})"
    return None

    # Background thread for live stock updates using Marketstack API
    
MARKETSTACK_API_KEY = 'fb94be82653b839a3a87920522c84e0e'  # Replace with your Marketstack API key

def fetch_live_stock_data():
    while True:
        for symbol in COMPANIES.keys():
            try:
                url = f"http://api.marketstack.com/v1/eod/latest"
                params = {
                    "access_key": MARKETSTACK_API_KEY,
                    "symbols": symbol
                }
                response = requests.get(url, params=params)
                response.raise_for_status()  # Raise error for failed requests
                data = response.json()

                if "data" in data and len(data["data"]) > 0:
                    latest_data = data["data"][0]
                    price_update = {
                        "symbol": symbol,
                        "price": latest_data["close"]  # 'close' is the latest price field
                    }
                    # Store in cache
                    cache_key = f"{symbol}_live"
                    stock_cache[cache_key] = price_update
                    last_fetch_time[cache_key] = datetime.now()

                    # Emit update to frontend
                    socketio.emit('stock_update', price_update)
                    logger.info(f"Emitted live update for {symbol}: {price_update['price']}")
                else:
                    raise ValueError(f"No live data available for {symbol}")
            except Exception as e:
                logger.error(f"Error fetching live data for {symbol}: {e}")
        time.sleep(60)  # Update every hour

threading.Thread(target=fetch_live_stock_data, daemon=True).start()

@socketio.on('connect')
def handle_connect():
    print("Client connected")

# Routes for rendering and data updates
@app.route('/trends')
def trends():
    start_date = request.args.get('start_date', (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))

    plt.figure(figsize=(12, 8))
    for symbol in COMPANIES.keys():
        df = fetch_stock_data(symbol, start_date, end_date)
        if not df.empty:
            plt.plot(df['Date'], df['Close'], label=COMPANIES[symbol])

    plt.title("Stock Price Trends")
    plt.xlabel("Date")
    plt.ylabel("Close Price (USD)")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()

    img = io.BytesIO()
    plt.savefig(img, format='png')
    img.seek(0)
    plt.close()

    return Response(img, mimetype='image/png')

# Route for the homepage
@app.route('/')
def index():
    symbol = 'AAPL'
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)  # 1 year of data
    
    df = fetch_stock_data(symbol, start_date.strftime('%Y-%m-%d'), end_date.strftime('%Y-%m-%d'))
    if df.empty:
        return render_template('index.html', error=f"Failed to load data for {symbol}. Please try again later.", companies=COMPANIES)

    notification = check_price_change(df, symbol)
    
    # Convert DataFrame to HTML for display
    hist_data_html = df.to_html(classes='table table-striped', index=False)
    
    return render_template('index.html',
                         hist_data=hist_data_html,
                         notification=notification,
                         default_symbol=symbol,
                         companies=COMPANIES)
    

@app.route('/update', methods=['POST'])
def update():
    symbol = request.form.get('company', 'AAPL')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    df = fetch_stock_data(symbol, start_date, end_date)
    notification = check_price_change(df, symbol)
    hist_data_html = df.to_html(classes='table table-striped', index=False)

    return jsonify({'hist_data': hist_data_html, 'notification': notification})

@app.route('/export_csv', methods=['POST'])
def export_csv():
    symbol = request.form.get('company')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')

    df = fetch_stock_data(symbol, start_date, end_date)
    csv_path = f"{symbol}_data.csv"
    df.to_csv(csv_path, index=False)

    return send_file(csv_path, as_attachment=True, download_name=f"{symbol}_stock_data.csv")

@app.route('/plot/<symbol>')
def plot(symbol):
    start_date = request.args.get('start_date', (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d'))
    end_date = request.args.get('end_date', datetime.now().strftime('%Y-%m-%d'))
    print(f"Plotting data for {symbol} from {start_date} to {end_date}")
    
    df = fetch_stock_data(symbol, start_date, end_date)
    if df.empty:
        return "No data available to plot", 404

    plt.figure(figsize=(10, 6))
    plt.plot(df['Date'], df['Close'], label='Close Price', color='blue')
    plt.title(f"{COMPANIES.get(symbol, symbol)} Stock Prices")
    plt.xlabel("Date")
    plt.ylabel("Close Price (USD)")
    plt.xticks(rotation=45)
    plt.legend()
    plt.tight_layout()

    img = io.BytesIO()
    plt.savefig(img, format='png')
    img.seek(0)
    plt.close() 

    return Response(img, mimetype='image/png')

if __name__ == '__main__':
    socketio.run(app, debug=True)
