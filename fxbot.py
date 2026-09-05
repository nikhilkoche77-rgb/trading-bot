import time
import hmac
import hashlib
import json
import os
import math
import threading
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
ADMIN_CHAT_IDS = [
    "1345385952",           # Admin 1 (Aap)
    "SECOND_USER_CHAT_ID"   # Admin 2 (Dusre user ki ID)
]

# --- COINDCX API CREDENTIALS ---
COINDCX_KEY = os.getenv("COINDCX_API_KEY", "YOUR_COINDCX_KEY_HERE")
COINDCX_SECRET = os.getenv("COINDCX_SECRET_KEY", "YOUR_COINDCX_SECRET_HERE")

# --- MICRO-CAPITAL RISK MANAGEMENT (Rs. 100-110 TRADES) ---
# Rs. 110 allocation taaki fee/TDS ke baad CoinDCX ka Rs. 100 minimum rule violate na ho
TRADE_INR_ALLOCATION = 110.0  
MAX_PARALLEL_TRADES = 2       # Ek waqt me max 2 trades (~Rs. 220 total engaged)

# Low-unit price coins best suited for Rs. 100 orders
SYMBOLS = {
    "XRP/INR": {"yf": "XRP-USD", "coindcx": "XRPINR", "step": 1},
    "ADA/INR": {"yf": "ADA-USD", "coindcx": "ADAINR", "step": 1},
    "DOGE/INR": {"yf": "DOGE-USD", "coindcx": "DOGEINR", "step": 0}
}

STRATEGIES = {
    "SCALP_MOMENTUM": {
        "label": "⚡ [MICRO SCALP - 15M/5M]",
        "htf_interval": "15m",
        "htf_period": "5d",
        "entry_interval": "5m",
        "entry_period": "5d",
        "adx_min": 24.0,
        "tp_multipliers": [1.5, 2.5]
    },
    "INTRADAY_TREND": {
        "label": "⏱️ [MICRO INTRADAY - 1D/15M]",
        "htf_interval": "1d",
        "htf_period": "60d",
        "entry_interval": "15m",
        "entry_period": "10d",
        "adx_min": 22.0,
        "tp_multipliers": [2.0, 3.5]
    }
}

active_positions = {
    strat_key: {
        name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0}
        for name in SYMBOLS
    }
    for strat_key in STRATEGIES
}

