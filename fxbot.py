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

# --- EXPANDED LIMITS & RISK ENGINE ---
TARGET_RR_RATIO = 2.0          
WEIGHT_ALLOCATION_PCT = 0.10   
MIN_TRADE_INR = 100.0          
MIN_TRADE_USDT = 1.20          
MAX_TRADE_INR = 600.0          
MAX_TRADE_USDT = 7.00          

MAX_PARALLEL_TRADES = 4        
DAILY_MAX_LOSS_INR = 45.0      
COOL_OFF_MINUTES = 15          
MAX_SPREAD_TOLERANCE_PCT = 0.50 
MIN_ORDER_BOOK_IMBALANCE = 1.30 

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

# --- UNIFIED ASSETS & CONTRACTS ---
SYMBOLS = {
    # Commodities & Forex Synthetics
    "XAU/USDT": {"base_curr": "USDT", "target_coin": "PAXG", "binance": "PAXGUSDT", "pair": "B-PAXG_USDT", "coindcx": "PAXGUSDT", "step": 4, "lot_contract": 100.0, "price_dec": 2},
    "EUR/USDT": {"base_curr": "USDT", "target_coin": "EUR", "binance": "EURUSDT", "pair": "B-EUR_USDT", "coindcx": "EURUSDT", "step": 4, "lot_contract": 100000.0, "price_dec": 4},
    "USDT/INR": {"base_curr": "INR", "target_coin": "USDT", "binance": "USDCUSDT", "pair": "B-USDT_INR", "coindcx": "USDTINR", "step": 2, "lot_contract": 100000.0, "price_dec": 2},
    "GOLD/INR": {"base_curr": "INR", "target_coin": "PAXG", "binance": "PAXGUSDT", "pair": "B-PAXG_INR", "coindcx": "PAXGINR", "step": 4, "lot_contract": 100.0, "price_dec": 2},

    # Crypto Majors & High Beta Coins
    "BTC/USDT": {"base_curr": "USDT", "target_coin": "BTC", "binance": "BTCUSDT", "pair": "B-BTC_USDT", "coindcx": "BTCUSDT", "step": 5, "lot_contract": 1.0, "price_dec": 2},
    "ETH/USDT": {"base_curr": "USDT", "target_coin": "ETH", "binance": "ETHUSDT", "pair": "B-ETH_USDT", "coindcx": "ETHUSDT", "step": 4, "lot_contract": 1.0, "price_dec": 2},
    "SOL/USDT": {"base_curr": "USDT", "target_coin": "SOL", "binance": "SOLUSDT", "pair": "B-SOL_USDT", "coindcx": "SOLUSDT", "step": 3, "lot_contract": 1.0, "price_dec": 2},
    "XRP/USDT": {"base_curr": "USDT", "target_coin": "XRP", "binance": "XRPUSDT", "pair": "B-XRP_USDT", "coindcx": "XRPUSDT", "step": 1, "lot_contract": 1.0, "price_dec": 4},
    "DOGE/USDT": {"base_curr": "USDT", "target_coin": "DOGE", "binance": "DOGEUSDT", "pair": "B-DOGE_USDT", "coindcx": "DOGEUSDT", "step": 0, "lot_contract": 1000.0, "price_dec": 5},
    "PEPE/USDT": {"base_curr": "USDT", "target_coin": "PEPE", "binance": "PEPEUSDT", "pair": "B-PEPE_USDT", "coindcx": "PEPEUSDT", "step": 0, "lot_contract": 1000000.0, "price_dec": 8},
    "SHIB/USDT": {"base_curr": "USDT", "target_coin": "SHIB", "binance": "SHIBUSDT", "pair": "B-SHIB_USDT", "coindcx": "SHIBUSDT", "step": 0, "lot_contract": 100000.0, "price_dec": 8},
    "SUI/USDT": {"base_curr": "USDT", "target_coin": "SUI", "binance": "SUIUSDT", "pair": "B-SUI_USDT", "coindcx": "SUIUSDT", "step": 1, "lot_contract": 1.0, "price_dec": 4},
    "NEAR/USDT": {"base_curr": "USDT", "target_coin": "NEAR", "binance": "NEARUSDT", "pair": "B-NEAR_USDT", "coindcx": "NEARUSDT", "step": 2, "lot_contract": 1.0, "price_dec": 3},
    "AVAX/USDT": {"base_curr": "USDT", "target_coin": "AVAX", "binance": "AVAXUSDT", "pair": "B-AVAX_USDT", "coindcx": "AVAXUSDT", "step": 2, "lot_contract": 1.0, "price_dec": 2},
    "WIF/USDT": {"base_curr": "USDT", "target_coin": "WIF", "binance": "WIFUSDT", "pair": "B-WIF_USDT", "coindcx": "WIFUSDT", "step": 2, "lot_contract": 1.0, "price_dec": 4},
    "LINK/USDT": {"base_curr": "USDT", "target_coin": "LINK", "binance": "LINKUSDT", "pair": "B-LINK_USDT", "coindcx": "LINKUSDT", "step": 2, "lot_contract": 1.0, "price_dec": 3},
    "FTM/USDT": {"base_curr": "USDT", "target_coin": "FTM", "binance": "FTMUSDT", "pair": "B-FTM_USDT", "coindcx": "FTMUSDT", "step": 1, "lot_contract": 1.0, "price_dec": 4},

    # Direct INR Pairs
    "BTC/INR": {"base_curr": "INR", "target_coin": "BTC", "binance": "BTCUSDT", "pair": "B-BTC_INR", "coindcx": "BTCINR", "step": 5, "lot_contract": 1.0, "price_dec": 2},
    "ETH/INR": {"base_curr": "INR", "target_coin": "ETH", "binance": "ETHUSDT", "pair": "B-ETH_INR", "coindcx": "ETHINR", "step": 4, "lot_contract": 1.0, "price_dec": 2},
    "SOL/INR": {"base_curr": "INR", "target_coin": "SOL", "binance": "SOLUSDT", "pair": "B-SOL_INR", "coindcx": "SOLINR", "step": 3, "lot_contract": 1.0, "price_dec": 2},
    "XRP/INR": {"base_curr": "INR", "target_coin": "XRP", "binance": "XRPUSDT", "pair": "B-XRP_INR", "coindcx": "XRPINR", "step": 1, "lot_contract": 1.0, "price_dec": 2},
    "DOGE/INR": {"base_curr": "INR", "target_coin": "DOGE", "binance": "DOGEUSDT", "pair": "B-DOGE_INR", "coindcx": "DOGEINR", "step": 0, "lot_contract": 100.0, "price_dec": 4},
    "ADA/INR": {"base_curr": "INR", "target_coin": "ADA", "binance": "ADAUSDT", "pair": "B-ADA_INR", "coindcx": "ADAINR", "step": 1, "lot_contract": 10.0, "price_dec": 2},
    "PEPE/INR": {"base_curr": "INR", "target_coin": "PEPE", "binance": "PEPEUSDT", "pair": "B-PEPE_INR", "coindcx": "PEPEINR", "step": 0, "lot_contract": 1000000.0, "price_dec": 8},
    "BONK/INR": {"base_curr": "INR", "target_coin": "BONK", "binance": "BONKUSDT", "pair": "B-BONK_INR", "coindcx": "BONKINR", "step": 0, "lot_contract": 100000.0, "price_dec": 8},
    "RENDER/INR": {"base_curr": "INR", "target_coin": "RENDER", "binance": "RENDERUSDT", "pair": "B-RENDER_INR", "coindcx": "RENDERINR", "step": 2, "lot_contract": 1.0, "price_dec": 2}
}

