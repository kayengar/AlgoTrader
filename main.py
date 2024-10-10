import yfinance as yf
import pandas as pd
import requests
import datetime
import os
import pickle  # For storing cache
from dotenv import load_dotenv
from dateutil import parser, tz

# Load environment variables from .env file
load_dotenv()

# Alpaca API setup
APCA_API_BASE_URL = os.getenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
APCA_API_KEY_ID = os.getenv("APCA_API_KEY")
APCA_API_SECRET_KEY = os.getenv("APCA_API_SECRET")

# Twilio API setup for SMS (optional)
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
MY_PHONE_NUMBER = os.getenv("MY_PHONE_NUMBER")

# Set up headers for Alpaca API
HEADERS = {
    "APCA-API-KEY-ID": APCA_API_KEY_ID,
    "APCA-API-SECRET-KEY": APCA_API_SECRET_KEY
}

# Define cache file and expiration time (1 day)
CACHE_FILE = '/app/data/stock_data_cache.pkl'  # Ensure the cache is in a data folder
RISK_PERCENTAGE = 0.02  # Risk 2% of available balance on each trade
LONG_TERM_HOLD_PERIOD = 365  # 365 days for long-term capital gains
COOLDOWN_PERIOD_HOURS = 720  # Cooldown period to avoid frequent buying

# Fetch S&P 500 symbols from Wikipedia
def get_sp500_symbols():
    sp500_url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    table = pd.read_html(sp500_url)
    df = table[0]  # First table contains the S&P 500 data
    return df['Symbol'].tolist()

# Load cache from file
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, 'rb') as f:
            return pickle.load(f)
    return {}

# Save cache to file
def save_cache(cache):
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump(cache, f)

# Clear cache daily
def clear_cache_if_needed(cache, date_str):
    # If the cache contains data from a previous day, clear it
    if 'date' not in cache or cache['date'] != date_str:
        print("Clearing old cache data...")
        return {'date': date_str}  # Reset cache for the new day
    return cache

# Fetch stock data using yfinance and cache it locally
def fetch_stock_data(ticker, cache, date_str, period="200d"):
    # Check if the data is cached for today
    if ticker in cache and 'data' in cache[ticker]:
        print(f"Using cached data for {ticker}")
        return cache[ticker]['data']

    # Fetch data from Yahoo Finance
    print(f"Fetching new data for {ticker} for {period}")
    stock_data = yf.download(ticker, period=period)

    # Update the cache with the fresh data
    if ticker not in cache:
        cache[ticker] = {}
    cache[ticker]['data'] = stock_data

    # Save the updated cache
    save_cache(cache)
    return stock_data

# Calculate RSI
def calculate_rsi(data, window=14):
    delta = data['Close'].diff(1)
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=window, min_periods=window).mean()
    avg_loss = loss.rolling(window=window, min_periods=window).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    data['RSI'] = rsi
    return data

# Calculate Moving Averages
def calculate_moving_averages(data, short_window=50, long_window=200):
    data['50_MA'] = data['Close'].rolling(window=short_window).mean()
    data['200_MA'] = data['Close'].rolling(window=long_window).mean()
    return data

# Get Alpaca account balance
def get_account_balance():
    account_url = f"{APCA_API_BASE_URL}/v2/account"
    response = requests.get(account_url, headers=HEADERS)
    account_info = response.json()

    if response.status_code != 200:
        raise Exception(f"Error fetching account info: {account_info}")

    # Use 'cash' or 'buying_power' as available
    cash = account_info.get('cash')
    buying_power = account_info.get('buying_power')
    status = account_info.get('status')

    if cash:
        return float(cash), status
    elif buying_power:
        return float(buying_power), status
    else:
        raise KeyError(f"'cash' or 'buying_power' not found in the response. Response: {account_info}")

# Calculate the number of shares to buy based on risk percentage
def calculate_shares_to_buy(current_price, balance):
    # Risk 2% of the available balance
    risk_amount = balance * RISK_PERCENTAGE
    shares_to_buy = risk_amount // current_price  # Floor division to get integer shares
    return int(shares_to_buy)

# Get last buy time from Alpaca for a given ticker
def get_last_buy_time(ticker):
    orders_url = f"{APCA_API_BASE_URL}/v2/orders"
    params = {
        "status": "filled",
        "side": "buy",
        "symbols": ticker,
        "limit": 100,
        "direction": "desc"
    }
    response = requests.get(orders_url, headers=HEADERS, params=params)
    orders = response.json()

    if response.status_code != 200:
        print(f"Error fetching orders for {ticker}: {orders}")
        return None

    for order in orders:
        if order['symbol'] == ticker and order['side'] == 'buy':
            filled_at = order.get('filled_at')
            if filled_at:
                # Parse the filled_at time using dateutil.parser
                last_buy_time = parser.parse(filled_at)
                return last_buy_time
    return None

