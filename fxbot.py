import time
import hmac
import hashlib
import json
import os
import threading
import requests
import datetime
import yfinance as yf
import pandas as pd
import numpy as np

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
ADMIN_CHAT_IDS = [
    "1345385952",           # Admin 1 (Aap)
    "SECOND_USER_CHAT_ID"   # Admin 2 (Dusre person ki Telegram ID)
]

# --- COINDCX API CREDENTIALS ---
# Render Environment se uthayega, ya fallback string use karega
COINDCX_KEY = os.getenv("COINDCX_API_KEY", "YOUR_COINDCX_KEY_HERE")
COINDCX_SECRET = os.getenv("COINDCX_SECRET_KEY", "YOUR_COINDCX_SECRET_HERE")

# --- STRATEGY CONFIGURATION ---
STRATEGIES = {
    "SCALP": {
        "label": "⚡ [SCALP - 15M/5M/1M]",
        "htf_interval": "15m",
        "htf_period": "5d",
        "mtf_interval": "5m",
        "mtf_period": "5d",
        "entry_interval": "1m",
        "entry_period": "1d",
        "adx_min": 23,
        "tp_multipliers": [1.5, 2.0, 3.0, 5.0]
    },
    "INTRADAY": {
        "label": "⏱️ [INTRADAY - 1D/4H/1H to 5M/15M]",
        "htf_interval": "1d",
        "htf_period": "60d",
        "mtf_interval": "1h",
        "mtf_period": "30d",
        "entry_interval": "5m",
        "entry_period": "5d",
        "adx_min": 22,
        "tp_multipliers": [1.5, 2.5, 3.5, 5.0]
    },
    "SWING": {
        "label": "🌊 [SWING - 1W/1D to 1H]",
        "htf_interval": "1wk",
        "htf_period": "2y",
        "mtf_interval": "1d",
        "mtf_period": "1y",
        "entry_interval": "1h",
        "entry_period": "1mo",
        "adx_min": 20,
        "tp_multipliers": [2.0, 3.0, 4.5, 6.0]
    }
}

# CoinDCX Market Pairs & Fixed Trade Quantity (Apne capital ke hisab se set karein)
SYMBOLS = {
    "BTC/INR": {"yf": "BTC-USD", "coindcx": "BTCINR", "qty": 0.0005},
    "ETH/INR": {"yf": "ETH-USD", "coindcx": "ETHINR", "qty": 0.008},
    "SOL/INR": {"yf": "SOL-USD", "coindcx": "SOLINR", "qty": 0.15},
    "DOGE/INR": {"yf": "DOGE-USD", "coindcx": "DOGEINR", "qty": 100.0}
}

# Live Active Positions
active_positions = {
    strat_key: {
        name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0}
        for name in SYMBOLS
    }
    for strat_key in STRATEGIES
}

