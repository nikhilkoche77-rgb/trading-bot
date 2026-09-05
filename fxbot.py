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
    "1345385952",           # Admin 1
    "SECOND_USER_CHAT_ID"   # Admin 2
]

# --- COINDCX API CREDENTIALS ---
COINDCX_KEY = os.getenv("COINDCX_API_KEY", "YOUR_COINDCX_KEY_HERE")
COINDCX_SECRET = os.getenv("COINDCX_SECRET_KEY", "YOUR_COINDCX_SECRET_HERE")

# --- STRICT RISK MANAGEMENT (Rs. 1,000 CAPITAL) ---
TRADE_INR_ALLOCATION = 110.0  
MAX_PARALLEL_TRADES = 2       # Max 2 active positions (~Rs. 220 total)
DAILY_MAX_LOSS = 30.0         # Rs. 30 loss par din bhar ke liye trading band

STATE_FILE = "trades_state.json"
is_paused = False
daily_realized_pnl = 0.0

# 12 Liquid & High-Volatility Pairs
SYMBOLS = {
    "SOL/INR": {"yf": "SOL-USD", "coindcx": "SOLINR", "step": 3},
    "XRP/INR": {"yf": "XRP-USD", "coindcx": "XRPINR", "step": 1},
    "DOGE/INR": {"yf": "DOGE-USD", "coindcx": "DOGEINR", "step": 0},
    "ADA/INR": {"yf": "ADA-USD", "coindcx": "ADAINR", "step": 1},
    "SHIB/INR": {"yf": "SHIB-USD", "coindcx": "SHIBINR", "step": 0},
    "PEPE/INR": {"yf": "PEPE24478-USD", "coindcx": "PEPEINR", "step": 0},
    "BONK/INR": {"yf": "BONK-USD", "coindcx": "BONKINR", "step": 0},
    "SUI/INR": {"yf": "SUI20947-USD", "coindcx": "SUIINR", "step": 1},
    "NEAR/INR": {"yf": "NEAR-USD", "coindcx": "NEARINR", "step": 2},
    "AVAX/INR": {"yf": "AVAX-USD", "coindcx": "AVAXINR", "step": 2},
    "RENDER/INR": {"yf": "RENDER-USD", "coindcx": "RENDERINR", "step": 2},
    "TRX/INR": {"yf": "TRX-USD", "coindcx": "TRXINR", "step": 0}
}

STRATEGIES = {
    "VOLATILITY_SCALP": {
        "label": "⚡ [PRO SCALP - 15M/1M]",
        "htf_interval": "15m",
        "htf_period": "5d",
        "entry_interval": "1m",
        "entry_period": "1d",
        "adx_min": 19.0,
        "tp_multipliers": [1.5, 2.5]
    }
}

# --- PERSISTENT STATE MANAGEMENT ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        strat: {name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0} for name in SYMBOLS}
        for strat in STRATEGIES
    }

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

