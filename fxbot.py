import time
import hmac
import hashlib
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
import requests
import pandas as pd

# ==========================================
# 1. CREDENTIALS & CONFIGURATION (DELTA ONLY)
# ==========================================
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
ADMIN_CHAT_IDS = ["1345385952"]

# Delta Exchange India Credentials & Base URL
DELTA_BASE_URL = "https://api.india.delta.exchange"
DELTA_API_KEY = "v6itEa7m3KKFwtUsAssZ4pbNqz2glG"
DELTA_API_SECRET = "DPzw2N590faaifL7MhHv2atWz9AljAdtu6GyhXkCx1HdNxJso3zER8Pomkkq"

DEFAULT_INTRADAY_RR = 3.0
MIN_TRADE_USDT = 2.0
MAX_PARALLEL_TRADES = 4
STATE_FILE = "delta_only_state.json"
is_paused = False

SESSION = requests.Session()
EXECUTOR = ThreadPoolExecutor(max_workers=4)

SYMBOLS = {
    "BTC/USDT": {"delta_symbol": "BTCUSD", "binance": "BTCUSDT", "step": 5, "p_dec": 2},
    "ETH/USDT": {"delta_symbol": "ETHUSD", "binance": "ETHUSDT", "step": 4, "p_dec": 2},
    "SOL/USDT": {"delta_symbol": "SOLUSDT", "binance": "SOLUSDT", "step": 3, "p_dec": 2},
    "XRP/USDT": {"delta_symbol": "XRPUSDT", "binance": "XRPUSDT", "step": 1, "p_dec": 4},
    "DOGE/USDT": {"delta_symbol": "DOGEUSDT", "binance": "DOGEUSDT", "step": 0, "p_dec": 5}
}