def send_telegram(message, chat_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    recipients = [chat_id] if chat_id else [cid for cid in ADMIN_CHAT_IDS if cid != "SECOND_USER_CHAT_ID"]
    
    for cid in recipients:
        payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram alert error: {e}")

def place_coindcx_order(market_pair, side, quantity):
    url = "https://api.coindcx.com/exchange/v1/orders/create"
    timeStamp = int(round(time.time() * 1000))
    
    body = {
        "side": side.lower(),
        "order_type": "market_order",
        "market": market_pair,
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

def count_open_positions():
    count = 0
    for strat in active_positions.values():
        for pos in strat.values():
            if pos["side"] is not None:
                count += 1
    return count

def calculate_technical_indicators(df, length=14):
    high = pd.Series(np.array(df['High']).flatten(), index=df.index)
    low = pd.Series(np.array(df['Low']).flatten(), index=df.index)
    close = pd.Series(np.array(df['Close']).flatten(), index=df.index)
    volume = pd.Series(np.array(df['Volume']).flatten(), index=df.index)

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
    vol_ma = volume.rolling(20).mean()

    return atr, adx, vol_ma

def get_macro_trend(ticker_symbol, period, interval):
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

def manage_trailing_sl(strat_key, strat_label, name, sym_cfg, curr_price):
    pos = active_positions[strat_key][name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]

    if pos["side"] == "BUY":
        # Breakeven Lock: Jab 1x ATR upar ho jaye, SL ko entry price par le aao
        if curr_price >= (pos["entry"] + pos["atr"]) and pos["sl"] < pos["entry"]:
            pos["sl"] = pos["entry"]
            send_telegram(
                f"🛡️ *BREAKEVEN SL LOCKED (NO RISK)*\n"
                f"🏷️ `{strat_label}` | `{name}`\n"
                f"💵 Stop Loss moved to Entry: `{pos['entry']:.2f}`"
            )

        if curr_price > pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price - trailing_gap
            if new_sl > pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(
                    f"📈 *TRAILING SL RAISED*\n"
                    f"🏷️ `{strat_label}` | `{name}`\n"
                    f"🛑 New SL: `{pos['sl']:.2f}`"
                )
        elif curr_price <= pos["sl"]:
            success, resp = place_coindcx_order(coindcx_pair, "sell", qty)
            if success:
                send_telegram(
                    f"🛑 *COINDCX ORDER EXITED (SL/TARGET)*\n\n"
                    f"🏷️ Strategy: `{strat_label}`\n"
                    f"🪙 Pair: `{coindcx_pair}`\n"
                    f"💵 Exit Price: `{curr_price:.2f}`\n"
                    f"📦 Sold Units: `{qty}`\n"
                    f"✅ CoinDCX Status: `Filled`"
                )
                pos["side"] = None
            else:
                send_telegram(f"⚠️ *Exit Order Failed on CoinDCX:* `{resp}`")

def check_strategy_for_symbol(strat_key, cfg, name, sym_cfg):
    try:
        # Max open position cap
        if count_open_positions() >= MAX_PARALLEL_TRADES:
            return

        ticker = sym_cfg["yf"]
        coindcx_pair = sym_cfg["coindcx"]
        precision = sym_cfg["step"]

        htf_trend = get_macro_trend(ticker, cfg["htf_period"], cfg["htf_interval"])
        if htf_trend != "BULLISH":
            return

        data = yf.download(ticker, period=cfg["entry_period"], interval=cfg["entry_interval"], progress=False)
        if data is None or len(data) < 50:
            return

        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        df['ema50'] = close_series.ewm(span=50, adjust=False).mean()
        df['ema93'] = close_series.ewm(span=93, adjust=False).mean()
        df['atr'], df['adx'], df['vol_ma'] = calculate_technical_indicators(df)

        df['swing_high'] = df['High'].iloc[-16:-1].max()
        df['swing_low'] = df['Low'].iloc[-16:-1].min()

        curr_price = float(df['Close'].iloc[-1])
        curr_open = float(df['Open'].iloc[-1])
        curr_vol = float(df['Volume'].iloc[-1])
        vol_avg = float(df['vol_ma'].iloc[-1]) if not pd.isna(df['vol_ma'].iloc[-1]) else 0.0
        curr_ema50 = float(df['ema50'].iloc[-1])
        curr_ema93 = float(df['ema93'].iloc[-1])
        curr_adx = float(df['adx'].iloc[-1]) if not pd.isna(df['adx'].iloc[-1]) else 0.0
        curr_atr = float(df['atr'].iloc[-1]) if not pd.isna(df['atr'].iloc[-1]) else 0.0
        swing_h = float(df['swing_high'].iloc[-1])
        swing_l = float(df['swing_low'].iloc[-1])

        manage_trailing_sl(strat_key, cfg["label"], name, sym_cfg, curr_price)

        trend_strong = curr_adx > cfg["adx_min"]
        volume_surge = curr_vol > vol_avg
        ema_aligned = curr_ema50 > curr_ema93
        breakout_confirmed = (curr_price > swing_h) and (curr_price > curr_open)

        active_pos = active_positions[strat_key][name]["side"]
        tp_mults = cfg["tp_multipliers"]

        # BUY SETUP
        if (active_pos is None) and trend_strong and volume_surge and ema_aligned and breakout_confirmed:
            atr_sl = curr_price - (curr_atr * 1.5)
            sl = max(swing_l, atr_sl)
            risk = curr_price - sl

            inr_price = curr_price * 90.0 if "USD" in ticker else curr_price
            raw_qty = TRADE_INR_ALLOCATION / inr_price
            qty = round(raw_qty, precision) if precision > 0 else math.floor(raw_qty)

            if qty > 0 and risk > 0:
                success, resp = place_coindcx_order(coindcx_pair, "buy", qty)
                if success:
                    active_positions[strat_key][name] = {
                        "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr, "qty": qty
                    }
                    msg = (
                        f"🎯 *₹100 MICRO-TRADE EXECUTED (COINDCX)*\n\n"
                        f"🏷️ Strategy: `{cfg['label']}`\n"
                        f"🪙 Pair: `{coindcx_pair}`\n"
                        f"💵 Approx INR Entry: `₹{inr_price:.2f}`\n"
                        f"📦 Quantity: `{qty}` (~₹{TRADE_INR_ALLOCATION:.0f})\n"
                        f"🛑 Initial SL: `{sl:.2f}`\n"
                        f"🎯 Target 1: `{curr_price + (risk * tp_mults[0]):.2f}`\n"
                        f"🎯 Target 2: `{curr_price + (risk * tp_mults[1]):.2f}`\n\n"
                        f"⚡ Status: `Active with Breakeven & Trailing SL`"
                    )
                    send_telegram(msg)
                else:
                    print(f"Order rejected on CoinDCX for {name}: {resp}")

    except Exception as e:
        print(f"Strategy error on [{strat_key}] {name}: {e}")

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
                                f"👑 *CoinDCX ₹100 Engine ({user_name})*\n\n"
                                f"💰 Per Trade Size: `₹{TRADE_INR_ALLOCATION:.0f} INR`\n"
                                f"🛡️ Max Concurrent Trades: `2 (Total ₹220 engaged)`\n"
                                f"🪙 Active Pairs: `XRP, ADA, DOGE`\n"
                                f"🎯 System: `Breakeven Lock + Trailing SL Active`\n"
                                f"🟢 Status: `Scanning for High-Probability Setups`",
                                chat_id=sender_id
                            )
                        else:
                            send_telegram(f"Hello {user_name}! 🔒 Private bot.", chat_id=sender_id)
        except Exception as e:
            print(f"Listener error: {e}")
        time.sleep(2)

threading.Thread(target=handle_incoming_users, daemon=True).start()

print("CoinDCX ₹100 Micro-Trading Engine Online...")
send_telegram(
    "⚡ *CoinDCX ₹100 Micro-Capital Engine Live!*\n\n"
    f"📦 Trade Allocation: `₹{TRADE_INR_ALLOCATION:.0f} per trade`\n"
    "🛡️ Risk: Max 2 positions parallel with Breakeven stop lock.\n"
    "🪙 Monitored Coins: `XRP/INR, ADA/INR, DOGE/INR`"
)

while True:
    for name, sym_cfg in SYMBOLS.items():
        for strat_key, cfg in STRATEGIES.items():
            check_strategy_for_symbol(strat_key, cfg, name, sym_cfg)
            time.sleep(1)
    time.sleep(5)