def calculate_lot_size(qty, lot_contract):
    lot = qty / lot_contract
    return round(lot, 5) if lot < 0.01 else round(lot, 3)

def calculate_sip_weight_allocation(base_curr, inr_bal, usdt_bal):
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
    return {name: {"side": None, "style": "INTRADAY", "entry": 0.0, "sl": 0.0, "tp": 0.0, "best_price": 0.0, "atr": 0.0, "qty": 0.0, "curr": "INR", "lot": 0.0, "amount": 0.0} for name in SYMBOLS}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

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
    raw_balances = {}
    if success and isinstance(data, list):
        for item in data:
            curr = item.get("currency", "")
            b_val = float(item.get("balance", 0.0))
            raw_balances[curr] = b_val
            if curr == "INR":
                inr_bal = b_val
            elif curr == "USDT":
                usdt_bal = b_val
    return inr_bal, usdt_bal, raw_balances

# Real-Time Live Ticker Price (Instant Microsecond Fetch)
def get_live_market_price(market_code, pair_name):
    try:
        url = "https://api.coindcx.com/exchange/ticker"
        res = requests.get(url, timeout=3).json()
        if isinstance(res, list):
            for t in res:
                if t.get("market") == market_code:
                    return float(t.get("last_price", 0.0))
    except Exception:
        pass
    # Fallback to candle if ticker fails
    try:
        url = f"https://public.coindcx.com/market_data/candles?pair={pair_name}&interval=1m&limit=2"
        r = requests.get(url, timeout=3).json()
        if isinstance(r, list) and len(r) > 0:
            return float(r[0]['close'])
    except Exception:
        pass
    return 0.0

