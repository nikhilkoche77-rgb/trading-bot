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

# --- RISK & COMPOUND ENGINE PARAMETERS ---
MAX_PARALLEL_TRADES = 2        # Maximum 2 concurrent trades across all assets
DAILY_MAX_LOSS_INR = 30.0      # ₹30 hard daily loss kill-switch
COOL_OFF_MINUTES = 20          # 20-minute anti-whipsaw delay after SL hit
MAX_SPREAD_TOLERANCE_PCT = 0.45 # Skip order if spread exceeds 0.45%
MIN_ORDER_BOOK_IMBALANCE = 1.40 # Buyers must be 1.4x of sellers in top book

# Dynamic Sizing Limits
MIN_TRADE_INR = 110.0
MAX_TRADE_INR = 300.0
MIN_TRADE_USDT = 1.30
MAX_TRADE_USDT = 3.50
ALLOCATION_PCT = 0.10          # 10% of total wallet balance per trade

STATE_FILE = "trades_state.json"
is_paused = False
daily_realized_pnl_inr = 0.0
cool_off_tracker = {}

# Performance Tracker Stats
daily_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "best_trade_pnl": 0.0,
    "worst_trade_pnl": 0.0
}

# --- UNIFIED MULTI-ASSET PAIRS (CRYPTO + GOLD + FOREX SYNTHETICS) ---
SYMBOLS = {
    # === COMMODITIES & FOREX SYNTHETICS ===
    "XAU/USDT": {"base_curr": "USDT", "binance": "PAXGUSDT", "pair": "B-PAXG_USDT", "coindcx": "PAXGUSDT", "step": 4},
    "EUR/USDT": {"base_curr": "USDT", "binance": "EURUSDT", "pair": "B-EUR_USDT", "coindcx": "EURUSDT", "step": 4},
    "USDT/INR": {"base_curr": "INR", "binance": "USDCUSDT", "pair": "B-USDT_INR", "coindcx": "USDTINR", "step": 2},
    "GOLD/INR": {"base_curr": "INR", "binance": "PAXGUSDT", "pair": "B-PAXG_INR", "coindcx": "PAXGINR", "step": 4},

    # === GLOBAL USDT / USD CRYPTO PAIRS ===
    "BTC/USDT": {"base_curr": "USDT", "binance": "BTCUSDT", "pair": "B-BTC_USDT", "coindcx": "BTCUSDT", "step": 5},
    "ETH/USDT": {"base_curr": "USDT", "binance": "ETHUSDT", "pair": "B-ETH_USDT", "coindcx": "ETHUSDT", "step": 4},
    "SOL/USDT": {"base_curr": "USDT", "binance": "SOLUSDT", "pair": "B-SOL_USDT", "coindcx": "SOLUSDT", "step": 3},
    "XRP/USDT": {"base_curr": "USDT", "binance": "XRPUSDT", "pair": "B-XRP_USDT", "coindcx": "XRPUSDT", "step": 1},
    "DOGE/USDT": {"base_curr": "USDT", "binance": "DOGEUSDT", "pair": "B-DOGE_USDT", "coindcx": "DOGEUSDT", "step": 0},
    "PEPE/USDT": {"base_curr": "USDT", "binance": "PEPEUSDT", "pair": "B-PEPE_USDT", "coindcx": "PEPEUSDT", "step": 0},
    "SHIB/USDT": {"base_curr": "USDT", "binance": "SHIBUSDT", "pair": "B-SHIB_USDT", "coindcx": "SHIBUSDT", "step": 0},
    "SUI/USDT": {"base_curr": "USDT", "binance": "SUIUSDT", "pair": "B-SUI_USDT", "coindcx": "SUIUSDT", "step": 1},
    "NEAR/USDT": {"base_curr": "USDT", "binance": "NEARUSDT", "pair": "B-NEAR_USDT", "coindcx": "NEARUSDT", "step": 2},
    "AVAX/USDT": {"base_curr": "USDT", "binance": "AVAXUSDT", "pair": "B-AVAX_USDT", "coindcx": "AVAXUSDT", "step": 2},
    "WIF/USDT": {"base_curr": "USDT", "binance": "WIFUSDT", "pair": "B-WIF_USDT", "coindcx": "WIFUSDT", "step": 2},
    "LINK/USDT": {"base_curr": "USDT", "binance": "LINKUSDT", "pair": "B-LINK_USDT", "coindcx": "LINKUSDT", "step": 2},
    "FTM/USDT": {"base_curr": "USDT", "binance": "FTMUSDT", "pair": "B-FTM_USDT", "coindcx": "FTMUSDT", "step": 1},

    # === HIGH-LIQUIDITY DIRECT INR CRYPTO PAIRS ===
    "BTC/INR": {"base_curr": "INR", "binance": "BTCUSDT", "pair": "B-BTC_INR", "coindcx": "BTCINR", "step": 5},
    "ETH/INR": {"base_curr": "INR", "binance": "ETHUSDT", "pair": "B-ETH_INR", "coindcx": "ETHINR", "step": 4},
    "SOL/INR": {"base_curr": "INR", "binance": "SOLUSDT", "pair": "B-SOL_INR", "coindcx": "SOLINR", "step": 3},
    "XRP/INR": {"base_curr": "INR", "binance": "XRPUSDT", "pair": "B-XRP_INR", "coindcx": "XRPINR", "step": 1},
    "DOGE/INR": {"base_curr": "INR", "binance": "DOGEUSDT", "pair": "B-DOGE_INR", "coindcx": "DOGEINR", "step": 0},
    "ADA/INR": {"base_curr": "INR", "binance": "ADAUSDT", "pair": "B-ADA_INR", "coindcx": "ADAINR", "step": 1},
    "PEPE/INR": {"base_curr": "INR", "binance": "PEPEUSDT", "pair": "B-PEPE_INR", "coindcx": "PEPEINR", "step": 0},
    "BONK/INR": {"base_curr": "INR", "binance": "BONKUSDT", "pair": "B-BONK_INR", "coindcx": "BONKINR", "step": 0},
    "RENDER/INR": {"base_curr": "INR", "binance": "RENDERUSDT", "pair": "B-RENDER_INR", "coindcx": "RENDERINR", "step": 2}
}

