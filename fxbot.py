import time
import threading
import requests
import yfinance as yf
import pandas as pd
import numpy as np

# --- TELEGRAM CONFIG ---
TELEGRAM_TOKEN = "8991028193:AAGzmceXw5nsDjHS25D_oboo-bnbr2vvmzw"
MY_CHAT_ID = "1345385952"

# Multi-Timeframe Strategy Configurations
STRATEGIES = {
    "SCALP": {
        "label": "⚡ [SCALP - 15M/5M/1M]",
        "htf_interval": "15m",
        "htf_period": "5d",
        "mtf_interval": "5m",
        "mtf_period": "5d",
        "entry_interval": "1m",
        "entry_period": "1d",
        "adx_min": 23,
        "tp_multipliers": [1.5, 2.0, 3.0, 5.0]
    },
    "INTRADAY": {
        "label": "⏱️ [INTRADAY - 1D/4H/1H to 5M/15M]",
        "htf_interval": "1d",
        "htf_period": "60d",
        "mtf_interval": "1h",
        "mtf_period": "30d",
        "entry_interval": "5m",
        "entry_period": "5d",
        "adx_min": 22,
        "tp_multipliers": [1.5, 2.5, 3.5, 5.0]
    },
    "SWING": {
        "label": "🌊 [SWING - 1W/1D to 1H]",
        "htf_interval": "1wk",
        "htf_period": "2y",
        "mtf_interval": "1d",
        "mtf_period": "1y",
        "entry_interval": "1h",
        "entry_period": "1mo",
        "adx_min": 20,
        "tp_multipliers": [2.0, 3.0, 4.5, 6.0]
    }
}

# 24/7 Active Pairs
SYMBOLS = {
    "BTC/USD": "BTC-USD",
    "ETH/USD": "ETH-USD",
    "SOL/USD": "SOL-USD",
    "BNB/USD": "BNB-USD",
    "XRP/USD": "XRP-USD",
    "ADA/USD": "ADA-USD",
    "DOGE/USD": "DOGE-USD"
}

active_positions = {
    strat_key: {
        name: {"side": None, "entry": 0.0, "sl": 0.0, "best_price": 0.0, "atr": 0.0}
        for name in SYMBOLS
    }
    for strat_key in STRATEGIES
}

def send_telegram(message, chat_id=MY_CHAT_ID):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def handle_incoming_users():
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
                        sender_id = str(update["message"]["chat"]["id"])
                        user_name = update["message"]["from"].get("first_name", "Trader")

                        if sender_id == MY_CHAT_ID:
                            send_telegram(
                                f"👑 *Admin Dashboard ({user_name})*\n\n"
                                f"🤖 Engine Status: `Online 24/7`\n"
                                f"🎯 Scalp: `15m/5m/1m`\n"
                                f"⏱️ Intraday: `1d/4h/1h -> 5m/15m`\n"
                                f"🌊 Swing: `1w/1d -> 1h`\n"
                                f"📊 Monitored Assets: `{len(SYMBOLS)} Cryptos`",
                                chat_id=sender_id
                            )
                        else:
                            public_notice = (
                                f"Hello {user_name}! 👋\n\n"
                                f"⚠️ *Private Algorithmic Engine*\n"
                                f"Signals are restricted to authorized administrators.\n\n"
                                f"🔒 *Access:* Closed"
                            )
                            send_telegram(public_notice, chat_id=sender_id)
        except Exception as e:
            print(f"Listener issue: {e}")
        time.sleep(2)

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

def get_trend(ticker_symbol, period, interval):
    try:
        data = yf.download(ticker_symbol, period=period, interval=interval, progress=False)
        if data is None or len(data) < 30:
            return "NEUTRAL"

        df = data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close_series = pd.Series(np.array(df['Close']).flatten(), index=df.index)
        ema50 = close_series.ewm(span=min(50, len(close_series)), adjust=False).mean()
        last_close = float(close_series.iloc[-1])
        last_ema = float(ema50.iloc[-1])

        if last_close > last_ema:
            return "BULLISH"
        elif last_close < last_ema:
            return "BEARISH"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

def manage_trailing_sl(strat_key, strat_label, name, curr_price):
    pos = active_positions[strat_key][name]
    if pos["side"] is None:
        return

    trailing_gap = pos["atr"] * 1.5

    if pos["side"] == "BUY":
        if curr_price > pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price - trailing_gap
            if new_sl > pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(
                    f"🛡️ *TRAILING SL UPDATED (BUY)*\n"
                    f"🏷️ Strategy: `{strat_label}`\n"
                    f"🪙 Asset: `{name}`\n"
                    f"📈 High: `{pos['best_price']:.4f}`\n"
                    f"🛑 New SL: `{pos['sl']:.4f}`"
                )
        elif curr_price <= pos["sl"]:
            send_telegram(
                f"🔴 *EXIT HIT (STOP LOSS)*\n"
                f"🏷️ Strategy: `{strat_label}`\n"
                f"🪙 Asset: `{name}`\n"
                f"💵 Exit: `{curr_price:.4f}`\n"
                f"🛑 SL Level: `{pos['sl']:.4f}`"
            )
            pos["side"] = None

    elif pos["side"] == "SELL":
        if curr_price < pos["best_price"]:
            pos["best_price"] = curr_price
            new_sl = curr_price + trailing_gap
            if new_sl < pos["sl"]:
                pos["sl"] = new_sl
                send_telegram(
                    f"🛡️ *TRAILING SL UPDATED (SELL)*\n"
                    f"🏷️ Strategy: `{strat_label}`\n"
                    f"🪙 Asset: `{name}`\n"
                    f"📉 Low: `{pos['best_price']:.4f}`\n"
                    f"🛑 New SL: `{pos['sl']:.4f}`"
                )
        elif curr_price >= pos["sl"]:
            send_telegram(
                f"🔴 *EXIT HIT (STOP LOSS)*\n"
                f"🏷️ Strategy: `{strat_label}`\n"
                f"🪙 Asset: `{name}`\n"
                f"💵 Exit: `{curr_price:.4f}`\n"
                f"🛑 SL Level: `{pos['sl']:.4f}`"
            )
            pos["side"] = None

