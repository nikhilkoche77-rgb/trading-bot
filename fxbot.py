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

# --- SIP WEIGHT & RISK ENGINE PARAMETERS ---
TARGET_RR_RATIO = 2.0          # 1:2 Risk to Reward Target
WEIGHT_ALLOCATION_PCT = 0.10   # 10% base SIP weight of active balance
MIN_TRADE_INR = 100.0          # CoinDCX minimum INR execution limit
MIN_TRADE_USDT = 1.20          # CoinDCX minimum USDT execution limit
MAX_TRADE_INR = 500.0          # Upper ceiling cap per trade
MAX_TRADE_USDT = 5.50          # Upper ceiling cap per trade

MAX_PARALLEL_TRADES = 2
DAILY_MAX_LOSS_INR = 30.0
COOL_OFF_MINUTES = 20
MAX_SPREAD_TOLERANCE_PCT = 0.45
MIN_ORDER_BOOK_IMBALANCE = 1.40

STATE_FILE = "trades_state.json"
is_paused = False
daily_realized_pnl_inr = 0.0
cool_off_tracker = {}

daily_stats = {
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "best_trade_pnl": 0.0,
    "worst_trade_pnl": 0.0
}

# --- UNIFIED ASSET UNIVERSE WITH LOT CONTRACTS ---
SYMBOLS = {
    # Commodities & Forex Synthetics
    "XAU/USDT": {"base_curr": "USDT", "binance": "PAXGUSDT", "pair": "B-PAXG_USDT", "coindcx": "PAXGUSDT", "step": 4, "lot_contract": 100.0},
    "EUR/USDT": {"base_curr": "USDT", "binance": "EURUSDT", "pair": "B-EUR_USDT", "coindcx": "EURUSDT", "step": 4, "lot_contract": 100000.0},
    "USDT/INR": {"base_curr": "INR", "binance": "USDCUSDT", "pair": "B-USDT_INR", "coindcx": "USDTINR", "step": 2, "lot_contract": 100000.0},
    "GOLD/INR": {"base_curr": "INR", "binance": "PAXGUSDT", "pair": "B-PAXG_INR", "coindcx": "PAXGINR", "step": 4, "lot_contract": 100.0},

    # Crypto Majors & Altcoins
    "BTC/USDT": {"base_curr": "USDT", "binance": "BTCUSDT", "pair": "B-BTC_USDT", "coindcx": "BTCUSDT", "step": 5, "lot_contract": 1.0},
    "ETH/USDT": {"base_curr": "USDT", "binance": "ETHUSDT", "pair": "B-ETH_USDT", "coindcx": "ETHUSDT", "step": 4, "lot_contract": 1.0},
    "SOL/USDT": {"base_curr": "USDT", "binance": "SOLUSDT", "pair": "B-SOL_USDT", "coindcx": "SOLUSDT", "step": 3, "lot_contract": 1.0},
    "XRP/USDT": {"base_curr": "USDT", "binance": "XRPUSDT", "pair": "B-XRP_USDT", "coindcx": "XRPUSDT", "step": 1, "lot_contract": 1.0},
    "DOGE/USDT": {"base_curr": "USDT", "binance": "DOGEUSDT", "pair": "B-DOGE_USDT", "coindcx": "DOGEUSDT", "step": 0, "lot_contract": 1.0},
    "PEPE/USDT": {"base_curr": "USDT", "binance": "PEPEUSDT", "pair": "B-PEPE_USDT", "coindcx": "PEPEUSDT", "step": 0, "lot_contract": 1.0},
    "SHIB/USDT": {"base_curr": "USDT", "binance": "SHIBUSDT", "pair": "B-SHIB_USDT", "coindcx": "SHIBUSDT", "step": 0, "lot_contract": 1.0},
    "SUI/USDT": {"base_curr": "USDT", "binance": "SUIUSDT", "pair": "B-SUI_USDT", "coindcx": "SUIUSDT", "step": 1, "lot_contract": 1.0},
    "NEAR/USDT": {"base_curr": "USDT", "binance": "NEARUSDT", "pair": "B-NEAR_USDT", "coindcx": "NEARUSDT", "step": 2, "lot_contract": 1.0},
    "AVAX/USDT": {"base_curr": "USDT", "binance": "AVAXUSDT", "pair": "B-AVAX_USDT", "coindcx": "AVAXUSDT", "step": 2, "lot_contract": 1.0},
    "WIF/USDT": {"base_curr": "USDT", "binance": "WIFUSDT", "pair": "B-WIF_USDT", "coindcx": "WIFUSDT", "step": 2, "lot_contract": 1.0},
    "LINK/USDT": {"base_curr": "USDT", "binance": "LINKUSDT", "pair": "B-LINK_USDT", "coindcx": "LINKUSDT", "step": 2, "lot_contract": 1.0},
    "FTM/USDT": {"base_curr": "USDT", "binance": "FTMUSDT", "pair": "B-FTM_USDT", "coindcx": "FTMUSDT", "step": 1, "lot_contract": 1.0},

    # Direct INR Pairs
    "BTC/INR": {"base_curr": "INR", "binance": "BTCUSDT", "pair": "B-BTC_INR", "coindcx": "BTCINR", "step": 5, "lot_contract": 1.0},
    "ETH/INR": {"base_curr": "INR", "binance": "ETHUSDT", "pair": "B-ETH_INR", "coindcx": "ETHINR", "step": 4, "lot_contract": 1.0},
    "SOL/INR": {"base_curr": "INR", "binance": "SOLUSDT", "pair": "B-SOL_INR", "coindcx": "SOLINR", "step": 3, "lot_contract": 1.0},
    "XRP/INR": {"base_curr": "INR", "binance": "XRPUSDT", "pair": "B-XRP_INR", "coindcx": "XRPINR", "step": 1, "lot_contract": 1.0},
    "DOGE/INR": {"base_curr": "INR", "binance": "DOGEUSDT", "pair": "B-DOGE_INR", "coindcx": "DOGEINR", "step": 0, "lot_contract": 1.0},
    "ADA/INR": {"base_curr": "INR", "binance": "ADAUSDT", "pair": "B-ADA_INR", "coindcx": "ADAINR", "step": 1, "lot_contract": 1.0},
    "PEPE/INR": {"base_curr": "INR", "binance": "PEPEUSDT", "pair": "B-PEPE_INR", "coindcx": "PEPEINR", "step": 0, "lot_contract": 1.0},
    "BONK/INR": {"base_curr": "INR", "binance": "BONKUSDT", "pair": "B-BONK_INR", "coindcx": "BONKINR", "step": 0, "lot_contract": 1.0},
    "RENDER/INR": {"base_curr": "INR", "binance": "RENDERUSDT", "pair": "B-RENDER_INR", "coindcx": "RENDERINR", "step": 2, "lot_contract": 1.0}
}

