import time
import hmac
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd
import numpy as np

# ==========================================
# 1. CREDENTIALS & DIRECT CONFIGURATION
# ==========================================
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
ADMIN_CHAT_IDS = ["1345385952"]

# CoinDCX Credentials
COINDCX_KEY = "3f4885d2c69c367379c14d146ef67da9743ea6fb92e23409"
COINDCX_SECRET = "b3e23b4021ef0445793ef36ba4b0359a58727d25f7e1aae65f4406df129fda5e"

# Delta Exchange India Correct Base URL
DELTA_BASE_URL = "https://api.india.delta.exchange"
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

SESSION = requests.Session()
EXECUTOR = ThreadPoolExecutor(max_workers=8)

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
# 2. INSTANT TELEGRAM UI
# ==========================================
def get_control_keyboard():
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Live Terminal", "callback_data": "cmd_status"},
                {"text": "💰 Wallets (INR)", "callback_data": "cmd_balance"}
            ],
            [
                {"text": "🔄 Refresh / Sync", "callback_data": "cmd_sync_now"},
                {"text": "⏸️ Pause", "callback_data": "cmd_pause"},
                {"text": "▶️ Resume", "callback_data": "cmd_resume"}
            ],
            [
                {"text": "🚨 Panic Exit (Close All)", "callback_data": "cmd_panic"}
            ]
        ]
    }
    return json.dumps(keyboard)

def send_telegram(message, chat_id=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    recipients = [chat_id] if chat_id else ADMIN_CHAT_IDS
    for cid in recipients:
        payload = {"chat_id": cid, "text": message, "parse_mode": "Markdown"}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        try:
            SESSION.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram error: {e}")

def answer_callback(cb_id, text=None):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery"
        payload = {"callback_query_id": cb_id}
        if text:
            payload["text"] = text
        SESSION.post(url, json=payload, timeout=3)
    except Exception:
        pass

# ==========================================
# 3. EXCHANGE API ENGINES & LIVE RATES
# ==========================================
def get_usdt_inr_rate():
    try:
        url = "https://api.coindcx.com/exchange/ticker"
        res = SESSION.get(url, timeout=4).json()
        if isinstance(res, list):
            for t in res:
                if t.get("market") == "USDTINR":
                    return float(t.get("last_price", 90.0))
    except Exception:
        pass
    return 90.0

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
        res = SESSION.post(f"https://api.coindcx.com{endpoint}", data=json_payload, headers=headers, timeout=6)
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
            res = SESSION.get(url, headers=headers, timeout=6)
        else:
            res = SESSION.post(url, headers=headers, data=payload, timeout=6)
        return res.status_code in [200, 201], res.json()
    except Exception as e:
        return False, {"error": str(e)}

def get_delta_wallet_balance():
    success, data = delta_auth_request("GET", "/v2/wallet/balances")
    usdt_bal = 0.0
    inr_bal = 0.0
    if success and isinstance(data, dict) and data.get("success"):
        for asset in data.get("result", []):
            sym = asset.get("asset_symbol", "").upper()
            avail = float(asset.get("available_balance", 0.0))
            if sym == "USDT":
                usdt_bal = avail
            elif sym in ["INR", "INR_D"]:
                inr_bal = avail
    return usdt_bal, inr_bal

def place_delta_order(product_symbol, side, size):
    payload = json.dumps({"product_symbol": product_symbol, "size": int(size), "side": side.lower(), "order_type": "market_order"})
    return delta_auth_request("POST", "/v2/orders", payload=payload)

# ==========================================
# 4. STATE & CORE LOGIC
# ==========================================
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

def is_btc_healthy():
    try:
        url = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=25"
        resp = SESSION.get(url, timeout=3).json()
        closes = [float(c[4]) for c in resp]
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().iloc[-1]
        drop_pct = ((closes[-1] - float(resp[-1][1])) / float(resp[-1][1])) * 100
        return not (drop_pct < -0.85 or closes[-1] < ema20)
    except Exception:
        return True

def check_binance_lead(symbol_binance):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol_binance}&interval=1m&limit=15"
        resp = SESSION.get(url, timeout=3).json()
        candles = [[float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])] for c in resp]
        df = pd.DataFrame(candles, columns=['open', 'high', 'low', 'close', 'volume'])
        last = df.iloc[-1]
        vol_avg = df['volume'].iloc[-6:-1].mean()
        gain = ((last['close'] - last['open']) / last['open']) * 100
        surge = last['volume'] > (vol_avg * 1.8)
        return (gain >= 0.25 and surge), gain, df
    except Exception:
        return False, 0.0, None