def sync_existing_holdings_from_exchange():
    _, _, raw_balances = get_coindcx_balances()
    synced_any = False

    for name, cfg in SYMBOLS.items():
        t_coin = cfg.get("target_coin")
        if not t_coin or t_coin in ["INR", "USDT"]:
            continue

        holding_qty = raw_balances.get(t_coin, 0.0)
        curr_price = get_live_market_price(cfg["coindcx"], cfg["pair"])

        if curr_price > 0 and (holding_qty * curr_price) >= 90.0:
            if active_positions[name].get("side") is None:
                step = cfg["step"]
                clean_qty = round(holding_qty, step) if step > 0 else math.floor(holding_qty)
                lot_size = calculate_lot_size(clean_qty, cfg.get("lot_contract", 1.0))
                approx_amount = round(clean_qty * curr_price, 2)
                initial_sl = round(curr_price * 0.985, cfg.get("price_dec", 4))

                active_positions[name] = {
                    "side": "BUY",
                    "style": "🎯 INTRADAY",
                    "entry": curr_price,
                    "sl": initial_sl,
                    "tp": round(curr_price * 1.03, cfg.get("price_dec", 4)),
                    "best_price": curr_price,
                    "atr": curr_price * 0.01,
                    "qty": clean_qty,
                    "curr": cfg["base_curr"],
                    "lot": lot_size,
                    "amount": approx_amount
                }
                synced_any = True
                send_telegram(
                    f"🔄 AUTO-SYNCED EXISTING HOLDING\n\n"
                    f"Asset: {name}\n"
                    f"Style: 🎯 INTRADAY\n"
                    f"Units: {clean_qty} ({lot_size:.4f} Lot)\n"
                    f"Entry Price: {curr_price}\n"
                    f"Live Value: {cfg['base_curr']} {approx_amount:.2f}",
                    reply_markup=get_control_keyboard()
                )

    if synced_any:
        save_state(active_positions)

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

# --- TELEGRAM CONTROLS & STATUS ---
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
            {"text": "🔄 Refresh / Sync", "callback_data": "cmd_sync_now"},
            {"text": "📋 SIP Breakdown", "callback_data": "cmd_weight_calc"}
        ]
    ]

    active_sells = []
    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            active_sells.append({"text": f"🔴 Close {p_name}", "callback_data": f"sell_{p_name}"})

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