def send_telegram(message, chat_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    recipients = [chat_id] if chat_id else [cid for cid in ADMIN_CHAT_IDS if cid != "SECOND_USER_CHAT_ID"]
    for cid in recipients:
        try:
            requests.post(url, json={"chat_id": cid, "text": message}, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")

def coindcx_auth_post(endpoint, body):
    timeStamp = int(round(time.time() * 1000))
    body["timestamp"] = timeStamp
    json_payload = json.dumps(body, separators=(',', ':'))
    signature = hmac.new(COINDCX_SECRET.encode('utf-8'), json_payload.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {'Content-Type': 'application/json', 'X-AUTH-APIKEY': COINDCX_KEY, 'X-AUTH-SIGNATURE': signature}
    try:
        res = requests.post(f"https://api.coindcx.com{endpoint}", data=json_payload, headers=headers, timeout=10)
        return res.status_code == 200, res.json()
    except Exception as e:
        return False, str(e)

def get_coindcx_balance():
    success, data = coindcx_auth_post("/exchange/v1/users/balances", {})
    if success and isinstance(data, list):
        for item in data:
            if item.get("currency") == "INR":
                return float(item.get("balance", 0.0))
    return 0.0

def place_coindcx_order(market_pair, side, quantity):
    body = {
        "side": side.lower(),
        "order_type": "market_order",
        "market": market_pair,
        "total_quantity": quantity
    }
    return coindcx_auth_post("/exchange/v1/orders/create", body)

def count_open_positions():
    return sum(1 for s in active_positions.values() for p in s.values() if p["side"] is not None)

def emergency_close_all():
    closed_count = 0
    for s_key, pairs in active_positions.items():
        for p_name, pos in pairs.items():
            if pos["side"] is not None:
                pair_code = SYMBOLS[p_name]["coindcx"]
                place_coindcx_order(pair_code, "sell", pos["qty"])
                pos["side"] = None
                closed_count += 1
    save_state(active_positions)
    return closed_count

def calculate_technical_indicators(df, length=14):
    high = pd.Series(np.array(df['High']).flatten(), index=df.index)
    low = pd.Series(np.array(df['Low']).flatten(), index=df.index)
    close = pd.Series(np.array(df['Close']).flatten(), index=df.index)

    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
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

def get_macro_trend(ticker_symbol, period, interval):
    try:
        df = yf.download(ticker_symbol, period=period, interval=interval, progress=False)
        if df is None or len(df) < 25:
            return "NEUTRAL"
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        ema30 = close_series.ewm(span=30, adjust=False).mean()
        return "BULLISH" if float(close_series.iloc[-1]) > float(ema30.iloc[-1]) else "BEARISH"
    except Exception:
        return "NEUTRAL"

def manage_trailing_sl(strat_key, strat_label, name, sym_cfg, curr_price):
    global daily_realized_pnl
    pos = active_positions[strat_key][name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]

    if pos["side"] == "BUY":
        # Breakeven Protection
        if curr_price >= (pos["entry"] + pos["atr"]) and pos["sl"] < pos["entry"]:
            pos["sl"] = pos["entry"]
            save_state(active_positions)
            send_telegram(f"🛡️ BREAKEVEN ACTIVATED\nPair: {name}\nSL moved to: {pos['entry']:.4f}")

        # Trailing SL Lock
        if curr_price > pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price - trailing_gap
            if new_sl > pos["sl"]:
                pos["sl"] = new_sl
                save_state(active_positions)
        elif curr_price <= pos["sl"]:
            success, _ = place_coindcx_order(coindcx_pair, "sell", qty)
            if success:
                trade_pnl = (curr_price - pos["entry"]) * qty
                daily_realized_pnl += trade_pnl
                send_telegram(
                    f"🛑 TRADE EXITED\n"
                    f"Pair: {coindcx_pair}\n"
                    f"Exit: {curr_price:.4f}\n"
                    f"Qty: {qty}\n"
                    f"Realized PnL: ₹{trade_pnl:.2f}"
                )
                pos["side"] = None
                save_state(active_positions)

def check_strategy_for_symbol(strat_key, cfg, name, sym_cfg):
    global is_paused
    if is_paused or count_open_positions() >= MAX_PARALLEL_TRADES or daily_realized_pnl <= -DAILY_MAX_LOSS:
        return

    ticker = sym_cfg["yf"]
    coindcx_pair = sym_cfg["coindcx"]
    precision = sym_cfg["step"]

    if get_macro_trend(ticker, cfg["htf_period"], cfg["htf_interval"]) != "BULLISH":
        return

    try:
        df = yf.download(ticker, period=cfg["entry_period"], interval=cfg["entry_interval"], progress=False)
        if df is None or len(df) < 30:
            return
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        df['ema20'] = close_series.ewm(span=20, adjust=False).mean()
        df['ema50'] = close_series.ewm(span=50, adjust=False).mean()
        df['atr'], df['adx'] = calculate_technical_indicators(df)
        df['swing_high'] = df['High'].iloc[-10:-1].max()
        df['swing_low'] = df['Low'].iloc[-10:-1].min()

        curr_price = float(df['Close'].iloc[-1])
        curr_open = float(df['Open'].iloc[-1])
        manage_trailing_sl(strat_key, cfg["label"], name, sym_cfg, curr_price)

        trend_strong = float(df['adx'].iloc[-1]) > cfg["adx_min"]
        ema_aligned = float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1])
        breakout = (curr_price > float(df['swing_high'].iloc[-1])) and (curr_price > curr_open)

        if active_positions[strat_key][name]["side"] is None and trend_strong and ema_aligned and breakout:
            atr_val = float(df['atr'].iloc[-1])
            sl = max(float(df['swing_low'].iloc[-1]), curr_price - (atr_val * 1.5))
            inr_price = curr_price * 90.0 if "USD" in ticker else curr_price
            raw_qty = TRADE_INR_ALLOCATION / inr_price
            qty = round(raw_qty, precision) if precision > 0 else math.floor(raw_qty)

            if qty > 0:
                success, _ = place_coindcx_order(coindcx_pair, "buy", qty)
                if success:
                    active_positions[strat_key][name] = {
                        "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": atr_val, "qty": qty
                    }
                    save_state(active_positions)
                    send_telegram(
                        f"🚀 PRO BUY ORDER EXECUTED\n\n"
                        f"Pair: {coindcx_pair}\n"
                        f"Price: {curr_price:.4f}\n"
                        f"Qty: {qty} (~₹{TRADE_INR_ALLOCATION:.0f})\n"
                        f"Initial SL: {sl:.4f}"
                    )
    except Exception as e:
        print(f"Check error on {name}: {e}")

# --- INTERACTIVE TELEGRAM HANDLER ---
def handle_incoming_users():
    global is_paused
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            resp = requests.get(url, params={"offset": last_update_id + 1, "timeout": 20}, timeout=25).json()

            if "result" in resp:
                for update in resp["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        sender_id = str(update["message"]["chat"]["id"])
                        user_text = update["message"]["text"].strip().lower()

                        if sender_id in ADMIN_CHAT_IDS:
                            if user_text in ["/start", "status"]:
                                pos_summary = ""
                                has_active = False
                                for s_key, pairs in active_positions.items():
                                    for p_name, pos in pairs.items():
                                        if pos["side"] is not None:
                                            has_active = True
                                            pnl_pct = ((pos["best_price"] - pos["entry"]) / pos["entry"]) * 100 if pos["entry"] > 0 else 0.0
                                            pos_summary += (
                                                f"\n🪙 {p_name} ({pos['qty']} units)\n"
                                                f"Entry: {pos['entry']:.4f} | Cur: {pos['best_price']:.4f}\n"
                                                f"SL: {pos['sl']:.4f} | Trailing: {pnl_pct:+.2f}%\n"
                                            )
                                if not has_active:
                                    pos_summary = "\n💤 No active trades right now."

                                reply = (
                                    f"👑 CoinDCX Pro Engine\n"
                                    f"Status: {'PAUSED' if is_paused else 'ACTIVE'}\n"
                                    f"Daily Realized PnL: ₹{daily_realized_pnl:.2f}\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"CURRENT POSITIONS:{pos_summary}\n"
                                    f"━━━━━━━━━━━━━━━━━━━\n"
                                    f"Commands: /balance, /close_all, /pause, /resume"
                                )
                                send_telegram(reply, chat_id=sender_id)

                            elif user_text == "/balance":
                                bal = get_coindcx_balance()
                                send_telegram(f"💰 CoinDCX Live Wallet: ₹{bal:.2f} INR", chat_id=sender_id)

                            elif user_text == "/close_all":
                                count = emergency_close_all()
                                send_telegram(f"🚨 EMERGENCY: Closed {count} open positions at market price.", chat_id=sender_id)

                            elif user_text == "/pause":
                                is_paused = True
                                send_telegram("⏸️ Engine PAUSED. No new trades will open.", chat_id=sender_id)

                            elif user_text == "/resume":
                                is_paused = False
                                send_telegram("▶️ Engine RESUMED. Scanning active.", chat_id=sender_id)
        except Exception as e:
            print(f"Listener error: {e}")
        time.sleep(2)

threading.Thread(target=handle_incoming_users, daemon=True).start()

print("CoinDCX Pro Engine Live...")
send_telegram("🔥 CoinDCX Pro Engine Armed!\nPersistent State & Commands Enabled.")

while True:
    for name, sym_cfg in SYMBOLS.items():
        for strat_key, cfg in STRATEGIES.items():
            check_strategy_for_symbol(strat_key, cfg, name, sym_cfg)
            time.sleep(1)
    time.sleep(3)