def generate_status_text():
    pos_lines = []
    for name, pos in active_positions.items():
        if pos.get("side"):
            pos_lines.append(f"• *{name}* | Entry: ${pos['entry']:.2f} | SL: ${pos['sl']:.2f} | TP: ${pos['tp']:.2f}")
    if not pos_lines:
        return "📊 *LIVE TERMINAL*\n\n💤 Koi active position open nahi hai. Market scan chal rahi hai."
    return "📊 *ACTIVE POSITIONS:*\n\n" + "\n".join(pos_lines)

def execute_dual_exit(name, sym_cfg, reason="EXIT"):
    pos = active_positions[name]
    if pos["coindcx_qty"] > 0:
        place_coindcx_order(sym_cfg["coindcx_pair"], "sell", pos["coindcx_qty"])
    if pos["delta_size"] > 0 and sym_cfg["delta_symbol"]:
        place_delta_order(sym_cfg["delta_symbol"], "sell", pos["delta_size"])

    send_telegram(f"🚨 *{reason}*\nClosed *{name}* on both CoinDCX & Delta!", reply_markup=get_control_keyboard())
    pos["side"] = None
    pos["coindcx_qty"] = 0.0
    pos["delta_size"] = 0
    save_state(active_positions)

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

    pos = active_positions[name]
    if pos.get("side"):
        if curr_price >= pos["tp"]:
            execute_dual_exit(name, sym_cfg, reason="🎯 TARGET HIT")
            return
        elif curr_price <= pos["sl"]:
            execute_dual_exit(name, sym_cfg, reason="🛑 STOP LOSS HIT")
            return

    if pos.get("side") is None and float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1]):
        sl = curr_price * 0.985
        tp = curr_price * 1.035

        coindcx_alloc = max(MIN_TRADE_INR if sym_cfg["base"] == "INR" else MIN_TRADE_USDT, 
                            (c_inr if sym_cfg["base"] == "INR" else c_usdt) * WEIGHT_ALLOCATION_PCT)
        cdcx_qty = round(coindcx_alloc / curr_price, sym_cfg["step"]) if sym_cfg["step"] > 0 else int(coindcx_alloc / curr_price)
        delta_contracts = max(1, int(MIN_TRADE_USDT / 1.0)) if sym_cfg["delta_symbol"] else 0

        cdcx_ok, _ = place_coindcx_order(sym_cfg["coindcx_pair"], "buy", cdcx_qty) if cdcx_qty > 0 else (False, None)
        delta_ok, _ = place_delta_order(sym_cfg["delta_symbol"], "buy", delta_contracts) if (delta_contracts > 0 and d_usdt >= MIN_TRADE_USDT) else (False, None)

        if cdcx_ok or delta_ok:
            active_positions[name] = {
                "side": "BUY", "coindcx_qty": cdcx_qty if cdcx_ok else 0.0,
                "delta_size": delta_contracts if delta_ok else 0,
                "entry": curr_price, "sl": sl, "tp": tp, "best_price": curr_price,
                "style": "🎯 INTRADAY"
            }
            save_state(active_positions)
            send_telegram(
                f"⚡ *PARALLEL TRADE OPENED*\n\n"
                f"Asset: `{name}`\n"
                f"• CoinDCX: {'✅ ' + str(cdcx_qty) + ' Units' if cdcx_ok else '❌ Skipped'}\n"
                f"• Delta: {'✅ ' + str(delta_contracts) + ' Contracts' if delta_ok else '❌ Skipped'}\n"
                f"Entry: ${curr_price:.{sym_cfg['p_dec']}f} | TP: ${tp:.{sym_cfg['p_dec']}f} | SL: ${sl:.{sym_cfg['p_dec']}f}",
                reply_markup=get_control_keyboard()
            )

