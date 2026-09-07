import time
import hmac
import hashlib
import json
import os
import threading
import requests
import pandas as pd
import numpy as np

# ==========================================
# 1. DIRECT CONFIGURATION
# ==========================================
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
ADMIN_CHAT_IDS = ["1345385952"]

# CoinDCX Credentials
COINDCX_KEY = "3f4885d2c69c367379c14d146ef67da9743ea6fb92e23409"
COINDCX_SECRET = "b3e23b4021ef0445793ef36ba4b0359a58727d25f7e1aae65f4406df129fda5e"

# Delta Exchange Credentials
DELTA_BASE_URL = "https://api.delta.exchange"
DELTA_API_KEY = "v6itEa7m3KKFwtUsAssZ4pbNqz2glG"
DELTA_API_SECRET = "DPzw2N590faaifL7MhHv2atWz9AljAdtu6GyhXkCx1HdNxJso3zER8Pomkkq"

DEFAULT_INTRADAY_RR = 3.0
WEIGHT_ALLOCATION_PCT = 0.10
MIN_TRADE_INR = 100.0
MIN_TRADE_USDT = 2.0
MAX_TRADE_USDT = 10.0
MAX_PARALLEL_TRADES = 4
STATE_FILE = "dual_trades_state.json"
is_paused = False

SYMBOLS = {
    "BTC/USDT": {"base": "USDT", "coindcx_pair": "B-BTC_USDT", "delta_symbol": "BTCUSD", "binance": "BTCUSDT", "step": 5, "p_dec": 2},
    "ETH/USDT": {"base": "USDT", "coindcx_pair": "B-ETH_USDT", "delta_symbol": "ETHUSD", "binance": "ETHUSDT", "step": 4, "p_dec": 2},
    "SOL/USDT": {"base": "USDT", "coindcx_pair": "B-SOL_USDT", "delta_symbol": "SOLUSDT", "binance": "SOLUSDT", "step": 3, "p_dec": 2},
    "XRP/USDT": {"base": "USDT", "coindcx_pair": "B-XRP_USDT", "delta_symbol": "XRPUSDT", "binance": "XRPUSDT", "step": 1, "p_dec": 4},
    "DOGE/USDT": {"base": "USDT", "coindcx_pair": "B-DOGE_USDT", "delta_symbol": "DOGEUSDT", "binance": "DOGEUSDT", "step": 0, "p_dec": 5},
    "PEPE/USDT": {"base": "USDT", "coindcx_pair": "B-PEPE_USDT", "delta_symbol": "PEPEUSDT", "binance": "PEPEUSDT", "step": 0, "p_dec": 8},
    "SUI/USDT": {"base": "USDT", "coindcx_pair": "B-SUI_USDT", "delta_symbol": "SUIUSDT", "binance": "SUIUSDT", "step": 1, "p_dec": 4},
    "NEAR/USDT": {"base": "USDT", "coindcx_pair": "B-NEAR_USDT", "delta_symbol": "NEARUSDT", "binance": "NEARUSDT", "step": 2, "p_dec": 3},
    "BTC/INR": {"base": "INR", "coindcx_pair": "B-BTC_INR", "delta_symbol": None, "binance": "BTCUSDT", "step": 5, "p_dec": 2},
    "SOL/INR": {"base": "INR", "coindcx_pair": "B-SOL_INR", "delta_symbol": None, "binance": "SOLUSDT", "step": 3, "p_dec": 2}
}

