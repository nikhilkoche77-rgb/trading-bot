import time
import requests
import yfinance as yf
import pandas as pd
import pandas_ta as ta

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAHk1_s827dXmN..."  # Apna BotFather Token check karein
CHAT_ID = "1345385952"

# Choose Mode: "SCALP" | "SWING" | "POSITION"
CURRENT_MODE = "SCALP"

MODE_CONFIGS = {
    "SCALP": {
        "interval": "1m",
        "period": "1d",
        "adx_threshold": 23,
        "ema_fast": 50,
        "ema_slow": 93,
        "label": "⚡ [1M SCALPING]"
    },
    "SWING": {
        "interval": "1h",
        "period": "1mo",
        "adx_threshold": 20,
        "ema_fast": 20,
        "ema_slow": 50,
        "label": "🌊 [SWING SETUP]"
    },
    "POSITION": {
        "interval": "1d",
        "period": "1y",
        "adx_threshold": 18,
        "ema_fast": 50,
        "ema_slow": 200,
        "label": "🏛️ [POSITION SETUP]"
    }
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
        close = df['Close']
        high = df['High']
        low = df['Low']
        open_p = df['Open']

        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
            high = high.iloc[:, 0]
            low = low.iloc[:, 0]
            open_p = open_p.iloc[:, 0]

        df['ema_fast'] = ta.ema(close, length=CFG["ema_fast"])
        df['ema_slow'] = ta.ema(close, length=CFG["ema_slow"])
        adx_df = ta.adx(high, low, close, length=14)
        df['adx'] = adx_df['ADX_14']

        # SMC Liquidity High/Low (Past 15 candles)
        df['swing_high'] = high.iloc[-16:-1].max()
        df['swing_low'] = low.iloc[-16:-1].min()

        curr_price = float(close.iloc[-1])
        curr_open = float(open_p.iloc[-1])
        curr_fast = float(df['ema_fast'].iloc[-1])
        curr_slow = float(df['ema_slow'].iloc[-1])
        curr_adx = float(df['adx'].iloc[-1])
        swing_h = float(df['swing_high'].iloc[-1])
        swing_l = float(df['swing_low'].iloc[-1])

        print(f"[{CURRENT_MODE}] {name:<14} | Price: {curr_price:<9.2f} | ADX: {curr_adx:.1f}")

        strong_trend = curr_adx > CFG["adx_threshold"]

        # BUY SETUP
        if strong_trend and (curr_fast > curr_slow) and (curr_price > swing_h) and (curr_price > curr_open):
            if last_signals[name] != "BUY":
                sl = swing_l
                risk = curr_price - sl

                if risk > 0:
                    tp1 = curr_price + (risk * 1.5)
                    tp2 = curr_price + (risk * 2.0)
                    tp3 = curr_price + (risk * 3.0)
                    tp4 = curr_price + (risk * 5.0)

                    msg = (
                        f"🚀 *{CFG['label']} BUY ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n"
                        f"📉 *Risk (Per Unit):* `{risk:.2f}`\n\n"
                        f"🎯 *TARGET LEVELS:*\n"
                        f"• TP 1 (1:1.5) : `{tp1:.2f}`\n"
                        f"• TP 2 (1:2.0) : `{tp2:.2f}`\n"
                        f"• TP 3 (1:3.0) : `{tp3:.2f}`\n"
                        f"• TP 4 (1:5.0) : `{tp4:.2f}`\n\n"
                        f"⚡ *ADX Strength:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Timeframe:* `{CFG['interval']}`\n"
                        f"📌 *Logic:* SMC Breakout + EMA Alignment"
                    )
                    send_telegram(msg)
                    last_signals[name] = "BUY"

        # SELL SETUP
        elif strong_trend and (curr_fast < curr_slow) and (curr_price < swing_l) and (curr_price < curr_open):
            if last_signals[name] != "SELL":
                sl = swing_h
                risk = sl - curr_price

                if risk > 0:
                    tp1 = curr_price - (risk * 1.5)
                    tp2 = curr_price - (risk * 2.0)
                    tp3 = curr_price - (risk * 3.0)
                    tp4 = curr_price - (risk * 5.0)

                    msg = (
                        f"⚠️ *{CFG['label']} SELL ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n"
                        f"📈 *Risk (Per Unit):* `{risk:.2f}`\n\n"
                        f"🎯 *TARGET LEVELS:*\n"
                        f"• TP 1 (1:1.5) : `{tp1:.2f}`\n"
                        f"• TP 2 (1:2.0) : `{tp2:.2f}`\n"
                        f"• TP 3 (1:3.0) : `{tp3:.2f}`\n"
                        f"• TP 4 (1:5.0) : `{tp4:.2f}`\n\n"
                        f"⚡ *ADX Strength:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Timeframe:* `{CFG['interval']}`\n"
                        f"📌 *Logic:* SMC Breakdown + EMA Alignment"
                    )
                    send_telegram(msg)
                    last_signals[name] = "SELL"

    except Exception as e:
        print(f"Error scanning {name}: {e}")

print("Multi-Target Bot Activated...")
send_telegram(f"✅ *Multi-Target Scanner Active!*\nMode: `{CURRENT_MODE}`\nTargets configured: `1:1.5 | 1:2 | 1:3 | 1:5`")

while True:
    for name, ticker in SYMBOLS.items():
        check_market(name, ticker)
        time.sleep(0.5)
    print("-" * 50)
    time.sleep(15)