# ==========================================
# 5. ASYNC INSTANT WORKER FOR TELEGRAM
# ==========================================
def process_balance_request(sender_id):
    c_inr, c_usdt = get_coindcx_balances()
    d_usdt, d_inr = get_delta_wallet_balance()
    usdt_rate = get_usdt_inr_rate()

    coindcx_total = c_inr + (c_usdt * usdt_rate)
    delta_total = d_inr + (d_usdt * usdt_rate)

    inr_warning = ""
    if d_inr > 0 and d_usdt < 1.0:
        inr_warning = "\n⚠️ *Delta Note:* Aapka balance INR me hai. App me jakar *Convert to USDT* karein."

    msg = (
        f"💰 *LIVE WALLETS AUDIT (IN INR)*\n\n"
        f"🇮🇳 *CoinDCX Wallet:*\n"
        f"• Available INR: ₹{c_inr:.2f}\n"
        f"• Available USDT: ${c_usdt:.2f} (~₹{c_usdt * usdt_rate:.2f})\n"
        f"• *Total CoinDCX Value:* *₹{coindcx_total:.2f}*\n\n"
        f"🌐 *Delta Exchange India:*\n"
        f"• Available USDT: ${d_usdt:.2f} (~₹{d_usdt * usdt_rate:.2f})\n"
        f"• Available INR: ₹{d_inr:.2f}\n"
        f"• *Total Delta Value:* *₹{delta_total:.2f}*{inr_warning}"
    )
    send_telegram(msg, chat_id=sender_id, reply_markup=get_control_keyboard())

def process_telegram_event(update):
    global is_paused
    if "callback_query" in update:
        cb = update["callback_query"]
        cb_id = cb["id"]
        data = cb.get("data")
        sender_id = str(cb["from"]["id"])

        if sender_id in ADMIN_CHAT_IDS:
            answer_callback(cb_id)

            if data == "cmd_balance":
                process_balance_request(sender_id)
            elif data == "cmd_status":
                send_telegram(generate_status_text(), chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_sync_now":
                send_telegram("🔄 *Sync Complete:* System live aur scan active hai.", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_pause":
                is_paused = True
                send_telegram("⏸️ *Scanner Paused:* Naye orders nahi liye jayenge.", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_resume":
                is_paused = False
                send_telegram("▶️ *Scanner Resumed:* Live parallel scan active ho gayi hai.", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_panic":
                for name, cfg in SYMBOLS.items():
                    if active_positions[name].get("side"):
                        execute_dual_exit(name, cfg, reason="🚨 PANIC EXIT")

    elif "message" in update and "text" in update["message"]:
        msg_text = update["message"]["text"].lower().strip()
        sender_id = str(update["message"]["chat"]["id"])

        if sender_id in ADMIN_CHAT_IDS:
            if any(cmd in msg_text for cmd in ["/balance", "/wallets", "wallet", "balance"]):
                process_balance_request(sender_id)
            elif any(cmd in msg_text for cmd in ["/status", "status", "terminal"]):
                send_telegram(generate_status_text(), chat_id=sender_id, reply_markup=get_control_keyboard())
            else:
                send_telegram("🎛️ *COMMAND TERMINAL ACTIVE*\nNeeche buttons se operate karein:", chat_id=sender_id, reply_markup=get_control_keyboard())

def instant_telegram_listener():
    last_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            res = SESSION.get(url, params={"offset": last_id + 1, "timeout": 25}, timeout=30).json()
            for update in res.get("result", []):
                last_id = update["update_id"]
                EXECUTOR.submit(process_telegram_event, update)
        except Exception:
            time.sleep(1)

# ==========================================
# 6. START BOT & LOOP
# ==========================================
threading.Thread(target=instant_telegram_listener, daemon=True).start()

send_telegram(
    "⚡ *Dual Engine Online (Delta India API Updated)*\n\n"
    "• Correct API endpoint `https://api.india.delta.exchange` configured.\n"
    "Neeche button dabakar balance check karein:",
    reply_markup=get_control_keyboard()
)

while True:
    try:
        c_inr, c_usdt = get_coindcx_balances()
        d_usdt, _ = get_delta_wallet_balance()
        for name, sym_cfg in SYMBOLS.items():
            scan_symbol(name, sym_cfg, c_inr, c_usdt, d_usdt)
            time.sleep(0.3)
    except Exception as e:
        print(f"Cycle error: {e}")
    time.sleep(3)