def generate_sip_breakdown_text(inr_bal, usdt_bal):
    inr_trade = calculate_sip_weight_allocation("INR", inr_bal, usdt_bal)
    usdt_trade = calculate_sip_weight_allocation("USDT", inr_bal, usdt_bal)
    return (
        f"📋 DYNAMIC SIZING BREAKDOWN\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🇮🇳 INR Wallet: ₹{inr_bal:.2f}\n"
        f"• Base Allocation: {int(WEIGHT_ALLOCATION_PCT*100)}%\n"
        f"• Next Trade Amount: ₹{inr_trade:.2f}\n"
        f"• Max Open Parallel Trades: {MAX_PARALLEL_TRADES}\n\n"
        f"💵 USDT Wallet: ${usdt_bal:.2f}\n"
        f"• Next Trade Amount: ${usdt_trade:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

def generate_status_text(user_name="Trader"):
    pos_summary = ""
    has_active = False
    total_unrealized_inr = 0.0

    for p_name, pos in active_positions.items():
        if pos.get("side") is not None:
            has_active = True
            curr_sym = "$" if pos.get("curr") == "USDT" else "₹"
            p_dec = SYMBOLS[p_name].get("price_dec", 4)

            # Microsecond Real-time Live Price via Ticker
            latest_price = get_live_market_price(SYMBOLS[p_name]["coindcx"], SYMBOLS[p_name]["pair"])
            if latest_price > 0:
                pos["best_price"] = latest_price

            cur_p = pos["best_price"]
            ent_p = pos["entry"]
            qty = pos["qty"]

            # Real-Time Running PnL (Amount + %)
            running_pnl = (cur_p - ent_p) * qty
            running_pct = ((cur_p - ent_p) / ent_p) * 100 if ent_p > 0 else 0.0
            inr_running = (running_pnl * 90.0) if pos.get("curr") == "USDT" else running_pnl
            total_unrealized_inr += inr_running

            pos_summary += (
                f"\n📌 {p_name} [{pos.get('style', '🎯 INTRADAY')}]\n"
                f"• Size: {pos.get('lot', 0.0):.4f} Lot | Amt: {curr_sym}{pos.get('amount', 0.0):.2f}\n"
                f"• Entry: {curr_sym}{ent_p:.{p_dec}f} | Current: {curr_sym}{cur_p:.{p_dec}f}\n"
                f"• SL: {curr_sym}{pos['sl']:.{p_dec}f} | TP: {curr_sym}{pos.get('tp', 0.0):.{p_dec}f}\n"
                f"• 💰 Live PnL: {curr_sym}{running_pnl:+.4f} ({running_pct:+.2f}%)\n"
            )

    save_state(active_positions)

    if not has_active:
        pos_summary = "\n💤 No active market positions right now."

    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    return (
        f"👑 Terminal Status ({user_name})\n"
        f"Status: {'⏸️ PAUSED' if is_paused else '🟢 ONLINE'}\n"
        f"Open Positions: {active_count}/{MAX_PARALLEL_TRADES}\n"
        f"💵 Total Live PnL: ₹{total_unrealized_inr:+.2f}\n"
        f"Net Closed PnL (Today): ₹{daily_realized_pnl_inr:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"ACTIVE TRADES & LIVE PROFIT:{pos_summary}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

def generate_analytics_text():
    total = daily_stats["total_trades"]
    win_rate = (daily_stats["wins"] / total * 100) if total > 0 else 0.0
    return (
        f"📊 PERFORMANCE REPORT\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🔢 Total Trades: {total} | Wins: {daily_stats['wins']} | Losses: {daily_stats['losses']}\n"
        f"🎯 Win Rate: {win_rate:.1f}%\n"
        f"💰 Realized Net PnL: ₹{daily_realized_pnl_inr:.2f}\n"
        f"🌟 Best Trade: ₹{daily_stats['best_trade_pnl']:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━"
    )

# --- TRADE TRAILING, SWING RUNNER & RISK ENGINE ---
def manage_trailing_sl(name, sym_cfg, curr_price):
    global daily_realized_pnl_inr, cool_off_tracker, daily_stats
    pos = active_positions[name]
    if pos.get("side") is None:
        return

    trailing_gap = pos["atr"] * 1.5
    coindcx_pair = sym_cfg["coindcx"]
    qty = pos["qty"]
    curr_sym = "$" if sym_cfg["base_curr"] == "USDT" else "₹"
    style = pos.get("style", "🎯 INTRADAY")
    p_dec = sym_cfg.get("price_dec", 4)

    risk_distance = abs(pos["entry"] - pos["sl"])
    profit_distance = curr_price - pos["entry"]

    # 1. Breakeven Lock at 1:1 move
    if curr_price >= (pos["entry"] + risk_distance) and pos["sl"] < pos["entry"]:
        pos["sl"] = pos["entry"]
        save_state(active_positions)
        send_telegram(f"🛡️ BREAKEVEN ACTIVATED (Zero Risk)\nPair: {name}\nStyle: {style}\nSL locked to Entry: {curr_sym}{pos['entry']:.{p_dec}f}")

    # 2. SWING MULTI-BAGGER RUNNER
    if "SWING" in style:
        if profit_distance >= (risk_distance * 4.0):
            swing_sl = curr_price - pos["atr"]
            if swing_sl > pos["sl"]:
                pos["sl"] = swing_sl
                save_state(active_positions)
    else:
        # Intraday / Scalping Target Exit at 1:2 R:R
        if pos.get("tp", 0.0) > 0 and curr_price >= pos["tp"]:
            success, _ = place_coindcx_order(coindcx_pair, "sell", qty)
            if success:
                trade_pnl = (curr_price - pos["entry"]) * qty
                inr_pnl = (trade_pnl * 90.0) if sym_cfg["base_curr"] == "USDT" else trade_pnl
                daily_realized_pnl_inr += inr_pnl
                daily_stats["total_trades"] += 1
                daily_stats["wins"] += 1
                cool_off_tracker[name] = time.time() + (COOL_OFF_MINUTES * 60)
                send_telegram(
                    f"🎯 TAKE PROFIT TARGET FILLED\n\n"
                    f"Asset: {name} [{style}]\n"
                    f"Units: {qty}\n"
                    f"Exit Price: {curr_sym}{curr_price:.{p_dec}f}\n"
                    f"Net Profit: {curr_sym}{trade_pnl:.2f} (~₹{inr_pnl:.2f})",
                    reply_markup=get_control_keyboard()
                )
                pos["side"] = None
                save_state(active_positions)
                return

    # 3. Dynamic Trailing Stop Loss
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
            daily_stats["total_trades"] += 1
            if inr_pnl >= 0:
                daily_stats["wins"] += 1
            else:
                daily_stats["losses"] += 1

            cool_off_tracker[name] = time.time() + (COOL_OFF_MINUTES * 60)
            send_telegram(
                f"🛑 TRAILING / SL EXIT\n\n"
                f"Asset: {name} [{style}]\n"
                f"Exit Price: {curr_sym}{curr_price:.{p_dec}f}\n"
                f"Closed PnL: {curr_sym}{trade_pnl:.2f} (~₹{inr_pnl:.2f})",
                reply_markup=get_control_keyboard()
            )
            pos["side"] = None
            save_state(active_positions)

def scan_symbol(name, sym_cfg, inr_bal, usdt_bal):
    global is_paused, cool_off_tracker
    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    if is_paused or active_count >= MAX_PARALLEL_TRADES or daily_realized_pnl_inr <= -DAILY_MAX_LOSS_INR:
        return

    if name in cool_off_tracker and time.time() < cool_off_tracker[name]:
        return

    base_curr = sym_cfg["base_curr"]
    trade_allocation = calculate_sip_weight_allocation(base_curr, inr_bal, usdt_bal)

    if base_curr == "INR" and inr_bal < trade_allocation:
        return
    if base_curr == "USDT" and usdt_bal < trade_allocation:
        return

    if not is_btc_healthy():
        return

    pair_code = sym_cfg["pair"]
    coindcx_pair = sym_cfg["coindcx"]
    binance_symbol = sym_cfg["binance"]
    precision = sym_cfg["step"]
    p_dec = sym_cfg.get("price_dec", 4)

    passed_book, _ = check_orderbook_metrics(pair_code)
    if not passed_book:
        return

    binance_surging, surge_gain = check_binance_lead_signal(binance_symbol)

    df = fetch_coindcx_candles(pair_code, interval="1m", limit=35)
    if df is None or len(df) < 25:
        return

    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['atr'], df['adx'] = calculate_technical_indicators(df)
    df['swing_high'] = df['high'].iloc[-10:-1].max()
    df['swing_low'] = df['low'].iloc[-10:-1].min()

    # Get ultra-accurate real-time price
    live_p = get_live_market_price(coindcx_pair, pair_code)
    curr_price = live_p if live_p > 0 else float(df['close'].iloc[-1])
    curr_open = float(df['open'].iloc[-1])
    
    manage_trailing_sl(name, sym_cfg, curr_price)

    adx_val = float(df['adx'].iloc[-1])
    trend_strong = adx_val > 18.0
    ema_bullish = float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1])
    local_breakout = (curr_price > float(df['swing_high'].iloc[-1])) and (curr_price > curr_open)

    should_enter = (
        active_positions[name].get("side") is None
        and binance_surging
        and (ema_bullish or local_breakout)
        and trend_strong
    )

    if should_enter:
        if adx_val > 35.0 and surge_gain > 0.80:
            trade_style = "🏔️ SWING RUNNER"
        elif surge_gain > 0.40:
            trade_style = "🎯 INTRADAY"
        else:
            trade_style = "⚡ SCALPING"

        atr_val = float(df['atr'].iloc[-1])
        sl = max(float(df['swing_low'].iloc[-1]), curr_price - (atr_val * 1.5))
        risk_dist = abs(curr_price - sl)
        
        tp = curr_price + (risk_dist * (4.0 if "SWING" in trade_style else TARGET_RR_RATIO))

        raw_qty = trade_allocation / curr_price
        qty = round(raw_qty, precision) if precision > 0 else math.floor(raw_qty)

        if qty > 0:
            lot_size = calculate_lot_size(qty, sym_cfg.get("lot_contract", 1.0))
            exact_invested = round(qty * curr_price, 2)

            success, _ = place_coindcx_order(coindcx_pair, "buy", qty)
            if success:
                active_positions[name] = {
                    "side": "BUY", "style": trade_style, "entry": curr_price, "sl": sl, "tp": tp,
                    "best_price": curr_price, "atr": atr_val, "qty": qty, 
                    "curr": base_curr, "lot": lot_size, "amount": exact_invested
                }
                save_state(active_positions)
                curr_sym = "$" if base_curr == "USDT" else "₹"
                send_telegram(
                    f"⚡ ORDER EXECUTED [{trade_style}]\n\n"
                    f"Asset: {name} ({base_curr})\n"
                    f"Invested Amount: {curr_sym}{exact_invested:.2f}\n"
                    f"Quantity: {qty} ({lot_size:.4f} Lot)\n"
                    f"Entry: {curr_sym}{curr_price:.{p_dec}f}\n"
                    f"Stop Loss: {curr_sym}{sl:.{p_dec}f}\n"
                    f"Target: {curr_sym}{tp:.{p_dec}f}",
                    reply_markup=get_control_keyboard()
                )