# --- DYNAMIC POSITION SIZING HELPER ---
def get_dynamic_allocation(base_curr, inr_bal, usdt_bal):
    if base_curr == "INR":
        scaled = inr_bal * ALLOCATION_PCT
        return max(MIN_TRADE_INR, min(scaled, MAX_TRADE_INR))
    else:
        scaled = usdt_bal * ALLOCATION_PCT
        return max(MIN_TRADE_USDT, min(scaled, MAX_TRADE_USDT))

# --- PERSISTENT STATE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0, "curr": "INR"} for name in SYMBOLS}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

# --- TELEGRAM CONTROLS ---
def get_control_keyboard():
    keyboard = [
        [
            {"text": "📊 Live Status", "callback_data": "cmd_status"},
            {"text": "💰 Wallets (INR+USDT)", "callback_data": "cmd_balance"}
        ]
    ]

    active_sells = []
    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            active_sells.append({"text": f"🔴 Sell {p_name}", "callback_data": f"sell_{p_name}"})

    if active_sells:
        keyboard.append(active_sells)

    keyboard.append([
        {"text": "📈 Daily Analytics", "callback_data": "cmd_analytics"},
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

def get_coindcx_balances():
    success, data = coindcx_auth_post("/exchange/v1/users/balances", {})
    inr_bal = 0.0
    usdt_bal = 0.0
    if success and isinstance(data, list):
        for item in data:
            if item.get("currency") == "INR":
                inr_bal = float(item.get("balance", 0.0))
            elif item.get("currency") == "USDT":
                usdt_bal = float(item.get("balance", 0.0))
    return inr_bal, usdt_bal

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
        time.sleep(1.2)
        ok, detail = coindcx_auth_post("/exchange/v1/orders/status", {"id": order_id})
        if ok and detail.get("status") in ["filled", "open"]:
            return True, resp
    return success, resp

def emergency_close_all():
    closed_count = 0
    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            pair_code = SYMBOLS[p_name]["coindcx"]
            place_coindcx_order(pair_code, "sell", pos["qty"])
            pos["side"] = None
            closed_count += 1
    save_state(active_positions)
    return closed_count

# --- ORDER BOOK SPREAD & DEPTH IMBALANCE ---
def check_orderbook_metrics(pair_name):
    """Checks both bid-ask spread tolerance and buyer vs seller volume imbalance."""
    try:
        url = f"https://public.coindcx.com/market_data/orderbook?pair={pair_name}"
        r = requests.get(url, timeout=4).json()
        bids = r.get("bids", {})
        asks = r.get("asks", {})
        if bids and asks:
            bid_prices = sorted([float(p) for p in bids.keys()], reverse=True)
            ask_prices = sorted([float(p) for p in asks.keys()])
            best_bid = bid_prices[0]
            best_ask = ask_prices[0]

            if best_bid > 0:
                spread_pct = ((best_ask - best_bid) / best_bid) * 100
                if spread_pct > MAX_SPREAD_TOLERANCE_PCT:
                    return False, "Spread too wide"

            # Check Top-5 Depth Volume Imbalance
            top_bids_vol = sum(float(bids[str(p)]) for p in bid_prices[:5] if str(p) in bids)
            top_asks_vol = sum(float(asks[str(p)]) for p in ask_prices[:5] if str(p) in asks)

            if top_asks_vol > 0:
                imbalance_ratio = top_bids_vol / top_asks_vol
                if imbalance_ratio < MIN_ORDER_BOOK_IMBALANCE:
                    return False, f"Weak buyer volume ({imbalance_ratio:.2f}x)"

            return True, "Passed"
    except Exception:
        pass
    return True, "Bypass"

# --- BTC MARKET HEALTH ---
def is_btc_healthy():
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=25"
        resp = requests.get(url, timeout=4).json()
        if not isinstance(resp, list) or len(resp) < 20:
            return True

        closes = [float(c[4]) for c in resp]
        series = pd.Series(closes)
        ema20 = series.ewm(span=20, adjust=False).mean().iloc[-1]
        last_price = closes[-1]
        open_last = float(resp[-1][1])
        drop_pct = ((last_price - open_last) / open_last) * 100

        if drop_pct < -0.85 or last_price < ema20:
            return False
        return True
    except Exception:
        return True

# --- BINANCE LEAD SIGNAL SCANNER ---
def check_binance_lead_signal(symbol_binance):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1m&limit=15"
        resp = requests.get(url, timeout=4).json()
        if not isinstance(resp, list) or len(resp) < 10:
            return False, 0.0

        candles = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in resp]
        df_b = pd.DataFrame(candles, columns=['open', 'high', 'low', 'close', 'volume'])

        last_c = df_b.iloc[-1]
        prev_vol_avg = df_b['volume'].iloc[-6:-1].mean()

        price_gain_pct = ((last_c['close'] - last_c['open']) / last_c['open']) * 100
        vol_surge = last_c['volume'] > (prev_vol_avg * 1.8)

        if price_gain_pct >= 0.25 and vol_surge:
            return True, price_gain_pct
    except Exception:
        pass
    return False, 0.0

# --- TECHNICAL ANALYSIS ENGINE ---
def fetch_coindcx_candles(pair_name, interval="1m", limit=35):
    try:
        url = f"https://public.coindcx.com/market_data/candles?pair={pair_name}&interval={interval}&limit={limit}"
        r = requests.get(url, timeout=6)
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

def generate_analytics_text():
    total = daily_stats["total_trades"]
    win_rate = (daily_stats["wins"] / total * 100) if total > 0 else 0.0
    return (
        f"📊 DAILY PERFORMANCE ANALYTICS\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Total Closed Trades: {total}\n"
        f"✅ Wins: {daily_stats['wins']} | ❌ Losses: {daily_stats['losses']}\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"💰 Realized PnL: ₹{daily_realized_pnl_inr:.2f}\n"
        f"🌟 Best Trade: ₹{daily_stats['best_trade_pnl']:.2f}\n"
        f"🔻 Worst Trade: ₹{daily_stats['worst_trade_pnl']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

def generate_status_text(user_name="Trader"):
    pos_summary = ""
    has_active = False
    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            has_active = True
            curr_sym = "$" if pos.get("curr") == "USDT" else "₹"
            pnl_pct = ((pos["best_price"] - pos["entry"]) / pos["entry"]) * 100 if pos["entry"] > 0 else 0.0
            pos_summary += (
                f"\n🪙 {p_name} ({pos['qty']} units)\n"
                f"Entry: {curr_sym}{pos['entry']:.4f} | Cur: {curr_sym}{pos['best_price']:.4f}\n"
                f"SL: {curr_sym}{pos['sl']:.4f} | Trailing: {pnl_pct:+.2f}%\n"
            )
    if not has_active:
        pos_summary = "\n💤 No active positions right now."

    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    return (
        f"👑 Multi-Asset Dual Engine ({user_name})\n"
        f"Status: {'⏸️ PAUSED' if is_paused else '🟢 ACTIVE'}\n"
        f"Open Trades: {active_count}/{MAX_PARALLEL_TRADES}\n"
        f"Realized PnL (Today): ₹{daily_realized_pnl_inr:.2f}\n"
        f"Trades Taken Today: {daily_stats['total_trades']}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"ACTIVE TRADES:{pos_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

# --- TRADE TRAILING & RISK ENGINE ---
def manage_trailing_sl(name, sym_cfg, curr_price):
    global daily_realized_pnl_inr, cool_off_tracker, daily_stats
    pos = active_positions[name]
    if pos.get("side") is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]
    curr_sym = "$" if sym_cfg["base_curr"] == "USDT" else "₹"

    # Breakeven Lock
    if curr_price >= (pos["entry"] + pos["atr"]) and pos["sl"] < pos["entry"]:
        pos["sl"] = pos["entry"]
        save_state(active_positions)
        send_telegram(f"🛡️ BREAKEVEN ACTIVATED\nPair: {name}\nSL locked to Entry: {curr_sym}{pos['entry']:.4f}")

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
            inr_pnl = (trade_pnl * 90.0) if sym_cfg["base_curr"] == "USDT" else trade_pnl
            daily_realized_pnl_inr += inr_pnl

            # Update Analytics
            daily_stats["total_trades"] += 1
            if inr_pnl >= 0:
                daily_stats["wins"] += 1
            else:
                daily_stats["losses"] += 1
            daily_stats["best_trade_pnl"] = max(daily_stats["best_trade_pnl"], inr_pnl)
            daily_stats["worst_trade_pnl"] = min(daily_stats["worst_trade_pnl"], inr_pnl)

            cool_off_tracker[name] = time.time() + (COOL_OFF_MINUTES * 60)
            send_telegram(
                f"🛑 AUTO-EXIT (SL/Trailing Hit)\n\n"
                f"Pair: {name}\n"
                f"Exit Price: {curr_sym}{curr_price:.4f}\n"
                f"PnL: {curr_sym}{trade_pnl:.2f} (~₹{inr_pnl:.2f})\n"
                f"Cooldown Active: {COOL_OFF_MINUTES} mins for this pair.",
                reply_markup=get_control_keyboard()
            )
            pos["side"] = None
            save_state(active_positions)

def scan_symbol(name, sym_cfg, inr_bal, usdt_bal):
    global is_paused, cool_off_tracker
    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    if is_paused or active_count >= MAX_PARALLEL_TRADES or daily_realized_pnl_inr <= -DAILY_MAX_LOSS_INR:
        return

    # Check Anti-Whipsaw Cooldown
    if name in cool_off_tracker and time.time() < cool_off_tracker[name]:
        return

    # Calculate Dynamic Allocation
    base_curr = sym_cfg["base_curr"]
    alloc = get_dynamic_allocation(base_curr, inr_bal, usdt_bal)

    if base_curr == "INR" and inr_bal < alloc:
        return
    if base_curr == "USDT" and usdt_bal < alloc:
        return

    # BTC Market Health
    if not is_btc_healthy():
        return

    pair_code = sym_cfg["pair"]
    coindcx_pair = sym_cfg["coindcx"]
    binance_symbol = sym_cfg["binance"]
    precision = sym_cfg["step"]

    # Order Book Depth & Spread Filter
    passed_book, _ = check_orderbook_metrics(pair_code)
    if not passed_book:
        return

    # Binance Early Surge Check
    binance_surging, surge_gain = check_binance_lead_signal(binance_symbol)

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
    local_breakout = (curr_price > float(df['swing_high'].iloc[-1])) and (curr_price > curr_open)

    should_enter = (
        active_positions[name].get("side") is None
        and binance_surging
        and (ema_bullish or local_breakout)
        and trend_strong
    )

    if should_enter:
        atr_val = float(df['atr'].iloc[-1])
        sl = max(float(df['swing_low'].iloc[-1]), curr_price - (atr_val * 1.5))
        raw_qty = alloc / curr_price
        qty = round(raw_qty, precision) if precision > 0 else math.floor(raw_qty)

        if qty > 0:
            success, _ = place_coindcx_order(coindcx_pair, "buy", qty)
            if success:
                active_positions[name] = {
                    "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price,
                    "atr": atr_val, "qty": qty, "curr": base_curr
                }
                save_state(active_positions)
                curr_sym = "$" if base_curr == "USDT" else "₹"
                send_telegram(
                    f"⚡ INSTITUTIONAL ENTRY (COMPOUND ENGINE)\n\n"
                    f"Pair: {name} ({base_curr})\n"
                    f"Binance Surge: +{surge_gain:.2f}%\n"
                    f"Local Price: {curr_sym}{curr_price:.4f}\n"
                    f"Allocated: {curr_sym}{alloc:.2f} (Units: {qty})\n"
                    f"Initial SL: {curr_sym}{sl:.4f}",
                    reply_markup=get_control_keyboard()
                )

# --- BACKGROUND AUTOMATION THREADS ---
def midnight_reset_scheduler():
    global daily_realized_pnl_inr, cool_off_tracker, daily_stats
    ist = timezone(timedelta(hours=5, minutes=30))
    while True:
        now = datetime.now(ist)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((next_run - now).total_seconds())

        # Send full analytics report at midnight
        report = f"🌙 MIDNIGHT REPORT (IST)\n\n" + generate_analytics_text()
        send_telegram(report)

        # Reset daily counters
        daily_realized_pnl_inr = 0.0
        cool_off_tracker.clear()
        daily_stats = {"total_trades": 0, "wins": 0, "losses": 0, "best_trade_pnl": 0.0, "worst_trade_pnl": 0.0}

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
                                answer_callback_query(cb["id"], "Wallets Checked")
                                inr_b, usdt_b = get_coindcx_balances()
                                send_telegram(
                                    f"💰 Live CoinDCX Wallets:\n\n"
                                    f"🇮🇳 INR Balance: ₹{inr_b:.2f}\n"
                                    f"💵 USDT Balance: ${usdt_b:.2f}",
                                    chat_id=sender_id, reply_markup=get_control_keyboard()
                                )
                            elif cb_data == "cmd_analytics":
                                answer_callback_query(cb["id"], "Analytics Loaded")
                                send_telegram(generate_analytics_text(), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_pause":
                                is_paused = True
                                answer_callback_query(cb["id"], "Paused")
                                send_telegram("⏸️ Bot Paused. New entries locked.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_resume":
                                is_paused = False
                                answer_callback_query(cb["id"], "Resumed")
                                send_telegram("▶️ Bot Active. Scanning markets.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_close_all":
                                answer_callback_query(cb["id"], "Exiting All")
                                count = emergency_close_all()
                                send_telegram(f"🚨 Panic Close: Exited {count} positions at market price.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data.startswith("sell_"):
                                target_pair = cb_data.replace("sell_", "")
                                if target_pair in active_positions and active_positions[target_pair].get("side") is not None:
                                    pos = active_positions[target_pair]
                                    pair_code = SYMBOLS[target_pair]["coindcx"]
                                    success, _ = place_coindcx_order(pair_code, "sell", pos["qty"])
                                    if success:
                                        base_c = SYMBOLS[target_pair]["base_curr"]
                                        curr_sym = "$" if base_c == "USDT" else "₹"
                                        recvd = pos["best_price"] * pos["qty"]
                                        answer_callback_query(cb["id"], f"Sold {target_pair}!")
                                        send_telegram(
                                            f"✅ MANUAL SELL EXECUTED\n\n"
                                            f"🪙 Pair: {target_pair}\n"
                                            f"📦 Units: {pos['qty']}\n"
                                            f"💵 Amount: {curr_sym}{recvd:.2f} credited to {base_c} Wallet.",
                                            chat_id=sender_id,
                                            reply_markup=get_control_keyboard()
                                        )
                                        pos["side"] = None
                                        save_state(active_positions)
                                    else:
                                        answer_callback_query(cb["id"], "Sell Failed!")

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

print("Master Multi-Asset Engine with Compound & Depth Filter Online...")
send_telegram("🔥 CoinDCX Multi-Asset Engine Armed!\nUpgrades: Dynamic Sizing + Order Book Imbalance + Analytics Deck.", reply_markup=get_control_keyboard())

# Main Scan Cycle
while True:
    inr_bal, usdt_bal = get_coindcx_balances()
    for name, sym_cfg in SYMBOLS.items():
        scan_symbol(name, sym_cfg, inr_bal, usdt_bal)
        time.sleep(0.3)
    time.sleep(2)