# ==========================================
# 2. ZERO-CRASH SIGNATURE ENGINES
# ==========================================
def coindcx_auth_post(endpoint, body):
    sec = str(COINDCX_SECRET) if COINDCX_SECRET else ""
    if not sec:
        return False, "CoinDCX secret is empty"
    try:
        timeStamp = int(round(time.time() * 1000))
        body["timestamp"] = timeStamp
        json_payload = json.dumps(body, separators=(',', ':'))
        signature = hmac.new(sec.encode('utf-8'), json_payload.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {'Content-Type': 'application/json', 'X-AUTH-APIKEY': str(COINDCX_KEY), 'X-AUTH-SIGNATURE': signature}
        res = requests.post(f"https://api.coindcx.com{endpoint}", data=json_payload, headers=headers, timeout=8)
        return res.status_code == 200, res.json()
    except Exception as e:
        return False, str(e)

def get_coindcx_balances():
    success, data = coindcx_auth_post("/exchange/v1/users/balances", {})
    inr_bal, usdt_bal = 0.0, 0.0
    if success and isinstance(data, list):
        for item in data:
            if item.get("currency") == "INR":
                inr_bal = float(item.get("balance", 0.0))
            elif item.get("currency") == "USDT":
                usdt_bal = float(item.get("balance", 0.0))
    return inr_bal, usdt_bal

def place_coindcx_order(market_pair, side, quantity):
    body = {"side": side.lower(), "order_type": "market_order", "market": market_pair, "total_quantity": quantity}
    return coindcx_auth_post("/exchange/v1/orders/create", body)

def delta_auth_request(method, endpoint, payload=""):
    sec = str(DELTA_API_SECRET) if DELTA_API_SECRET else ""
    if not sec:
        return False, {"error": "Delta secret is empty"}
    try:
        timestamp = str(int(time.time()))
        message = method + timestamp + endpoint + "" + payload
        signature = hmac.new(sec.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {
            "api-key": str(DELTA_API_KEY), "signature": signature,
            "timestamp": timestamp, "Content-Type": "application/json", "User-Agent": "dual-bot"
        }
        url = f"{DELTA_BASE_URL}{endpoint}"
        if method == "GET":
            res = requests.get(url, headers=headers, timeout=8)
        else:
            res = requests.post(url, headers=headers, data=payload, timeout=8)
        return res.status_code in [200, 201], res.json()
    except Exception as e:
        return False, {"error": str(e)}

def get_delta_wallet_balance():
    success, data = delta_auth_request("GET", "/v2/wallet/balances")
    usdt_bal = 0.0
    if success and isinstance(data, dict) and data.get("success"):
        for asset in data.get("result", []):
            if asset.get("asset_symbol") == "USDT":
                usdt_bal = float(asset.get("available_balance", 0.0))
                break
    return usdt_bal

def place_delta_order(product_symbol, side, size):
    payload = json.dumps({"product_symbol": product_symbol, "size": int(size), "side": side.lower(), "order_type": "market_order"})
    return delta_auth_request("POST", "/v2/orders", payload=payload)

# ==========================================
# 3. BINANCE FEED & TRADING LOGIC
# ==========================================
def is_btc_healthy():
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=25"
        resp = requests.get(url, timeout=4).json()
        closes = [float(c[4]) for c in resp]
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
        drop_pct = ((closes[-1] - float(resp[-1][1])) / float(resp[-1][1])) * 100
        return not (drop_pct < -0.85 or closes[-1] < ema20)
    except Exception:
        return True

def check_binance_lead(symbol_binance):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1m&limit=15"
        resp = requests.get(url, timeout=4).json()
        candles = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in resp]
        df = pd.DataFrame(candles, columns=['open', 'high', 'low', 'close', 'volume'])
        last = df.iloc[-1]
        vol_avg = df['volume'].iloc[-6:-1].mean()
        gain = ((last['close'] - last['open']) / last['open']) * 100
        surge = last['volume'] > (vol_avg * 1.8)
        return (gain >= 0.25 and surge), gain, df
    except Exception:
        return False, 0.0, None

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {name: {"side": None, "coindcx_qty": 0.0, "delta_size": 0, "entry": 0.0, "sl": 0.0, "tp": 0.0, "best_price": 0.0, "style": "INTRADAY"} for name in SYMBOLS}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for cid in ADMIN_CHAT_IDS:
        try:
            requests.post(url, json={"chat_id": cid, "text": message}, timeout=8)
        except Exception:
            pass

def scan_symbol(name, sym_cfg, c_inr, c_usdt, d_usdt):
    global is_paused
    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    if is_paused or active_count >= MAX_PARALLEL_TRADES or not is_btc_healthy():
        return

    surging, gain, df = check_binance_lead(sym_cfg["binance"])
    if not surging or df is None or len(df) < 25:
        return

    df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
    curr_price = float(df['close'].iloc[-1])

    if active_positions[name].get("side") is None and float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1]):
        sl = curr_price * 0.985
        tp = curr_price * 1.035

        coindcx_alloc = max(MIN_TRADE_INR if sym_cfg["base"] == "INR" else MIN_TRADE_USDT, 
                            (c_inr if sym_cfg["base"] == "INR" else c_usdt) * WEIGHT_ALLOCATION_PCT)
        cdcx_qty = round(coindcx_alloc / curr_price, sym_cfg["step"]) if sym_cfg["step"] > 0 else int(coindcx_alloc / curr_price)
        delta_contracts = max(1, int(MIN_TRADE_USDT / 1.0)) if sym_cfg["delta_symbol"] else 0

        cdcx_ok, _ = place_coindcx_order(sym_cfg["coindcx_pair"], "buy", cdcx_qty) if cdcx_qty > 0 else (False, None)
        delta_ok, _ = place_delta_order(sym_cfg["delta_symbol"], "buy", delta_contracts) if delta_contracts > 0 else (False, None)

        if cdcx_ok or delta_ok:
            active_positions[name] = {
                "side": "BUY", "coindcx_qty": cdcx_qty if cdcx_ok else 0.0,
                "delta_size": delta_contracts if delta_ok else 0,
                "entry": curr_price, "sl": sl, "tp": tp, "best_price": curr_price,
                "style": "🎯 INTRADAY"
            }
            save_state(active_positions)
            send_telegram(
                f"⚡ PARALLEL TRADE OPENED\n\n"
                f"Pair: {name}\n"
                f"• CoinDCX Spot: {'✅ Filled' if cdcx_ok else '❌ Skipped'}\n"
                f"• Delta Futures: {'✅ Filled' if delta_ok else '❌ Skipped'}\n"
                f"Entry: ${curr_price:.{sym_cfg['p_dec']}f} | Target: ${tp:.{sym_cfg['p_dec']}f}"
            )

send_telegram("🔥 Zero-Crash Dual Engine Online! Live scanning CoinDCX & Delta Exchange.")

# ==========================================
# 4. MAIN LOOP
# ==========================================
while True:
    try:
        c_inr, c_usdt = get_coindcx_balances()
        d_usdt = get_delta_wallet_balance()
        for name, sym_cfg in SYMBOLS.items():
            scan_symbol(name, sym_cfg, c_inr, c_usdt, d_usdt)
            time.sleep(0.3)
    except Exception as e:
        print(f"Cycle error: {e}")
    time.sleep(3)