# --- COINDCX ORDER EXECUTION ENGINE ---
def place_coindcx_order(market_pair, side, quantity, price_per_unit):
    """
    CoinDCX API par HMAC-SHA256 signature generate karke real order execute karta hai
    """
    url = "https://api.coindcx.com/exchange/v1/orders/create"
    timeStamp = int(round(time.time() * 1000))
    
    body = {
        "side": side.lower(),               # 'buy' or 'sell'
        "order_type": "market_order",       # instant market fill
        "market": market_pair,              # e.g., 'BTCINR'
        "total_quantity": quantity,
        "timestamp": timeStamp
    }
    
    json_payload = json.dumps(body, separators=(',', ':'))
    signature = hmac.new(COINDCX_SECRET.encode('utf-8'), json_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    
    headers = {
        'Content-Type': 'application/json',
        'X-AUTH-APIKEY': COINDCX_KEY,
        'X-AUTH-SIGNATURE': signature
    }
    
    try:
        response = requests.post(url, data=json_payload, headers=headers, timeout=10)
        res_data = response.json()
        if response.status_code == 200:
            return True, res_data
        else:
            return False, res_data.get("message", str(res_data))
    except Exception as e:
        return False, str(e)

# --- TELEGRAM ENGINE (DUAL-ADMIN SUPPORT) ---
def send_telegram(message, chat_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    recipients = [chat_id] if chat_id else [cid for cid in ADMIN_CHAT_IDS if cid != "SECOND_USER_CHAT_ID"]
    
    for cid in recipients:
        payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram error to {cid}: {e}")

def handle_incoming_users():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 20}
            resp = requests.get(url, params=params, timeout=25).json()

            if "result" in resp:
                for update in resp["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        sender_id = str(update["message"]["chat"]["id"])
                        user_name = update["message"]["from"].get("first_name", "Trader")

                        if sender_id in ADMIN_CHAT_IDS:
                            send_telegram(
                                f"👑 *CoinDCX Live Engine ({user_name})*\n\n"
                                f"🤖 Status: `Online & Armed`\n"
                                f"⚡ Engine: `Scalp | Intraday | Swing`\n"
                                f"🪙 Execution: `CoinDCX API Connected`\n"
                                f"🛡️ Trailing SL: `Active`",
                                chat_id=sender_id
                            )
                        else:
                            send_telegram(f"Hello {user_name}! 🔒 Private bot. Access restricted.", chat_id=sender_id)
        except Exception as e:
            print(f"Listener issue: {e}")
        time.sleep(2)

# --- INDICATOR CALCULATIONS ---
def calculate_atr_and_adx(df, length=14):
    high = pd.Series(np.array(df['High']).flatten(), index=df.index)
    low = pd.Series(np.array(df['Low']).flatten(), index=df.index)
    close = pd.Series(np.array(df['Close']).flatten(), index=df.index)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(length).mean()

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0).flatten()
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0).flatten()

    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(length).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(length).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(length).mean()
    return atr, adx

def get_trend(ticker_symbol, period, interval):
    try:
        data = yf.download(ticker_symbol, period=period, interval=interval, progress=False)
        if data is None or len(data) < 30:
            return "NEUTRAL"
        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        ema50 = close_series.ewm(span=min(50, len(close_series)), adjust=False).mean()
        last_close = float(close_series.iloc[-1])
        last_ema = float(ema50.iloc[-1])

        if last_close > last_ema:
            return "BULLISH"
        elif last_close < last_ema:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

# --- POSITION & TRAILING SL MANAGEMENT ---
def manage_trailing_sl(strat_key, strat_label, name, sym_cfg, curr_price):
    pos = active_positions[strat_key][name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]

    if pos["side"] == "BUY":
        if curr_price > pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price - trailing_gap
            if new_sl > pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(
                    f"🛡️ *TRAILING SL RAISED*\n"
                    f"🏷️ `{strat_label}` | `{name}`\n"
                    f"📈 High: `{pos['best_price']:.2f}`\n"
                    f"🛑 New SL: `{pos['sl']:.2f}`"
                )
        elif curr_price <= pos["sl"]:
            # Trigger Real Sell Order on CoinDCX
            success, resp = place_coindcx_order(coindcx_pair, "sell", qty, curr_price)
            if success:
                send_telegram(
                    f"🛑 *COINDCX POSITION CLOSED (SL HIT)*\n\n"
                    f"🏷️ Strategy: `{strat_label}`\n"
                    f"🪙 Pair: `{coindcx_pair}`\n"
                    f"💵 Exit Price: `{curr_price:.2f}`\n"
                    f"📦 Sold Units: `{qty}`\n"
                    f"✅ Order Status: `Filled on CoinDCX`"
                )
                pos["side"] = None
            else:
                send_telegram(f"⚠️ *Exit Execution Failed on CoinDCX:* `{resp}`")

