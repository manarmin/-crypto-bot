import os
import sys
import requests
import pandas as pd
import numpy as np
import ccxt

# تنظیم انکودینگ پیش‌فرض برای پشتیبانی کامل از زبان فارسی و Unicode
sys.stdout.reconfigure(encoding='utf-8')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram tokens not set!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if not res.ok:
            print(f"Telegram API Error: {res.text}")
    except Exception as e:
        print(f"Telegram Send Exception: {e}")

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series, fast=12, slow=26, signal=9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - signal_line
    return macd, signal_line, hist

def main():
    exchanges = [
        ccxt.coinbase({'enableRateLimit': True}),
        ccxt.kraken({'enableRateLimit': True}),
        ccxt.kucoin({'enableRateLimit': True}),
        ccxt.bybit({'enableRateLimit': True})
    ]
    
    symbols = []
    for ex in exchanges:
        try:
            ex.load_markets()
            for s in ex.symbols:
                if s.endswith('/USDT') or s.endswith('/USD'):
                    if s not in symbols:
                        symbols.append((ex, s))
                if len(symbols) >= 120:
                    break
        except Exception:
            continue
        if len(symbols) >= 120:
            break

    signals_found = 0
    timeframes = ['1h', '4h']

    for ex, sym in symbols:
        for tf in timeframes:
            try:
                ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=100)
                if not ohlcv or len(ohlcv) < 35:
                    continue
                df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
                
                df['rsi'] = calculate_rsi(df['close'])
                macd, signal_line, hist = calculate_macd(df['close'])
                df['macd'] = macd
                df['macd_signal'] = signal_line
                df['macd_hist'] = hist

                last = df.iloc[-1]
                prev = df.iloc[-2]

                # نمونه شرط سیگنال: RSI زیر 35 + کراس صعودی MACD
                rsi_buy = last['rsi'] < 35
                macd_cross_buy = (prev['macd'] < prev['macd_signal']) and (last['macd'] > last['macd_signal'])

                if rsi_buy and macd_cross_buy:
                    signals_found += 1
                    msg = (
                        f"🟢 <b>سیگنال خرید جدید!</b>\n\n"
                        f"📌 <b>ارز:</b> {sym}\n"
                        f"⏱ <b>تایم‌فریم:</b> {tf}\n"
                        f"💵 <b>قیمت:</b> {last['close']}\n"
                        f"📊 <b>RSI:</b> {round(last['rsi'], 2)}\n"
                        f"🏛 <b>صرافی:</b> {ex.id.upper()}"
                    )
                    send_telegram(msg)
            except Exception:
                continue

    # ارسال پیام وضعیت کلی به تلگرام (حتی اگر سیگنالی یافت نشود)
    status_msg = f"📊 <b>گزارش اسکن ربات</b>\n\n✅ اسکن با موفقیت انجام شد.\n🔍 تعداد سیگنال‌های یافت شده: {signals_found}"
    send_telegram(status_msg)

if __name__ == '__main__':
    main()