def calculate_lot_size(qty, lot_contract):
    lot = qty / lot_contract
    return round(lot, 5) if lot < 0.01 else round(lot, 3)

# --- SIP WEIGHT SIZING ENGINE ---
def calculate_sip_weight_allocation(base_curr, inr_bal, usdt_bal):
    """
    Calculates dynamic trade amount based on active balance and weight factor.
    """
    bal = inr_bal if base_curr == "INR" else usdt_bal
    raw_amount = bal * WEIGHT_ALLOCATION_PCT
    min_limit = MIN_TRADE_INR if base_curr == "INR" else MIN_TRADE_USDT
    max_limit = MAX_TRADE_INR if base_curr == "INR" else MAX_TRADE_USDT
    return max(min_limit, min(raw_amount, max_limit))

# --- PERSISTENT STATE ---
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {name: {"side": None, "entry": 0.0, "sl": 0.0, "tp": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0, "curr": "INR", "lot": 0.0, "amount": 0.0} for name in SYMBOLS}

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
            {"text": "📊 Live Terminal", "callback_data": "cmd_status"},
            {"text": "💰 Wallets", "callback_data": "cmd_balance"}
        ],
        [
            {"text": f"⚖️ SIP Weight ({int(WEIGHT_ALLOCATION_PCT*100)}%)", "callback_data": "cmd_toggle_weight"},
            {"text": f"🎯 Target (1:{TARGET_RR_RATIO})", "callback_data": "cmd_toggle_rr"}
        ],
        [
            {"text": "📋 SIP Weight Breakdown", "callback_data": "cmd_weight_calc"}
        ]
    ]

    active_sells = []
    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            active_sells.append({"text": f"🔴 Close {p_name} ({pos.get('lot', 0.0):.4f} Lot)", "callback_data": f"sell_{p_name}"})

    if active_sells:
        keyboard.append(active_sells)

    keyboard.append([
        {"text": "📈 Performance Analytics", "callback_data": "cmd_analytics"},
        {"text": "⏸️ Pause", "callback_data": "cmd_pause"},
        {"text": "▶️ Resume", "callback_data": "cmd_resume"}
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

# --- ORDER BOOK SPREAD & DEPTH ---
def check_orderbook_metrics(pair_name):
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

            top_bids_vol = sum(float(bids[str(p)]) for p in bid_prices[:5] if str(p) in bids)
            top_asks_vol = sum(float(asks[str(p)]) for p in ask_prices[:5] if str(p) in asks)

            if top_asks_vol > 0:
                imbalance_ratio = top_bids_vol / top_asks_vol
                if imbalance_ratio < MIN_ORDER_BOOK_IMBALANCE:
                    return False, "Weak buyers"

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

# --- SIP BREAKDOWN & STATUS TEXTS ---
def generate_sip_breakdown_text(inr_bal, usdt_bal):
    inr_trade = calculate_sip_weight_allocation("INR", inr_bal, usdt_bal)
    usdt_trade = calculate_sip_weight_allocation("USDT", inr_bal, usdt_bal)
    
    # Sample calculation for SOL and BTC
    sol_qty_sample = inr_trade / 14000.0 if inr_trade > 0 else 0.0
    return (
        f"📋 SIP WEIGHT DYNAMIC BREAKDOWN\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🇮🇳 INR Wallet: ₹{inr_bal:.2f}\n"
        f"• Base Allocation Weight: {int(WEIGHT_ALLOCATION_PCT*100)}%\n"
        f"• Current Calculated Trade Amount: ₹{inr_trade:.2f}\n"
        f"• Estimated Execution Volume (Sample SOL): ~{sol_qty_sample:.4f} Units\n"
        f"• Auto-Limits: Min ₹{MIN_TRADE_INR:.0f} | Max ₹{MAX_TRADE_INR:.0f}\n\n"
        f"💵 USDT Wallet: ${usdt_bal:.2f}\n"
        f"• Current Calculated Trade Amount: ${usdt_trade:.2f}\n"
        f"• Auto-Limits: Min ${MIN_TRADE_USDT:.2f} | Max ${MAX_TRADE_USDT:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💡 Balance jaise badhega ya ghatega, ye amounts aur quantities live dynamically adjust hongi."
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
                f"\n📌 {p_name}\n"
                f"• Volume (Lot): {pos.get('lot', 0.0):.5f} Lot ({pos['qty']} units)\n"
                f"• Amount Allocated: {curr_sym}{pos.get('amount', 0.0):.2f}\n"
                f"• Entry: {curr_sym}{pos['entry']:.4f} | Cur: {curr_sym}{pos['best_price']:.4f}\n"
                f"• SL: {curr_sym}{pos['sl']:.4f} | TP (1:{TARGET_RR_RATIO}): {curr_sym}{pos.get('tp', 0.0):.4f}\n"
                f"• Running Trailing: {pnl_pct:+.2f}%\n"
            )
    if not has_active:
        pos_summary = "\n💤 No active market positions right now."

    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    return (
        f"👑 Terminal Status ({user_name})\n"
        f"Status: {'⏸️ PAUSED' if is_paused else '🟢 ONLINE'}\n"
        f"SIP Weight: {int(WEIGHT_ALLOCATION_PCT*100)}% Dynamic | 1:{TARGET_RR_RATIO} Target\n"
        f"Open Positions: {active_count}/{MAX_PARALLEL_TRADES}\n"
        f"Net Daily PnL: ₹{daily_realized_pnl_inr:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"ACTIVE TRADES:{pos_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

def generate_analytics_text():
    total = daily_stats["total_trades"]
    win_rate = (daily_stats["wins"] / total * 100) if total > 0 else 0.0
    return (
        f"📊 INSTITUTIONAL PERFORMANCE REPORT\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Total Completed Trades: {total}\n"
        f"✅ Wins: {daily_stats['wins']} | ❌ Losses: {daily_stats['losses']}\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"💰 Realized Net PnL: ₹{daily_realized_pnl_inr:.2f}\n"
        f"🌟 Best Winner: ₹{daily_stats['best_trade_pnl']:.2f}\n"
        f"🔻 Worst Trade: ₹{daily_stats['worst_trade_pnl']:.2f}\n"
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

    # Breakeven Lock at 1:1 move
    risk_distance = abs(pos["entry"] - pos["sl"])
    if curr_price >= (pos["entry"] + risk_distance) and pos["sl"] < pos["entry"]:
        pos["sl"] = pos["entry"]
        save_state(active_positions)
        send_telegram(f"🛡️ BREAKEVEN ACTIVATED (1:1 Hit)\nPair: {name}\nSL locked to Entry: {curr_sym}{pos['entry']:.4f}")

    # Full Take-Profit Target (1:2 R:R)
    if pos.get("tp", 0.0) > 0 and curr_price >= pos["tp"]:
        success, _ = place_coindcx_order(coindcx_pair, "sell", qty)
        if success:
            trade_pnl = (curr_price - pos["entry"]) * qty
            inr_pnl = (trade_pnl * 90.0) if sym_cfg["base_curr"] == "USDT" else trade_pnl
            daily_realized_pnl_inr += inr_pnl

            daily_stats["total_trades"] += 1
            daily_stats["wins"] += 1
            daily_stats["best_trade_pnl"] = max(daily_stats["best_trade_pnl"], inr_pnl)

            cool_off_tracker[name] = time.time() + (COOL_OFF_MINUTES * 60)
            send_telegram(
                f"🎯 TAKE PROFIT TARGET FILLED (1:{TARGET_RR_RATIO})\n\n"
                f"Asset: {name}\n"
                f"Volume (Lot): {pos.get('lot', 0.0):.5f} Lot\n"
                f"Quantity: {qty} units\n"
                f"Exit Price: {curr_sym}{curr_price:.4f}\n"
                f"Net Profit: {curr_sym}{trade_pnl:.2f} (~₹{inr_pnl:.2f})",
                reply_markup=get_control_keyboard()
            )
            pos["side"] = None
            save_state(active_positions
