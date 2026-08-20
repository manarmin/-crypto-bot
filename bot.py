import asyncio
import ccxt.async_support as ccxt
import urllib.request
import json
import os
import gc

# ==================== ۱. تنظیمات ربات و تلگرام ====================
MAIN_BOT_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
TEST_BOT_TOKEN = '8778525679:AAF0DG2sZLkuega7VJpOg5KQdpteAoA66NU'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', TEST_BOT_TOKEN)
CHAT_ID = os.environ.get('CHAT_ID', '570158397')

RSI_LEN = 14
RSI_MA_LEN = 14
OB_LEVEL = 70
OS_LEVEL = 30
VOL_MA_LEN = 20
VOL_MULTIPLIER = 1.2

TIMEFRAMES = ['30m', '4h', '1d', '1w']

EXCLUDED_BASE_ASSETS = {
    'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'USDE', 'PYUSD', 'USDS', 'USDD',
    'FRAX', 'LUSD', 'GUSD', 'EUR', 'AEUR', 'WBTC', 'WETH', 'STETH', 'WEETH',
    'RETH', 'CBETH', 'BTCB', 'WBETH', 'WBNB', 'WMATIC', 'PAXG', 'XAUT'
}

last_alerted = {}

def send_telegram(msg):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    data = json.dumps({'chat_id': CHAT_ID, 'text': msg, 'parse_mode': 'Markdown'}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'Telegram Error: {e}')

# ==================== ۲. استخراج ۱۰۰ ارز پرحجم و ترندها ====================
def fetch_json(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode('utf-8'))
    except Exception:
        return None

async def get_top_volume_symbols_for_ex(exchange):
    """استخراج ۱۰۰ ارز پرحجم هر صرافی به صورت پویا"""
    top_symbols = {}
    try:
        tickers = await exchange.fetch_tickers()
        valid_tickers = []
        for sym, ticker in tickers.items():
            if '/' not in sym:
                continue
            base, quote = sym.split('/')
            if quote in ['USDT', 'USD', 'BTC'] and base not in EXCLUDED_BASE_ASSETS:
                vol = ticker.get('quoteVolume')
                if vol is None and ticker.get('baseVolume') and ticker.get('last'):
                    vol = ticker['baseVolume'] * ticker['last']
                vol = float(vol or 0)
                valid_tickers.append((sym, vol))

        # مرتب‌سازی بر اساس حجم معاملات
        valid_tickers.sort(key=lambda x: x[1], reverse=True)
        
        # انتخاب ۱۰۰ ارز اول پرحجم
        for rank, (sym, vol) in enumerate(valid_tickers[:100], start=1):
            top_symbols[sym] = {'category': f'📊 ۱۰۰ پرحجم صرافی (رتبه {rank})', 'rank': rank}
    except Exception as e:
        print(f"خطا در دریافت حجم برای {exchange.id}: {e}")
    return top_symbols

def get_trending_from_coingecko():
    trending = {}
    print("در حال دریافت ترندهای CoinGecko...")
    cg_data = fetch_json('https://api.coingecko.com/api/v3/search/trending')
    if cg_data and 'coins' in cg_data:
        for item in cg_data['coins'][:15]:
            sym = item['item']['symbol'].upper()
            if sym not in EXCLUDED_BASE_ASSETS:
                trending[f"{sym}/USDT"] = {'category': '🔥 ترند بازار (CoinGecko)', 'rank': 999}
                trending[f"{sym}/BTC"] = {'category': '🔥 ترند بازار (CoinGecko)', 'rank': 999}
    return trending

# ==================== ۳. توابع محاسبات ریاضی ====================
def calc_rma(src, length):
    n = len(src)
    rma = [0.0] * n
    if n < length: return rma
    rma[length - 1] = sum(src[:length]) / length
    for i in range(length, n):
        rma[i] = (rma[i - 1] * (length - 1) + src[i]) / length
    return rma

def calc_sma(src, length):
    n = len(src)
    sma = [0.0] * n
    for i in range(length - 1, n):
        sma[i] = sum(src[i - length + 1 : i + 1]) / length
    return sma

def calc_rsi(closes, length=14):
    n = len(closes)
    gains, losses = [0.0] * n, [0.0] * n
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        if diff > 0: gains[i] = diff
        else: losses[i] = -diff
    avg_gains = calc_rma(gains, length)
    avg_losses = calc_rma(losses, length)
    rsi = [0.0] * n
    for i in range(length, n):
        if avg_losses[i] == 0: rsi[i] = 100.0
        else: rsi[i] = 100.0 - (100.0 / (1.0 + (avg_gains[i] / avg_losses[i])))
    return rsi