def check_strategy_for_symbol(strat_key, cfg, name, ticker_symbol):
    try:
        htf_trend = get_trend(ticker_symbol, cfg["htf_period"], cfg["htf_interval"])
        mtf_trend = get_trend(ticker_symbol, cfg["mtf_period"], cfg["mtf_interval"])

        # Both Higher and Intermediate timeframes must align
        if htf_trend != mtf_trend or htf_trend == "NEUTRAL":
            return

        data = yf.download(ticker_symbol, period=cfg["entry_period"], interval=cfg["entry_interval"], progress=False)
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

        manage_trailing_sl(strat_key, cfg["label"], name, curr_price)

        strong_trend = curr_adx > cfg["adx_min"]
        active_pos = active_positions[strat_key][name]["side"]
        tp_mults = cfg["tp_multipliers"]

        # BUY SETUP
        if (active_pos is None) and (htf_trend == "BULLISH") and strong_trend and (curr_ema50 > curr_ema93) and (curr_price > swing_h) and (curr_price > curr_open):
            atr_sl = curr_price - (curr_atr * 1.5)
            sl = max(swing_l, atr_sl)
            risk = curr_price - sl

            if risk > 0:
                active_positions[strat_key][name] = {
                    "side": "BUY", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr
                }
                msg = (
                    f"🚀 *{cfg['label']} BUY SIGNAL*\n\n"
                    f"🪙 *Asset:* `{name}`\n"
                    f"📈 *Trend Confluence:* `{cfg['htf_interval']} + {cfg['mtf_interval']} Bullish`\n"
                    f"💵 *Entry:* `{curr_price:.4f}`\n"
                    f"🛑 *Dynamic SL:* `{sl:.4f}`\n"
                    f"📊 *ATR:* `{curr_atr:.4f}`\n\n"
                    f"🎯 *TARGETS:*\n"
                    f"• TP 1: `{curr_price + (risk * tp_mults[0]):.4f}`\n"
                    f"• TP 2: `{curr_price + (risk * tp_mults[1]):.4f}`\n"
                    f"• TP 3: `{curr_price + (risk * tp_mults[2]):.4f}`\n"
                    f"• TP 4: `{curr_price + (risk * tp_mults[3]):.4f}`\n\n"
                    f"⚡ *ADX:* `{curr_adx:.1f}`"
                )
                send_telegram(msg)

        # SELL SETUP
        elif (active_pos is None) and (htf_trend == "BEARISH") and strong_trend and (curr_ema50 < curr_ema93) and (curr_price < swing_l) and (curr_price < curr_open):
            atr_sl = curr_price + (curr_atr * 1.5)
            sl = min(swing_h, atr_sl)
            risk = sl - curr_price

            if risk > 0:
                active_positions[strat_key][name] = {
                    "side": "SELL", "entry": curr_price, "sl": sl, "best_price": curr_price, "atr": curr_atr
                }
                msg = (
                    f"⚠️ *{cfg['label']} SELL SIGNAL*\n\n"
                    f"🪙 *Asset:* `{name}`\n"
                    f"📉 *Trend Confluence:* `{cfg['htf_interval']} + {cfg['mtf_interval']} Bearish`\n"
                    f"💵 *Entry:* `{curr_price:.4f}`\n"
                    f"🛑 *Dynamic SL:* `{sl:.4f}`\n"
                    f"📊 *ATR:* `{curr_atr:.4f}`\n\n"
                    f"🎯 *TARGETS:*\n"
                    f"• TP 1: `{curr_price - (risk * tp_mults[0]):.4f}`\n"
                    f"• TP 2: `{curr_price - (risk * tp_mults[1]):.4f}`\n"
                    f"• TP 3: `{curr_price - (risk * tp_mults[2]):.4f}`\n"
                    f"• TP 4: `{curr_price - (risk * tp_mults[3]):.4f}`\n\n"
                    f"⚡ *ADX:* `{curr_adx:.1f}`"
                )
                send_telegram(msg)

    except Exception as e:
        print(f"Error on [{strat_key}] {name}: {e}")

threading.Thread(target=handle_incoming_users, daemon=True).start()

print("Multi-Timeframe Crypto Engine Live...")
send_telegram(
    "🔥 *Multi-Timeframe Crypto Engine Live 24/7!*\n\n"
    "• ⚡ *Scalping:* 15M / 5M -> 1M Entry\n"
    "• ⏱️ *Intraday:* 1D / 1H -> 5M Entry\n"
    "• 🌊 *Swing:* 1W / 1D -> 1H Entry\n\n"
    "Scanning BTC, ETH, SOL, BNB, XRP, ADA, DOGE..."
)

while True:
    for name, ticker in SYMBOLS.items():
        for strat_key, cfg in STRATEGIES.items():
            check_strategy_for_symbol(strat_key, cfg, name, ticker)
            time.sleep(1)
    time.sleep(5)