# --- BACKGROUND THREADS ---
def midnight_reset_scheduler():
    global daily_realized_pnl_inr, cool_off_tracker, daily_stats
    ist = timezone(timedelta(hours=5, minutes=30))
    while True:
        now = datetime.now(ist)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=0, second=5, microsecond=0)
        time.sleep((next_run - now).total_seconds())

        report = f"🌙 MIDNIGHT REPORT (IST)\n\n" + generate_analytics_text()
        send_telegram(report)

        daily_realized_pnl_inr = 0.0
        cool_off_tracker.clear()
        daily_stats = {"total_trades": 0, "wins": 0, "losses": 0, "best_trade_pnl": 0.0, "worst_trade_pnl": 0.0}

def handle_incoming_users():
    global is_paused, TARGET_RR_RATIO, WEIGHT_ALLOCATION_PCT
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
                                answer_callback_query(cb["id"], "Fetching Live Tickers...")
                                send_telegram(generate_status_text(u_name), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_balance":
                                answer_callback_query(cb["id"], "Wallets Checked")
                                inr_b, usdt_b, _ = get_coindcx_balances()
                                send_telegram(
                                    f"💰 Broker Wallet Balances:\n\n"
                                    f"🇮🇳 INR Wallet: ₹{inr_b:.2f}\n"
                                    f"💵 USDT Wallet: ${usdt_b:.2f}",
                                    chat_id=sender_id, reply_markup=get_control_keyboard()
                                )
                            elif cb_data == "cmd_sync_now":
                                answer_callback_query(cb["id"], "Refreshing Live PnL & Tickers...")
                                sync_existing_holdings_from_exchange()
                                send_telegram(generate_status_text(u_name), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_weight_calc":
                                answer_callback_query(cb["id"], "Calculating...")
                                inr_b, usdt_b, _ = get_coindcx_balances()
                                send_telegram(generate_sip_breakdown_text(inr_b, usdt_b), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_toggle_weight":
                                if WEIGHT_ALLOCATION_PCT == 0.10:
                                    WEIGHT_ALLOCATION_PCT = 0.15
                                elif WEIGHT_ALLOCATION_PCT == 0.15:
                                    WEIGHT_ALLOCATION_PCT = 0.20
                                elif WEIGHT_ALLOCATION_PCT == 0.20:
                                    WEIGHT_ALLOCATION_PCT = 0.05
                                else:
                                    WEIGHT_ALLOCATION_PCT = 0.10
                                answer_callback_query(cb["id"], f"Weight: {int(WEIGHT_ALLOCATION_PCT*100)}%")
                                send_telegram(f"⚖️ Sizing adjusted to **{int(WEIGHT_ALLOCATION_PCT*100)}%**.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_toggle_rr":
                                if TARGET_RR_RATIO == 2.0:
                                    TARGET_RR_RATIO = 3.0
                                elif TARGET_RR_RATIO == 3.0:
                                    TARGET_RR_RATIO = 1.5
                                else:
                                    TARGET_RR_RATIO = 2.0
                                answer_callback_query(cb["id"], f"R:R 1:{TARGET_RR_RATIO}")
                                send_telegram(f"🎯 Target R:R set to **1:{TARGET_RR_RATIO}**.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_analytics":
                                answer_callback_query(cb["id"], "Analytics Loaded")
                                send_telegram(generate_analytics_text(), chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_pause":
                                is_paused = True
                                answer_callback_query(cb["id"], "Paused")
                                send_telegram("⏸️ Terminal Paused.", chat_id=sender_id, reply_markup=get_control_keyboard())
                            elif cb_data == "cmd_resume":
                                is_paused = False
                                answer_callback_query(cb["id"], "Resumed")
                                send_telegram("▶️ Terminal Active.", chat_id=sender_id, reply_markup=get_control_keyboard())
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
                                        answer_callback_query(cb["id"], f"Closed {target_pair}!")
                                        send_telegram(
                                            f"✅ MANUAL POSITION CLOSED\n\n"
                                            f"📌 Asset: {target_pair}\n"
                                            f"• Units: {pos['qty']}\n"
                                            f"• Credited: {curr_sym}{recvd:.2f} ({base_c} Wallet)",
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

sync_existing_holdings_from_exchange()

print("Master Terminal with Real-time Live PnL Online...")
send_telegram("🔥 Broker Terminal Online!\nReal-time Ticker PnL, Meme Precision & Style Tags Active.", reply_markup=get_control_keyboard())

# Main Scan Cycle
while True:
    inr_bal, usdt_bal, _ = get_coindcx_balances()
    for name, sym_cfg in SYMBOLS.items():
        scan_symbol(name, sym_cfg, inr_bal, usdt_bal)
        time.sleep(0.3)
    time.sleep(2)