# ==================== ۴. آنالیز و دریافت سیگنال‌ها ====================
async def analyze_rsi_cycle(exchange, symbol, tf, info):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=300)
        if not ohlcv or len(ohlcv) < 200:
            return None

        times = [k[0] for k in ohlcv]
        highs = [float(k[2]) for k in ohlcv]
        lows = [float(k[3]) for k in ohlcv]
        closes = [float(k[4]) for k in ohlcv]
        volumes = [float(k[5]) for k in ohlcv]

        n = len(closes)
        rsi = calc_rsi(closes, RSI_LEN)
        rsi_ma = calc_sma(rsi, RSI_MA_LEN)
        vol_ma = calc_sma(volumes, VOL_MA_LEN)

        buy_state, buy_count = 0, 0
        price_b1, rsi_b1 = None, None
        lowest_price_in_state, lowest_rsi_in_state = None, None

        sell_state, sell_count = 0, 0
        price_s1, rsi_s1 = None, None
        highest_price_in_state, highest_rsi_in_state = None, None

        latest_signal = None

        for i in range(40, n):
            in_ob = rsi[i] >= OB_LEVEL
            in_os = rsi[i] <= OS_LEVEL
            high_volume = volumes[i] > (vol_ma[i] * VOL_MULTIPLIER)

            crossover = (rsi[i - 1] < rsi_ma[i - 1]) and (rsi[i] >= rsi_ma[i])
            crossunder = (rsi[i - 1] > rsi_ma[i - 1]) and (rsi[i] <= rsi_ma[i])

            # سیستم خرید
            if in_ob: buy_state, buy_count = 0, 0
            if buy_state == 0:
                if rsi[i] < rsi_ma[i] and in_os:
                    buy_state = 1
                    lowest_price_in_state, lowest_rsi_in_state = lows[i], rsi[i]
            elif buy_state == 1:
                lowest_price_in_state = min(lowest_price_in_state, lows[i])
                lowest_rsi_in_state = min(lowest_rsi_in_state, rsi[i])
                if crossover:
                    price_b1, rsi_b1 = lowest_price_in_state, lowest_rsi_in_state
                    buy_state = 2
            elif buy_state == 2:
                if rsi[i] < rsi_ma[i] and in_os:
                    buy_state = 3
                    lowest_price_in_state, lowest_rsi_in_state = lows[i], rsi[i]
            elif buy_state == 3:
                lowest_price_in_state = min(lowest_price_in_state, lows[i])
                lowest_rsi_in_state = min(lowest_rsi_in_state, rsi[i])
                if crossover:
                    buy_state = 0
                    buy_count += 1
                    bull_div = False if (price_b1 is None) else ((lowest_price_in_state < price_b1) and (lowest_rsi_in_state > rsi_b1))
                    if i >= n - 2:
                        latest_signal = {
                            'action': 'BUY',
                            'type': f"🟢 BUY SIGNAL B{buy_count}{' (Vol+)' if high_volume else ''}{' (+Div)' if bull_div else ''}",
                            'price': closes[i], 'rsi': rsi[i], 'rsi_ma': rsi_ma[i], 'time': times[i]
                        }

            # سیستم فروش
            if in_os: sell_state, sell_count = 0, 0
            if sell_state == 0:
                if rsi[i] > rsi_ma[i] and in_ob:
                    sell_state = 1
                    highest_price_in_state, highest_rsi_in_state = highs[i], rsi[i]
            elif sell_state == 1:
                highest_price_in_state = max(highest_price_in_state, highs[i])
                highest_rsi_in_state = max(highest_rsi_in_state, rsi[i])
                if crossunder:
                    price_s1, rsi_s1 = highest_price_in_state, highest_rsi_in_state
                    sell_state = 2
            elif sell_state == 2:
                if rsi[i] > rsi_ma[i] and in_ob:
                    sell_state = 3
                    highest_price_in_state, highest_rsi_in_state = highs[i], rsi[i]
            elif sell_state == 3:
                highest_price_in_state = max(highest_price_in_state, highs[i])
                highest_rsi_in_state = max(highest_rsi_in_state, rsi[i])
                if crossunder:
                    sell_state = 0
                    sell_count += 1
                    bear_div = False if (price_s1 is None) else ((highest_price_in_state > price_s1) and (highest_rsi_in_state < rsi_s1))
                    if i >= n - 2:
                        latest_signal = {
                            'action': 'SELL',
                            'type': f"🔴 SELL SIGNAL S{sell_count}{' (Vol+)' if high_volume else ''}{' (-Div)' if bear_div else ''}",
                            'price': closes[i], 'rsi': rsi[i], 'rsi_ma': rsi_ma[i], 'time': times[i]
                        }

        if latest_signal:
            base, quote = symbol.split('/')
            # استانداردسازی واحد پایه برای مقایسه بین صرافی‌ها
            normalized_quote = 'USD_STABLE' if quote in ['USDT', 'USD'] else 'BTC'
            
            return {
                'exchange': exchange.id.capitalize(),
                'symbol': symbol,
                'base': base,
                'norm_quote': normalized_quote,
                'tf': tf,
                'category': info['category'],
                'rank': info['rank'],
                'signal': latest_signal
            }
    except Exception:
        pass
    return None