# ==========================================
# 2. TELEGRAM UI & KEYBOARD
# ==========================================
def get_control_keyboard():
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "📊 Live Terminal", "callback_data": "cmd_status"},
                {"text": "💰 Delta Wallet", "callback_data": "cmd_balance"}
            ],
            [
                {"text": "🔄 Refresh", "callback_data": "cmd_sync_now"},
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
        except Exception:
            pass

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
# 3. DELTA EXCHANGE API ENGINE
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

def get_delta_wallet_balance():
    sec = str(DELTA_API_SECRET) if DELTA_API_SECRET else ""
    if not sec:
        return 0.0, 0.0, "Delta secret is empty"
    try:
        timestamp = str(int(time.time()))
        endpoint = "/v2/wallet/balances"
        method = "GET"
        message = timestamp + method + endpoint + ""
        signature = hmac.new(sec.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        headers = {
            "api-key": str(DELTA_API_KEY), "signature": signature,
            "timestamp": timestamp, "Content-Type": "application/json", "User-Agent": "delta-bot"
        }
        url = f"{DELTA_BASE_URL}{endpoint}"
        res = SESSION.get(url, headers=headers, timeout=6)
        
        if res.status_code != 200:
            return 0.0, 0.0, f"HTTP {res.status_code}: {res.text}"

        data = res.json()
        usdt_bal, inr_bal = 0.0, 0.0
        if isinstance(data, dict):
            result_items = data.get("result", [])
            if isinstance(result_items, list):
                for asset in result_items:
                    sym = str(asset.get("asset_symbol", asset.get("currency", ""))).upper()
                    avail = float(asset.get("available_balance", asset.get("balance", asset.get("equity", 0.0))))
                    if sym in ["USDT", "USD"]:
                        usdt_bal = max(usdt_bal, avail)
                    elif sym in ["INR", "INR_D"]:
                        inr_bal = max(inr_bal, avail)
        return usdt_bal, inr_bal, "SUCCESS"
    except Exception as e:
        return 0.0, 0.0, f"Exception: {str(e)}"

def place_delta_order(product_symbol, side, size):
    timestamp = str(int(time.time()))
    endpoint = "/v2/orders"
    method = "POST"
    payload = json.dumps({"product_symbol": product_symbol, "size": int(size), "side": side.lower(), "order_type": "market_order"})
    message = timestamp + method + endpoint + payload
    signature = hmac.new(str(DELTA_API_SECRET).encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    headers = {
        "api-key": str(DELTA_API_KEY), "signature": signature,
        "timestamp": timestamp, "Content-Type": "application/json", "User-Agent": "delta-bot"
    }
    try:
        res = SESSION.post(f"{DELTA_BASE_URL}{endpoint}", headers=headers, data=payload, timeout=6)
        return res.status_code in [200, 201], res.json()
    except Exception as e:
        return False, {"error": str(e)}

# ==========================================
# 4. STATE & STRATEGY LOGIC
# ==========================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {name: {"side": None, "delta_size": 0, "entry": 0.0, "sl": 0.0, "tp": 0.0} for name in SYMBOLS}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)
    except Exception as e:
        print(f"State save error: {e}")

active_positions = load_state()

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
            pos_lines.append(f"• *{name}* | Size: {pos['delta_size']} | Entry: ${pos['entry']:.2f} | TP: ${pos['tp']:.2f}")
    if not pos_lines:
        return "📊 *DELTA LIVE TERMINAL*\n\n💤 Koi active position open nahi hai. Market scan chal rahi hai."
    return "📊 *ACTIVE DELTA POSITIONS:*\n\n" + "\n".join(pos_lines)

def execute_delta_exit(name, sym_cfg, reason="EXIT"):
    pos = active_positions[name]
    if pos["delta_size"] > 0:
        place_delta_order(sym_cfg["delta_symbol"], "sell", pos["delta_size"])

    send_telegram(f"🚨 *{reason}*\nClosed *{name}* on Delta Exchange!", reply_markup=get_control_keyboard())
    pos["side"] = None
    pos["delta_size"] = 0
    save_state(active_positions)

def scan_symbol(name, sym_cfg, d_usdt):
    global is_paused
    active_count = sum(1 for p in active_positions.values() if p.get("side") is not None)
    if is_paused or active_count >= MAX_PARALLEL_TRADES:
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
            execute_delta_exit(name, sym_cfg, reason="🎯 TARGET HIT")
            return
        elif curr_price <= pos["sl"]:
            execute_delta_exit(name, sym_cfg, reason="🛑 STOP LOSS HIT")
            return

    if pos.get("side") is None and float(df['ema20'].iloc[-1]) > float(df['ema50'].iloc[-1]):
        sl = curr_price * 0.985
        tp = curr_price * 1.035
        delta_contracts = max(1, int(MIN_TRADE_USDT / 1.0))

        delta_ok, _ = place_delta_order(sym_cfg["delta_symbol"], "buy", delta_contracts) if d_usdt >= MIN_TRADE_USDT else (False, None)

        if delta_ok:
            active_positions[name] = {
                "side": "BUY", "delta_size": delta_contracts,
                "entry": curr_price, "sl": sl, "tp": tp
            }
            save_state(active_positions)
            send_telegram(
                f"⚡ *DELTA TRADE OPENED*\n\n"
                f"Asset: `{name}`\n"
                f"• Size: {delta_contracts} Contracts\n"
                f"Entry: ${curr_price:.{sym_cfg['p_dec']}f} | TP: ${tp:.{sym_cfg['p_dec']}f} | SL: ${sl:.{sym_cfg['p_dec']}f}",
                reply_markup=get_control_keyboard()
            )

# ==========================================
# 5. TELEGRAM EVENT HANDLER
# ==========================================
def process_balance_request(sender_id):
    d_usdt, d_inr, debug_msg = get_delta_wallet_balance()
    usdt_rate = get_usdt_inr_rate()
    delta_total = d_inr + (d_usdt * usdt_rate)

    msg = (
        f"🌐 *DELTA EXCHANGE AUDIT*\n\n"
        f"• Available USDT: ${d_usdt:.2f} (~₹{d_usdt * usdt_rate:.2f})\n"
        f"• Available INR: ₹{d_inr:.2f}\n"
        f"• *Total Portfolio:* *₹{delta_total:.2f}*\n\n"
        f"🛠️ *Status:* `{debug_msg}`"
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
                send_telegram("🔄 *Sync Complete:* Delta scanner live active hai.", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_pause":
                is_paused = True
                send_telegram("⏸️ *Scanner Paused.*", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_resume":
                is_paused = False
                send_telegram("▶️ *Scanner Resumed.*", chat_id=sender_id, reply_markup=get_control_keyboard())
            elif data == "cmd_panic":
                for name, cfg in SYMBOLS.items():
                    if active_positions[name].get("side"):
                        execute_delta_exit(name, cfg, reason="🚨 PANIC EXIT")

    elif "message" in update and "text" in update["message"]:
        msg_text = update["message"]["text"].lower().strip()
        sender_id = str(update["message"]["chat"]["id"])

        if sender_id in ADMIN_CHAT_IDS:
            if any(cmd in msg_text for cmd in ["/balance", "/wallets", "wallet", "balance"]):
                process_balance_request(sender_id)
            elif any(cmd in msg_text for cmd in ["/status", "status", "terminal"]):
                send_telegram(generate_status_text(), chat_id=sender_id, reply_markup=get_control_keyboard())
            else:
                send_telegram("🎛️ *DELTA BOT TERMINAL*\nUse buttons below:", chat_id=sender_id, reply_markup=get_control_keyboard())

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
# 6. RUN BOT ENGINE
# ==========================================
threading.Thread(target=instant_telegram_listener, daemon=True).start()

send_telegram(
    "⚡ *Delta Exchange Bot Online*\n\n"
    "• CoinDCX hata diya gaya hai. Ab saari trading seedha Delta par hogi.",
    reply_markup=get_control_keyboard()
)

while True:
    try:
        d_usdt, _, _ = get_delta_wallet_balance()
        for name, sym_cfg in SYMBOLS.items():
            scan_symbol(name, sym_cfg, d_usdt)
            time.sleep(0.3)
    except Exception as e:
        print(f"Cycle error: {e}")
    time.sleep(3)
