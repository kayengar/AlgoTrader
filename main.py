import yfinance as yf
import pandas as pd
import requests
import datetime
import os
import pickle  # For storing cache
from twilio.rest import Client
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Alpaca API setup
APCA_API_BASE_URL = "https://paper-api.alpaca.markets"
APCA_API_KEY_ID = os.getenv("ALPACA_API_KEY")
APCA_API_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# Twilio API setup for SMS
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
CACHE_FILE = 'stock_data_cache.pkl'
RISK_PERCENTAGE = 0.02  # Risk 2% of available balance on each trade
LONG_TERM_HOLD_PERIOD = 365  # 365 days for long-term capital gains
WASH_SALE_PERIOD = 30  # 30 days to avoid wash-sale rule


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

    # Fetch data from Yahoo Finance (e.g., 200 days of data for moving averages)
    print(f"Fetching new data for {ticker} for {period}")
    stock_data = yf.download(ticker, period=period)

    # Update the cache with the fresh data
    cache[ticker] = {
        'data': stock_data
    }

    # Save the updated cache
    save_cache(cache)

    return stock_data


# Calculate RSI
def calculate_rsi(data, window=14):
    delta = data['Close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=window).mean()
    avg_loss = loss.rolling(window=window).mean()
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
    account_info = requests.get(account_url, headers=HEADERS).json()
    return float(account_info['cash']), account_info['status']


# Calculate the number of shares to buy based on risk percentage
def calculate_shares_to_buy(ticker, current_price, balance):
    # Risk 2% of the available balance
    risk_amount = balance * RISK_PERCENTAGE
    shares_to_buy = risk_amount // current_price  # Floor division to get integer shares
    return int(shares_to_buy)


# Check holding period to determine long-term or short-term capital gains
def check_holding_period(ticker, cache):
    if 'purchase_date' in cache[ticker]:
        purchase_date = cache[ticker]['purchase_date']
        holding_period = (datetime.datetime.now() - purchase_date).days
        return holding_period
    return 0


# Place buy order on Alpaca
def place_buy_order(ticker, quantity):
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


# Place sell order on Alpaca, avoiding short-term capital gains if possible
def place_sell_order(ticker, quantity, cache):
    holding_period = check_holding_period(ticker, cache)
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


# Send SMS notification via Twilio
def send_sms(message):
    client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
    client.messages.create(
        body=message,
        from_=TWILIO_PHONE_NUMBER,
        to=MY_PHONE_NUMBER
    )


# Combined strategy to execute buy/sell based on Moving Average + RSI
def execute_strategy(ticker, stock_data, cache):
    # Calculate RSI and Moving Averages
    stock_data = calculate_rsi(stock_data)
    stock_data = calculate_moving_averages(stock_data)

    # Get latest data points
    latest = stock_data.iloc[-1]
    previous = stock_data.iloc[-2]

    # Check account balance
    balance, status = get_account_balance()
    if balance <= 0:
        send_sms("Account balance is negative or zero, stopping execution.")
        return

    # Buy signal: 50_MA > 200_MA and RSI < 30 (oversold)
    if latest['50_MA'] > latest['200_MA'] and latest['RSI'] < 30:
        current_price = latest['Close']
        if ticker not in cache:
            cache[ticker] = {}

        # Calculate the number of shares to buy
        shares_to_buy = calculate_shares_to_buy(ticker, current_price, balance)
        if shares_to_buy > 0:
            place_buy_order(ticker, shares_to_buy)
            send_sms(f"Buy order placed for {ticker}: {shares_to_buy} shares.")
            # Update cache with position and last buy action
            cache[ticker]['position'] = cache[ticker].get('position', 0) + shares_to_buy
            cache[ticker]['last_action'] = 'buy'
            cache[ticker]['last_time'] = datetime.datetime.now()
            cache[ticker]['purchase_date'] = datetime.datetime.now()  # Track the purchase date

    # Sell signal: 50_MA < 200_MA and RSI > 70 (overbought)
    if latest['50_MA'] < latest['200_MA'] and latest['RSI'] > 70 and ticker in cache and 'position' in cache[ticker]:
        shares_to_sell = cache[ticker]['position']
        place_sell_order(ticker, shares_to_sell, cache)
        send_sms(f"Sell order placed for {ticker}: {shares_to_sell} shares.")
        cache[ticker]['position'] = 0  # Reset position after selling
        cache[ticker]['last_action'] = 'sell'
        cache[ticker]['last_time'] = datetime.datetime.now()


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
        stock_data = fetch_stock_data(stock, cache, today_str, period="200d")  # Fetch last 200 days of data
        execute_strategy(stock, stock_data, cache)
