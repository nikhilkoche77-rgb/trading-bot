import time
import threading
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from g4f.client import Client

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
MY_CHAT_ID = "1345385952"

# Free AI Client (No Key, No Payment Needed)
ai_client = Client()

CURRENT_MODE = "SCALP"

MODE_CONFIGS = {
    "SCALP": {
        "entry_interval": "1m", 
        "entry_period": "1d", 
        "htf_interval": "15m", 
        "htf_period": "5d", 
        "adx_min": 23, 
        "label": "⚡ [1M+15M SCALP]"
    },
    "SWING": {
        "entry_interval": "1h", 
        "entry_period": "1mo", 
        "htf_interval": "1d", 
        "htf_period": "1y", 
        "adx_min": 20, 
        "label": "🌊 [SWING]"
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

active_positions = {
    name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0} 
    for name in SYMBOLS
}

def send_telegram(message, chat_id=MY_CHAT_ID):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# --- 100% FREE AI TRADING BRAIN ---
def get_free_ai_trading_reply(user_question):
    sys_prompt = (
        "You are an expert Wall Street Algorithmic Trader. "
        "Reply only to technical analysis, indicators, stock market, forex, crypto, and trading strategy questions in simple natural Hinglish. "
        "Keep answers concise (max 3-4 bullet points). If the question is not about finance/trading, decline politely."
    )
    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_question}
            ],
            web_search=False
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Free AI Error: {e}")
        return "Filhal AI engine processing me hai, kripya 1 minute baad dobara sawal bhejein."

# --- TELEGRAM INCOMING LISTENER ---
def telegram_listener():
    last_update_id = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 20}
            resp = requests.get(url, params=params, timeout=25).json()

            if "result" in resp:
                for update in resp["result"]:
                    last_update_id = update["update_id"]
                    if "message" in update and "text" in update["message"]:
                        msg_text = update["message"]["text"].strip()
                        sender_id = str(update["message"]["chat"]["id"])
                        user_name = update["message"]["from"].get("first_name", "Trader")

                        print(f"Query from {user_name}: {msg_text}")

                        # Admin Command
                        if sender_id == MY_CHAT_ID and msg_text in ["/start", "status"]:
                            reply = (
                                f"👋 Welcome Boss ({user_name})!\n\n"
                                f"🤖 System Status: Online 24/7\n"
                                f"⚙️ Mode: {CFG['label']}\n"
                                f"📊 Monitored: {len(SYMBOLS)} Pairs\n"
                                f"🧠 Free AI Trading Assistant: Active"
                            )
                            send_telegram(reply, chat_id=sender_id)

                        # Public / Any Trading Query
                        else:
                            if msg_text == "/start":
                                welcome_msg = (
                                    f"Hello {user_name}! 👋\n\n"
                                    f"Main ek AI Trading Assistant aur Quantitative Engine hoon. "
                                    f"Aap mujhse Trading, Indicators, Risk Management, ya Price Action "
                                    f"se juda koi bhi sawal pooch sakte hain!"
                                )
                                send_telegram(welcome_msg, chat_id=sender_id)
                            else:
                                ai_reply = get_free_ai_trading_reply(msg_text)
                                send_telegram(ai_reply, chat_id=sender_id)

        except Exception as e:
            print(f"Listener error: {e}")
        time.sleep(1)

def calculate_atr_and_adx(df, length=14):
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
    return atr, adx

def get_higher_timeframe_trend(ticker_symbol):
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
    except Exception:
        return "NEUTRAL"

