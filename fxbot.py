import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
CHAT_ID = "1345385952"

# "SCALP" (1m entry + 15m HTF) | "SWING" (1h entry + 1d HTF)
CURRENT_MODE = "SCALP"

MODE_CONFIGS = {
    "SCALP": {
        "entry_interval": "1m", 
        "entry_period": "1d", 
        "htf_interval": "15m", 
        "htf_period": "5d", 
        "adx_min": 23, 
        "label": "⚡ [1M+15M SCALP CONFIRMED]"
    },
    "SWING": {
        "entry_interval": "1h", 
        "entry_period": "1mo", 
        "htf_interval": "1d", 
        "htf_period": "1y", 
        "adx_min": 20, 
        "label": "🌊 [SWING CONFIRMED]"
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

def calculate_adx(df, length=14):
    high = pd.Series(np.array(df['High']).flatten(), index=df.index)
    low = pd.Series(np.array(df['Low']).flatten(), index=df.index)
    close = pd.Series(np.array(df['Close']).flatten(), index=df.index)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(length).mean()

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0).flatten()
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0).flatten()

    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(length).mean() / atr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(length).mean() / atr)

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

def get_higher_timeframe_trend(ticker_symbol):
    """Checks 15M (or 1D) Higher Timeframe Trend using EMA 50 & EMA 200"""
    try:
        htf_data = yf.download(ticker_symbol, period=CFG["htf_period"], interval=CFG["htf_interval"], progress=False)
        if htf_data is None or len(htf_data) < 50:
            return "NEUTRAL"

        df_htf = htf_data.copy()
        if isinstance(df_htf.columns, pd.MultiIndex):
            df_htf.columns = df_htf.columns.get_level_values(0)

        close_series = pd.Series(np.array(df_htf['Close']).flatten(), index=df_htf.index)
        ema50 = close_series.ewm(span=50, adjust=False).mean()
        ema200 = close_series.ewm(span=min(200, len(close_series)), adjust=False).mean()

        last_close = float(close_series.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])
        last_ema200 = float(ema200.iloc[-1])

        if (last_close > last_ema50) and (last_ema50 > last_ema200):
            return "BULLISH"
        elif (last_close < last_ema50) and (last_ema50 < last_ema200):
            return "BEARISH"
        return "NEUTRAL"
    except Exception as e:
        print(f"HTF Error: {e}")
        return "NEUTRAL"

def check_market(name, ticker_symbol):
    try:
        # 1. Higher Timeframe Confluence Check
        htf_trend = get_higher_timeframe_trend(ticker_symbol)

        # 2. Lower Timeframe Download
        data = yf.download(ticker_symbol, period=CFG["entry_period"], interval=CFG["entry_interval"], progress=False)
        if data is None or len(data) < 50:
            return

        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        # Indicators Calculation
        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        df['ema50'] = close_series.ewm(span=50, adjust=False).mean()
        df['ema93'] = close_series.ewm(span=93, adjust=False).mean()
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

        print(f"[{CURRENT_MODE}] {name:<14} | Price: {curr_price:<9.2f} | ADX: {curr_adx:.1f} | HTF Trend: {htf_trend}")

        strong_trend = curr_adx > CFG["adx_min"]

        # BUY SETUP (HTF Must be BULLISH)
        if (htf_trend == "BULLISH") and strong_trend and (curr_ema50 > curr_ema93) and (curr_price > swing_h) and (curr_price > curr_open):
            if last_signals[name] != "BUY":
                sl = swing_l
                risk = curr_price - sl
                if risk > 0:
                    msg = (
                        f"🚀 *{CFG['label']} BUY ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"📈 *HTF Confluence:* `15M Bullish Verified`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n\n"
                        f"🎯 *TARGETS:*\n"
                        f"• TP 1 (1:1.5): `{curr_price + (risk * 1.5):.2f}`\n"
                        f"• TP 2 (1:2.0): `{curr_price + (risk * 2.0):.2f}`\n"
                        f"• TP 3 (1:3.0): `{curr_price + (risk * 3.0):.2f}`\n"
                        f"• TP 4 (1:5.0): `{curr_price + (risk * 5.0):.2f}`\n\n"
                        f"⚡ *ADX:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Entry TF:* `{CFG['entry_interval']}` | *Trend TF:* `{CFG['htf_interval']}`"
                    )
                    send_telegram(msg)
                    last_signals[name] = "BUY"

        # SELL SETUP (HTF Must be BEARISH)
        elif (htf_trend == "BEARISH") and strong_trend and (curr_ema50 < curr_ema93) and (curr_price < swing_l) and (curr_price < curr_open):
            if last_signals[name] != "SELL":
                sl = swing_h
                risk = sl - curr_price
                if risk > 0:
                    msg = (
                        f"⚠️ *{CFG['label']} SELL ALERT*\n\n"
                        f"🪙 *Asset:* `{name}`\n"
                        f"📉 *HTF Confluence:* `15M Bearish Verified`\n"
                        f"💵 *Entry:* `{curr_price:.2f}`\n"
                        f"🛑 *Stop Loss:* `{sl:.2f}`\n\n"
                        f"🎯 *TARGETS:*\n"
                        f"• TP 1 (1:1.5): `{curr_price - (risk * 1.5):.2f}`\n"
                        f"• TP 2 (1:2.0): `{curr_price - (risk * 2.0):.2f}`\n"
                        f"• TP 3 (1:3.0): `{curr_price - (risk * 3.0):.2f}`\n"
                        f"• TP 4 (1:5.0): `{curr_price - (risk * 5.0):.2f}`\n\n"
                        f"⚡ *ADX:* `{curr_adx:.1f}`\n"
                        f"⏱️ *Entry TF:* `{CFG['entry_interval']}` | *Trend TF:* `{CFG['htf_interval']}`"
                    )
                    send_telegram(msg)
                    last_signals[name] = "SELL"

    except Exception as e:
        print(f"Error on {name}: {e}")

print("Multi-Timeframe Bot Online...")
send_telegram("🔥 *Multi-Timeframe Confluence Engine Live!*\nFilters: 15M Trend + 1M Breakout")

while True:
    for name, ticker in SYMBOLS.items():
        check_market(name, ticker)
        time.sleep(2)
    time.sleep(10)
