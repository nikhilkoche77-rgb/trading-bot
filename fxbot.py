import time
import hmac
import hashlib
import json
import os
import math
import threading
from datetime import datetime, timezone, timedelta
import requests
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

# --- STRICT RISK MANAGEMENT (Rs. 1,000 CAPITAL GUARD) ---
TRADE_INR_ALLOCATION = 110.0  # ₹110 per trade
MAX_PARALLEL_TRADES = 2       # Maximum 2 concurrent trades (~₹220 engaged)
DAILY_MAX_LOSS = 30.0         # ₹30 loss par din bhar ke liye trading band

STATE_FILE = "trades_state.json"
is_paused = False
daily_realized_pnl = 0.0

# 12 High-Liquidity Active Pairs on CoinDCX
SYMBOLS = {
    "SOL/INR": {"pair": "B-SOL_INR", "coindcx": "SOLINR", "step": 3},
    "XRP/INR": {"pair": "B-XRP_INR", "coindcx": "XRPINR", "step": 1},
    "DOGE/INR": {"pair": "B-DOGE_INR", "coindcx": "DOGEINR", "step": 0},
    "ADA/INR": {"pair": "B-ADA_INR", "coindcx": "ADAINR", "step": 1},
    "SHIB/INR": {"pair": "B-SHIB_INR", "coindcx": "SHIBINR", "step": 0},
    "PEPE/INR": {"pair": "B-PEPE_INR", "coindcx": "PEPEINR", "step": 0},
    "BONK/INR": {"pair": "B-BONK_INR", "coindcx": "BONKINR", "step": 0},
    "SUI/INR": {"pair": "B-SUI_INR", "coindcx": "SUIINR", "step": 1},
    "NEAR/INR": {"pair": "B-NEAR_INR", "coindcx": "NEARINR", "step": 2},
    "AVAX/INR": {"pair": "B-AVAX_INR", "coindcx": "AVAXINR", "step": 2},
    "RENDER/INR": {"pair": "B-RENDER_INR", "coindcx": "RENDERINR", "step": 2},
    "TRX/INR": {"pair": "B-TRX_INR", "coindcx": "TRXINR", "step": 0}
}

# --- PERSISTENT STATE LOGIC ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0} for name in SYMBOLS}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

# --- TELEGRAM CONTROLS & DYNAMIC SELL BUTTONS ---
def get_control_keyboard():
    keyboard = [
        [
            {"text": "📊 Live Status", "callback_data": "cmd_status"},
            {"text": "💰 INR Wallet", "callback_data": "cmd_balance"}
        ]
    ]

    # Dynamically generate a separate SELL button for each active trade
    active_sells = []
    for p_name, pos in active_positions.items():
        if pos["side"] is not None:
            coin_code = p_name.split("/")[0]
            active_sells.append({"text": f"🔴 Sell {coin_code}", "callback_data": f"sell_{p_name}"})

    if active_sells:
        keyboard.append(active_sells)

    keyboard.append([
        {"text": "⏸️ Pause Engine", "callback_data": "cmd_pause"},
        {"text": "▶️ Resume Engine", "callback_data": "cmd_resume"}
    ])
    keyboard.append([{"text": "🚨 Panic Exit (Close All)", "callback_data": "cmd_close_all"}])

    return {"inline_keyboard": keyboard}

def send_telegram(message, chat_id=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    recipients = [chat_id] if chat_id else [cid for cid in ADMIN_CHAT_IDS if cid != "SECOND_USER_CHAT_ID"]
    for cid in recipients:
        payload = {"chat_id": cid, "text": message}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            print(f"Telegram error: {e}")

def answer_callback_query(callback_query_id, text=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
    try:
        requests.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=5)
    except Exception:
        pass

# --- COINDCX API EXECUTION ---
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
    success, resp = coindcx_auth_post("/exchange/v1/orders/create", body)
    if success and "orders" in resp:
        order_id = resp["orders"][0].get("id")
        time.sleep(1.5)
        ok, detail = coindcx_auth_post("/exchange/v1/orders/status", {"id": order_id})
        if ok and detail.get("status") in ["filled", "open"]:
            return True, resp
    return success, resp

def emergency_close_all():
    closed_count = 0
    for p_name, pos in active_positions.items():
        if pos["side"] is not None:
            pair_code = SYMBOLS[p_name]["coindcx"]
            place_coindcx_order(pair_code, "sell", pos["qty"])
            pos["side"] = None
            closed_count += 1
    save_state(active_positions)
    return closed_count

# --- DIRECT ZERO-LATENCY MARKET DATA ---
def fetch_coindcx_candles(pair_name, interval="1m", limit=40):
    try:
        url = f"https://public.coindcx.com/market_data/candles?pair={pair_name}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            raw = r.json()
            if isinstance(raw, list) and len(raw) >= 20:
                df = pd.DataFrame(raw)
                df = df.iloc[::-1].reset_index(drop=True)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    df[col] = df[col].astype(float)
                return df
    except Exception:
        pass
    return None

def calculate_technical_indicators(df, length=14):
    high, low, close = df['high'], df['low'], df['close']
    tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(length).mean()

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(length).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(length).mean() / atr)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(length).mean()
    return atr, adx