# ==================== ۵. اجرای اصلی و پردازش سیگنال‌های ستاره‌دار ====================
async def main():
    print("🚀 شروع اسکن ۱۰۰ ارز پرحجم صرافی‌ها و پردازش سیگنال‌های ستاره‌دار...")

    exchanges = {
        'binance': ccxt.binance({'enableRateLimit': True}),
        'coinbase': ccxt.coinbase({'enableRateLimit': True}),
        'kraken': ccxt.kraken({'enableRateLimit': True})
    }

    cg_trending = get_trending_from_coingecko()
    
    tasks = []
    ex_symbols_map = {}

    # دریافت ۱۰۰ ارز پرحجم از هر صرافی به صورت همزمان
    for name, ex in exchanges.items():
        try:
            await ex.load_markets()
            top_symbols = await get_top_volume_symbols_for_ex(ex)
            # ترکیب با ترندهای کوین‌گکو
            combined = {**cg_trending, **top_symbols}
            ex_symbols_map[name] = combined
            print(f"✅ صرافی {name.capitalize()}: تعداد {len(combined)} ارز آماده اسکن شد.")
        except Exception as e:
            print(f"❌ خطا در لود {name}: {e}")

    # ساخت لیست پردازش‌ها
    for name, ex in exchanges.items():
        symbols_info = ex_symbols_map.get(name, {})
        for symbol, info in symbols_info.items():
            # تطبیق نماد برای کوین‌بیس
            alt_sym = symbol.replace('/USDT', '/USD') if name == 'coinbase' else symbol
            active_sym = alt_sym if alt_sym in ex.markets else (symbol if symbol in ex.markets else None)

            if active_sym:
                for tf in TIMEFRAMES:
                    if tf in ex.timeframes:
                        tasks.append(analyze_rsi_cycle(ex, active_sym, tf, info))

    print(f"🔎 تعداد کل آنالیزهای آماده اجرا: {len(tasks)}")

    # اجرای دسته‌ای برای رعایت قوانین صرافی‌ها
    results = []
    chunk_size = 40
    for i in range(0, len(tasks), chunk_size):
        chunk = tasks[i:i + chunk_size]
        res = await asyncio.gather(*chunk)
        results.extend([r for r in res if r is not None])
        await asyncio.sleep(0.5)

    # ------------------ تجمیع و بررسی سیگنال‌های ستاره‌دار (Gold Signals) ------------------
    grouped_signals = {}
    for res in results:
        # کلید گروه‌بندی: مثلا (ETH, USD_STABLE, 4h, BUY)
        group_key = (res['base'], res['norm_quote'], res['tf'], res['signal']['action'])
        if group_key not in grouped_signals:
            grouped_signals[group_key] = []
        grouped_signals[group_key].append(res)

    # ارسال گزارش‌ها به تلگرام
    for group_key, sig_list in grouped_signals.items():
        base_coin, norm_q, tf, action = group_key
        exchanges_involved = [s['exchange'] for s in sig_list]
        is_multi_exchange = len(exchanges_involved) >= 2
        
        # بررسی اگر در رتبه‌های بالای حجم باشد (مثلاً زیر ۱۵)
        min_rank = min([s['rank'] for s in sig_list])
        is_top_volume = min_rank <= 15

        # شرط ستاره‌دار شدن: یا تایید همزمان چند صرافی یا حجم بسیار بالا
        is_starred = is_multi_exchange or is_top_volume

        # نمونه سیگنال برای استخراج جزئیات
        first_sig = sig_list[0]
        sig_data = first_sig['signal']
        symbol_disp = first_sig['symbol']
        category_disp = first_sig['category']

        key_alert = f"{base_coin}_{norm_q}_{tf}_{sig_data['type']}_{sig_data['time']}"
        if last_alerted.get(key_alert) != key_alert:
            last_alerted[key_alert] = key_alert

            star_header = ""
            if is_starred:
                reasons = []
                if is_multi_exchange: reasons.append("تایید همزمان در چند صرافی")
                if is_top_volume: reasons.append("حجم معاملات بسیار بالا")
                star_header = f"⭐ *[سیگنال طلایی - {' + '.join(reasons)}]* ⭐\n\n"

            ex_str = ", ".join(exchanges_involved)
            price_fmt = f"{sig_data['price']:.8f}" if symbol_disp.endswith('/BTC') else f"{sig_data['price']:.4f}"

            msg = (
                f"{star_header}"
                f"*{sig_data['type']}*\n\n"
                f'🏛 *صرافی(ها):* `{ex_str}`\n'
                f'📌 *ارز:* `{symbol_disp}`\n'
                f'🏷 *گروه:* `{category_disp}`\n'
                f'⏱ *تایم‌فریم:* `{tf}`\n'
                f"💵 *قیمت:* `{price_fmt}`\n"
                f"📊 *RSI:* `{sig_data['rsi']:.1f}` | *RSI MA:* `{sig_data['rsi_ma']:.1f}`"
            )
            send_telegram(msg)

    for ex in exchanges.values():
        await ex.close()

    gc.collect()
    print("✅ اسکن کامل شد و پیام‌ها ارسال گردید.")

if __name__ == '__main__':
    asyncio.run(main())