def check_strategy_for_symbol(strat_key, cfg, name, sym_cfg):
    try:
        ticker = sym_cfg["yf"]
        coindcx_pair = sym_cfg["coindcx"]
        qty = sym_cfg["qty"]

        htf_trend = get_trend(ticker, cfg["htf_period"], cfg["htf_interval"])
        mtf_trend = get_trend(ticker, cfg["mtf_period"], cfg["mtf_interval"])

        if htf_trend != mtf_trend or htf_trend == "NEUTRAL":
            return

        data = yf.download(ticker, period=cfg["entry_period"], interval=cfg["entry_interval"], progress=False)
        if data is None or len(data) < 50:
            return

        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        df['ema50'] = close_series.ewm(span=50, adjust=False).mean()
        df['ema93'] = close_series.ewm(span=93, adjust=False).mean()
        df['atr'], df['adx'] = calculate_atr_and_adx(df)

        df['swing_high'] = df['High'].iloc[-16:-1].max()
        df['swing_low'] = df['Low'].iloc[-16:-1].min()

        curr_price = float(df['Close'].iloc[-1])
        curr_open = float(df['Open'].iloc[-1])
        curr_ema50 = float(df['ema50'].iloc[-1])
        curr_ema93 = float(df['ema93'].iloc[-1])
        curr_adx = float(df['adx'].iloc[-1]) if not pd.isna(df['adx'].iloc[-1]) else 0.0
        curr_atr = float(df['atr'].iloc[-1]) if not pd.isna(df['atr'].iloc[-1]) else 0.0
        swing_h = float(df['swing_high'].iloc[-1])
        swing_l = float(df['swing_low'].iloc[-1])

        manage_trailing_sl(strat_key, cfg["label"], name, sym_cfg, curr_price)

        strong_trend = curr_adx > cfg["adx_min"]
        active_pos = active_positions[strat_key][name]["side"]
        tp_mults = cfg["tp_multipliers"]

        # BUY SETUP -> AUTO ORDER ON COINDCX
        if (active_pos is None) and (htf_trend == "BULLISH") and strong_trend and (curr_ema50 > curr_ema93) and (curr_price > swing_h) and (curr_price > curr_open):
            atr_sl = curr_price - (curr_atr * 1.5)
            sl = max(swing_l, atr_sl)
            risk = curr_price - sl

            if risk > 0:
                # Real order send to CoinDCX
                success, resp = place_coindcx_order(coindcx_pair, "buy", qty, curr_price)
                if success:
                    active_positions[strat_key][name] = {
                        "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr, "qty": qty
                    }
                    msg = (
                        f"🚀 *REAL TRADE EXECUTED (COINDCX)*\n\n"
                        f"🏷️ Strategy: `{cfg['label']}`\n"
                        f"🪙 Market: `{coindcx_pair}`\n"
                        f"💵 Entry Level: `{curr_price:.2f}`\n"
                        f"📦 Quantity: `{qty}`\n"
                        f"🛑 Trailing SL: `{sl:.2f}`\n"
                        f"🎯 TP 1: `{curr_price + (risk * tp_mults[0]):.2f}`\n"
                        f"🎯 TP 2: `{curr_price + (risk * tp_mults[1]):.2f}`\n\n"
                        f"⚡ Status: `Order Filled via API`"
                    )
                    send_telegram(msg)
                else:
                    print(f"CoinDCX Buy Rejected for {name}: {resp}")

    except Exception as e:
        print(f"Error on [{strat_key}] {name}: {e}")

threading.Thread(target=handle_incoming_users, daemon=True).start()

print("CoinDCX Auto Engine Active...")
send_telegram(
    "⚡ *CoinDCX Algorithmic Engine Live!*\n\n"
    "• Authenticated via HMAC-SHA256 API.\n"
    "• Live Auto Buy, Trailing Stop Loss & Exit Enabled.\n\n"
    "Scanning Pairs..."
)

while True:
    for name, sym_cfg in SYMBOLS.items():
        for strat_key, cfg in STRATEGIES.items():
            check_strategy_for_symbol(strat_key, cfg, name, sym_cfg)
            time.sleep(1)
    time.sleep(5)