# Get current position for a ticker
def get_position(ticker):
    positions_url = f"{APCA_API_BASE_URL}/v2/positions/{ticker}"
    response = requests.get(positions_url, headers=HEADERS)
    if response.status_code == 200:
        position = response.json()
        qty = float(position['qty'])
        avg_entry_price = float(position['avg_entry_price'])
        return qty, avg_entry_price
    elif response.status_code == 404:
        # No position
        return 0, 0
    else:
        print(f"Error fetching position for {ticker}: {response.text}")
        return 0, 0

# Place buy order on Alpaca
def place_buy_order(ticker, quantity):
    print(f"Placing buy order for ticker {ticker}")
    order_url = f"{APCA_API_BASE_URL}/v2/orders"
    order_data = {
        "symbol": ticker,
        "qty": quantity,
        "side": "buy",
        "type": "market",
        "time_in_force": "gtc"
    }
    response = requests.post(order_url, json=order_data, headers=HEADERS)
    return response.json()

# Place sell order on Alpaca
def place_sell_order(ticker, quantity, purchase_date):
    print(f"Placing sell order for ticker {ticker}")
    holding_period = (datetime.datetime.now() - purchase_date).days if purchase_date else 0
    if holding_period < LONG_TERM_HOLD_PERIOD:
        print(f"Warning: Selling {ticker} will trigger short-term capital gains!")
    order_url = f"{APCA_API_BASE_URL}/v2/orders"
    order_data = {
        "symbol": ticker,
        "qty": quantity,
        "side": "sell",
        "type": "market",
        "time_in_force": "gtc"
    }
    response = requests.post(order_url, json=order_data, headers=HEADERS)
    return response.json()

# Send SMS notification via Twilio (optional)
def send_sms(message):
    print("SMS:", message)
    # Uncomment the lines below to enable SMS notifications
    # client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    # client.messages.create(
    #     body=message,
    #     from_=TWILIO_PHONE_NUMBER,
    #     to=MY_PHONE_NUMBER
    # )

# Combined strategy to execute buy/sell based on Moving Average + RSI
def execute_strategy(ticker, stock_data):
    # Calculate RSI and Moving Averages
    stock_data = calculate_rsi(stock_data)
    stock_data = calculate_moving_averages(stock_data)

    # Ensure there are enough data points
    if stock_data.shape[0] < 200:
        print(f"Not enough data for {ticker}, skipping.")
        return

    # Get latest data points
    latest = stock_data.iloc[-1]

    # Skip if any required indicators are NaN
    if pd.isna(latest['50_MA']) or pd.isna(latest['200_MA']) or pd.isna(latest['RSI']):
        print(f"Indicators not available for {ticker}, skipping.")
        return

    # Check account balance
    balance, status = get_account_balance()
    if balance <= 0:
        send_sms("Account balance is negative or zero, stopping execution.")
        return

    # Get last buy time and position from Alpaca API
    last_buy_time = get_last_buy_time(ticker)
    position_qty, avg_entry_price = get_position(ticker)

    # Apply cooldown period to avoid frequent buys
    if last_buy_time:
        time_since_last_buy = datetime.datetime.now(tz=tz.UTC) - last_buy_time
        if time_since_last_buy < datetime.timedelta(hours=COOLDOWN_PERIOD_HOURS):
            print(f"Cooldown active for {ticker}. Time since last buy: {time_since_last_buy}")
            return

    # Buy signal: 50_MA > 200_MA and RSI < 30 (oversold)
    if latest['50_MA'] > latest['200_MA'] and latest['RSI'] < 30 and position_qty == 0:
        current_price = latest['Close']
        shares_to_buy = calculate_shares_to_buy(current_price, balance)
        if shares_to_buy > 0:
            order_response = place_buy_order(ticker, shares_to_buy)
            send_sms(f"Buy order placed for {ticker}: {shares_to_buy} shares.")
            print(f"Buy order response: {order_response}")
        else:
            print(f"Insufficient funds to buy {ticker}.")
    # Sell signal: 50_MA < 200_MA and RSI > 70 (overbought)
    elif latest['50_MA'] < latest['200_MA'] and latest['RSI'] > 70 and position_qty > 0:
        purchase_date = last_buy_time
        shares_to_sell = position_qty
        order_response = place_sell_order(ticker, shares_to_sell, purchase_date)
        send_sms(f"Sell order placed for {ticker}: {shares_to_sell} shares.")
        print(f"Sell order response: {order_response}")
    else:
        print(f"No action for {ticker} at this time.")

# Main execution loop
if __name__ == "__main__":
    # Get S&P 500 symbols
    stocks = get_sp500_symbols()
    print(f"Fetched {len(stocks)} S&P 500 stocks.")

    # Load the cache from file
    cache = load_cache()

    # Get the current date (used to validate cache)
    today_str = datetime.date.today().strftime("%Y-%m-%d")

    # Clear the cache if it contains data from a previous day
    cache = clear_cache_if_needed(cache, today_str)

    # Loop through all stocks and apply your strategy
    for stock in stocks:
        # Skip stocks that cause issues
        if stock.startswith("BRK") or stock.startswith("BF"):
            continue
        stock_data = fetch_stock_data(stock, cache, today_str, period="250d")  # Fetch last 250 days of data
        execute_strategy(stock, stock_data)

    # Save the cache at the end
    save_cache(cache)
