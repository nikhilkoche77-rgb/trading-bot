import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAHk1_s827dXmN..."  # Apna poora token verify karein
CHAT_ID = "1345385952"

# "SCALP" (1m) | "SWING" (1h) | "POSITION" (1d)
CURRENT_MODE = "SCALP"

MODE_CONFIGS = {
    "SCALP": {"interval": "1m", "period": "1d", "adx_min": 23, "label": "⚡ [1M SCALP]"},
    "SWING": {"interval": "1h", "period": "1mo", "adx_min": 20, "label": "🌊 [SWING]"},
    "POSITION": {"interval": "1d", "period": "1y", "adx_min": 18, "label": "🏛️ [POSITION]"}
}

CFG = MODE_CONFIGS[CURRENT_MODE]

SYMBOLS = {
    "XAU/USD (Gold)": "GC=F",
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "Crude Oil": "CL=F",
    "Silver": "SI=F"
}

last_signals = {name: None for name in SYMBOLS}

def calculate_adx(df, length=14):
    high, low, close = df['High'], df['Low'], df['Close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(length).mean()

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100 * (pd.Series(plus_dm).rolling(length).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm).rolling(length).mean() / atr)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(length).mean()
    return adx

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def check_market(name, ticker_symbol):
    try:
        data = yf.download(ticker_symbol, period=CFG["period"], interval=CFG["interval"], progress=False)
        if len(data) < 100:
            return

        df = data.copy()
        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        # Indicators Calculation
        df['ema50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['ema93'] = df['Close'].ewm(span=93, adjust=False).mean()
        df['adx'] = calculate_adx(df)

        df['swing_high'] = df['High'].iloc[-16:-1].max()
        df['swing_low'] = df['Low'].iloc[-16:-1].min()

        curr_price = float(df['Close'].iloc[-1])
        curr_open = float(df['Open'].iloc[-1])
        curr_ema50 = float(df['ema50'].iloc[-1])
        curr_ema93 = float(df['ema93'].iloc[-1])
        curr_adx = float(df['adx'].iloc[-1]) if not pd.isna(df['adx'].iloc[-1]) else 0.0
        swing_h = float(df['swing_high'].iloc[-1])
        swing_l = float(df['swing_low'].iloc[-1])

        print(f"[{CURRENT_MODE}] {name:<14} | Price: {curr_price:<9.2f} | ADX: {curr_adx:.1f}")

        strong_trend = curr_adx > CFG["adx_min"]

        # BUY SETUP
        if strong_trend and (curr_ema50 > curr_ema93) and (curr_price > swing_h) and (curr_price > curr_open):
            if last_signals[name] != "BUY":
                sl = swing_l
                risk = curr_price - sl
                if risk > 0:
                    msg = (
                        f"🚀 *{CFG['label']} BUY ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n\n"
                        f"🎯 *TARGETS:*\n"
                        f"• TP 1 (1:1.5): `{curr_price + (risk * 1.5):.2f}`\n"
                        f"• TP 2 (1:2.0): `{curr_price + (risk * 2.0):.2f}`\n"
                        f"• TP 3 (1:3.0): `{curr_price + (risk * 3.0):.2f}`\n"
                        f"• TP 4 (1:5.0): `{curr_price + (risk * 5.0):.2f}`\n\n"
                        f"⚡ *ADX:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Timeframe:* `{CFG['interval']}`"
                    )
                    send_telegram(msg)
                    last_signals[name] = "BUY"

        # SELL SETUP
        elif strong_trend and (curr_ema50 < curr_ema93) and (curr_price < swing_l) and (curr_price < curr_open):
            if last_signals[name] != "SELL":
                sl = swing_h
                risk = sl - curr_price
                if risk > 0:
                    msg = (
                        f"⚠️ *{CFG['label']} SELL ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n\n"
                        f"🎯 *TARGETS:*\n"
                        f"• TP 1 (1:1.5): `{curr_price - (risk * 1.5):.2f}`\n"
                        f"• TP 2 (1:2.0): `{curr_price - (risk * 2.0):.2f}`\n"
                        f"• TP 3 (1:3.0): `{curr_price - (risk * 3.0):.2f}`\n"
                        f"• TP 4 (1:5.0): `{curr_price - (risk * 5.0):.2f}`\n\n"
                        f"⚡ *ADX:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Timeframe:* `{CFG['interval']}`"
                    )
                    send_telegram(msg)
                    last_signals[name] = "SELL"

    except Exception as e:
        print(f"Error on {name}: {e}")

print("Cloud Bot Online...")
send_telegram(f"✅ *Cloud Scalper Live 24/7!*\nTargets: 1:1.5 | 1:2 | 1:3 | 1:5")

while True:
    for name, ticker in SYMBOLS.items():
        check_market(name, ticker)
        time.sleep(0.5)
    print("-" * 50)
    time.sleep(15)