def manage_trailing_sl(name, curr_price):
    pos = active_positions[name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5

    if pos["side"] == "BUY":
        if curr_price > pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price - trailing_gap
            if new_sl > pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(f"🛡️ TRAILING SL UPDATED (BUY)\n🪙 Asset: {name}\n📈 High: {pos['best_price']:.2f}\n🛑 New SL: {pos['sl']:.2f}")
        elif curr_price <= pos["sl"]:
            send_telegram(f"🔴 EXIT HIT (STOP LOSS)\n🪙 Asset: {name}\n💵 Exit: {curr_price:.2f}\n🛑 SL Hit: {pos['sl']:.2f}")
            pos["side"] = None

    elif pos["side"] == "SELL":
        if curr_price < pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price + trailing_gap
            if new_sl < pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(f"🛡️ TRAILING SL UPDATED (SELL)\n🪙 Asset: {name}\n📉 Low: {pos['best_price']:.2f}\n🛑 New SL: {pos['sl']:.2f}")
        elif curr_price >= pos["sl"]:
            send_telegram(f"🔴 EXIT HIT (STOP LOSS)\n🪙 Asset: {name}\n💵 Exit: {curr_price:.2f}\n🛑 SL Hit: {pos['sl']:.2f}")
            pos["side"] = None

def check_market(name, ticker_symbol):
    try:
        htf_trend = get_higher_timeframe_trend(ticker_symbol)

        data = yf.download(ticker_symbol, period=CFG["entry_period"], interval=CFG["entry_interval"], progress=False)
        if data is None or len(data) < 50:
            return

        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        for col in ['Open', 'High', 'Low', 'Close']:
            if isinstance(df[col], pd.DataFrame):
                df[col] = df[col].iloc[:, 0]

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        df['ema50'] = close_series.ewm(span=50, adjust=False).mean()
        df['ema93'] = close_series.ewm(span=93, adjust=False).mean()
        df['atr'], df['adx'] = calculate_atr_and_adx(df)

        df['swing_high'] = df['High'].iloc[-16:-1].max()
        df['swing_low'] = df['Low'].iloc[-16:-1].min()

        curr_price = float(df['Close'].iloc[-1])
        curr_open = float(df['Open'].iloc[-1])
        curr_ema50 = float(df['ema50'].iloc[-1])
        curr_ema93 = float(df['ema93'].iloc[-1])
        curr_adx = float(df['adx'].iloc[-1]) if not pd.isna(df['adx'].iloc[-1]) else 0.0
        curr_atr = float(df['atr'].iloc[-1]) if not pd.isna(df['atr'].iloc[-1]) else 0.0
        swing_h = float(df['swing_high'].iloc[-1])
        swing_l = float(df['swing_low'].iloc[-1])

        manage_trailing_sl(name, curr_price)

        strong_trend = curr_adx > CFG["adx_min"]
        active_pos = active_positions[name]["side"]

        # BUY SETUP
        if (active_pos is None) and (htf_trend == "BULLISH") and strong_trend and (curr_ema50 > curr_ema93) and (curr_price > swing_h) and (curr_price > curr_open):
            atr_sl = curr_price - (curr_atr * 1.5)
            sl = max(swing_l, atr_sl)
            risk = curr_price - sl

            if risk > 0:
                active_positions[name] = {"side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr}
                msg = (
                    f"🚀 {CFG['label']} BUY ALERT\n\n"
                    f"🪙 Asset: {name}\n"
                    f"📈 HTF Trend: 15M Bullish\n"
                    f"💵 Entry: {curr_price:.2f}\n"
                    f"🛑 Dynamic SL: {sl:.2f}\n"
                    f"📊 ATR: {curr_atr:.2f}\n\n"
                    f"🎯 TARGETS:\n"
                    f"• TP 1: {curr_price + (risk * 1.5):.2f}\n"
                    f"• TP 2: {curr_price + (risk * 2.0):.2f}\n"
                    f"• TP 3: {curr_price + (risk * 3.0):.2f}\n"
                    f"• TP 4: {curr_price + (risk * 5.0):.2f}\n\n"
                    f"⚡ ADX: {curr_adx:.1f}"
                )
                send_telegram(msg)

        # SELL SETUP
        elif (active_pos is None) and (htf_trend == "BEARISH") and strong_trend and (curr_ema50 < curr_ema93) and (curr_price < swing_l) and (curr_price < curr_open):
            atr_sl = curr_price + (curr_atr * 1.5)
            sl = min(swing_h, atr_sl)
            risk = sl - curr_price

            if risk > 0:
                active_positions[name] = {"side": "SELL", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr}
                msg = (
                    f"⚠️ {CFG['label']} SELL ALERT\n\n"
                    f"🪙 Asset: {name}\n"
                    f"📉 HTF Trend: 15M Bearish\n"
                    f"💵 Entry: {curr_price:.2f}\n"
                    f"🛑 Dynamic SL: {sl:.2f}\n"
                    f"📊 ATR: {curr_atr:.2f}\n\n"
                    f"🎯 TARGETS:\n"
                    f"• TP 1: {curr_price - (risk * 1.5):.2f}\n"
                    f"• TP 2: {curr_price - (risk * 2.0):.2f}\n"
                    f"• TP 3: {curr_price - (risk * 3.0):.2f}\n"
                    f"• TP 4: {curr_price - (risk * 5.0):.2f}\n\n"
                    f"⚡ ADX: {curr_adx:.1f}"
                )
                send_telegram(msg)

    except Exception as e:
        print(f"Error on {name}: {e}")

# Run Free AI Listener
listener_thread = threading.Thread(target=telegram_listener, daemon=True)
listener_thread.start()

print("Zero-Cost AI Scanner Live...")
send_telegram("🔥 Zero-Cost Free AI Engine Live 24/7!")

while True:
    for name, ticker in SYMBOLS.items():
        check_market(name, ticker)
        time.sleep(2)
    time.sleep(10)