def generate_status_text(user_name="Trader"):
    pos_summary = ""
    has_active = False
    for p_name, pos in active_positions.items():
        if pos["side"] is not None:
            has_active = True
            pnl_pct = ((pos["best_price"] - pos["entry"]) / pos["entry"]) * 100 if pos["entry"] > 0 else 0.0
            pos_summary += (
                f"\n🪙 {p_name} ({pos['qty']} units)\n"
                f"Entry: ₹{pos['entry']:.4f} | Cur: ₹{pos['best_price']:.4f}\n"
                f"SL: ₹{pos['sl']:.4f} | Trailing: {pnl_pct:+.2f}%\n"
            )
    if not has_active:
        pos_summary = "\n💤 No active trades right now."

    active_count = sum(1 for p in active_positions.values() if p["side"] is not None)
    return (
        f"👑 CoinDCX Pro Deck ({user_name})\n"
        f"Status: {'⏸️ PAUSED' if is_paused else '🟢 ACTIVE'}\n"
        f"Open Trades: {active_count}/{MAX_PARALLEL_TRADES}\n"
        f"Daily Realized PnL: ₹{daily_realized_pnl:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"CURRENT POSITIONS:{pos_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Touch any button below to manage:"
    )

# --- TRADE EXECUTION & TRAILING ENGINE ---
def manage_trailing_sl(name, sym_cfg, curr_price):
    global daily_realized_pnl
    pos = active_positions[name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]

    # Breakeven Protection
    if curr_price >= (pos["entry"] + pos["atr"]) and pos["sl"] < pos["entry"]:
        pos["sl"] = pos["entry"]
        save_state(active_positions)
        send_telegram(f"🛡️ BREAKEVEN ACTIVATED\nPair: {name}\nSL moved to Entry: ₹{pos['entry']:.4f}")

    # Trailing Stop Loss
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
                f"🛑 AUTO-EXIT (SL/Target Hit)\n\n"
                f"Pair: {coindcx_pair}\n"
                f"Exit Price: ₹{curr_price:.4f}\n"
                f"Units: {qty}\n"
                f"Realized PnL: ₹{trade_pnl:.2f}\n"
                f"Amount credited to CoinDCX Wallet.",
                reply_markup=get_control_keyboard()
            )
            pos["side"] = None
            save_state(active_positions)

def scan_symbol(name, sym_cfg):
    global is_paused
    active_count = sum(1 for p in active_positions.values() if p["side"] is not None)
    if is_paused or active_count >= MAX_PARALLEL_TRADES or daily_realized_pnl <= -DAILY_MAX_LOSS:
        return

    pair_code = sym_cfg["pair"]
    coindcx_pair = sym_cfg["coindcx"]
    precision = sym_cfg["step"]

    df = fetch_coindcx_candles(pair_code, interval="1m", limit=35)
    if df is None or len(df) < 25:
        return

    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['atr'], df['adx'] = calculate_technical_indicators(df)
    df['swing_high'] = df['high'].iloc[-10:-1].max()
    df['swing_low'] = df['low'].iloc[-10:-1].min()

    curr_price = float(df['close'].iloc[-1])
    curr_open = float(df['open'].iloc[-1])
    manage_trailing_sl(name, sym_cfg, curr_price)

    trend_strong = float(df['adx'].iloc[-1]) > 18.0
    ema_bullish = float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1])
    breakout = (curr_price > float(df['swing_high'].iloc[-1])) and (curr_price > curr_open)

    if active_positions[name]["side"] is None and trend_strong and ema_bullish and breakout:
        atr_val = float(df['atr'].iloc[-1])
        sl = max(float(df['swing_low'].iloc[-1]), curr_price - (atr_val * 1.5))
        raw_qty = TRADE_INR_ALLOCATION / curr_price
        qty = round(raw_qty, precision) if precision > 0 else math.floor(raw_qty)

        if qty > 0:
            success, _ = place_coindcx_order(coindcx_pair, "buy", qty)
            if success:
                active_positions[name] = {
                    "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": atr_val, "qty": qty
                }
                save_state(active_positions)
                send_telegram(
                    f"🚀 PRO BUY EXECUTED\n\n"
                    f"Pair: {coindcx_pair}\n"
                    f"Price: ₹{curr_price:.4f}\n"
                    f"Qty: {qty} (~₹{TRADE_INR_ALLOCATION:.0f})\n"
                    f"Initial SL: ₹{sl:.4f}",
                    reply_markup=get_control_keyboard()
                )

# --- BACKGROUND AUTOMATION THREADS ---
def midnight_reset_scheduler():
    global daily_realized_pnl
    ist = timezone(timedelta(hours=5, minutes=30))
    while True:
        now = datetime.now(ist)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((next_run - now).total_seconds())

        report = f"🌙 MIDNIGHT REPORT (IST)\nTotal Realized PnL: ₹{daily_realized_pnl:.2f}\nDaily drawdown lock reset to ₹0.00."
        send_telegram(report)
        daily_realized_pnl = 0.0

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

                    if "callback_query" in update:
                        cb = update["callback_query"]
                        sender_id = str(cb["from"]["id"])
                        cb_data = cb.get("data", "")
                        u_name = cb["from"].get("first_name", "Trader")

                        if sender_id in ADMIN_CHAT_IDS:
                            if cb_data == "cmd_status":
                                answer_callback_query(cb["id"], "Status Updated")
                                send_telegram(generate_status_text(u_name), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_balance":
                                answer_callback_query(cb["id"], "Wallet Checked")
                                bal = get_coindcx_balance()
                                send_telegram(f"💰 CoinDCX Live Wallet: ₹{bal:.2f} INR", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_pause":
                                is_paused = True
                                answer_callback_query(cb["id"], "Paused")
                                send_telegram("⏸️ Bot Paused. New entries locked.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_resume":
                                is_paused = False
                                answer_callback_query(cb["id"], "Resumed")
                                send_telegram("▶️ Bot Active. Scanning high-volatility pairs.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_close_all":
                                answer_callback_query(cb["id"], "Exiting All")
                                count = emergency_close_all()
                                send_telegram(f"🚨 Panic Close: Exited {count} positions at market price.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data.startswith("sell_"):
                                target_pair = cb_data.replace("sell_", "")
                                if target_pair in active_positions and active_positions[target_pair]["side"] is not None:
                                    pos = active_positions[target_pair]
                                    pair_code = SYMBOLS[target_pair]["coindcx"]
                                    success, _ = place_coindcx_order(pair_code, "sell", pos["qty"])
                                    if success:
                                        recvd_inr = pos["best_price"] * pos["qty"]
                                        answer_callback_query(cb["id"], f"Sold {target_pair}!")
                                        send_telegram(
                                            f"✅ MANUAL SELL EXECUTED\n\n"
                                            f"🪙 Pair: {target_pair}\n"
                                            f"📦 Units Sold: {pos['qty']}\n"
                                            f"💵 Est. Return: ₹{recvd_inr:.2f} credited to CoinDCX INR Wallet.",
                                            chat_id=sender_id,
                                            reply_markup=get_control_keyboard()
                                        )
                                        pos["side"] = None
                                        save_state(active_positions)
                                    else:
                                        answer_callback_query(cb["id"], "Sell Failed!")
                                else:
                                    answer_callback_query(cb["id"], "Position already closed!")

                    elif "message" in update and "text" in update["message"]:
                        sender_id = str(update["message"]["chat"]["id"])
                        u_name = update["message"]["from"].get("first_name", "Trader")
                        if sender_id in ADMIN_CHAT_IDS:
                            send_telegram(generate_status_text(u_name), chat_id=sender_id, reply_markup=get_control_keyboard())
        except Exception as e:
            print(f"Telegram loop error: {e}")
        time.sleep(2)

# Start workers
threading.Thread(target=handle_incoming_users, daemon=True).start()
threading.Thread(target=midnight_reset_scheduler, daemon=True).start()

print("Master CoinDCX Institutional Engine Online...")
send_telegram("🔥 Master CoinDCX Institutional Engine Armed!\nInteractive Sell Buttons & Risk Guard Active.", reply_markup=get_control_keyboard())

# Main Scan Cycle
while True:
    for name, sym_cfg in SYMBOLS.items():
        scan_symbol(name, sym_cfg)
        time.sleep(0.5)
    time.sleep(2)
