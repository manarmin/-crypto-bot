import gc
import json
import os
import shutil
import time
import urllib.request

# ==================== ۱. آزادسازی حافظه کش دیسک ====================
try:
    cache_path = os.path.expanduser('~/.cache')
    if os.path.exists(cache_path):
        shutil.rmtree(cache_path)
except Exception:
    pass

# ==================== ۲. تنظیمات ربات و تلگرام ====================
MAIN_BOT_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
TEST_BOT_TOKEN = '8778525679:AAF0DG2sZLkuega7VJpOg5KQdpteAoA66NU'

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', TEST_BOT_TOKEN)
CHAT_ID = os.environ.get('CHAT_ID', '570158397')

rsi_len = 14
rsi_ma_len = 14
ob_level = 70
os_level = 30

vol_ma_len = 20
vol_multiplier = 1.2

TIMEFRAMES = ['30m', '4h', '1d', '1w']

BINANCE_URLS = [
    'https://data-api.binance.vision',
    'https://api1.binance.com',
    'https://api2.binance.com',
]

WORKING_BASE_URL = None
last_alerted = {}

EXCLUDED_BASE_ASSETS = {
    'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'USDE', 'PYUSD', 'USDS', 'USDD',
    'FRAX', 'LUSD', 'GUSD', 'EUR', 'AEUR', 'WBTC', 'WETH', 'STETH', 'WEETH',
    'RETH', 'CBETH', 'BTCB', 'WBETH', 'WBNB', 'WMATIC', 'PAXG', 'XAUT'
}

# لیست رزرو در صورت قطعی کامل API ها
STATIC_FALLBACK = [
    'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT', 'DOGEUSDT', 'ADAUSDT',
    'AVAXUSDT', 'SHIBUSDT', 'DOTUSDT', 'LINKUSDT', 'BCHUSDT', 'NEARUSDT', 'SUIUSDT',
    'LTCUSDT', 'UNIUSDT', 'PEPEUSDT', 'APTUSDT', 'ICPUSDT', 'ETCUSDT', 'XMRUSDT'
]

def send_telegram(msg):
    url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
    data = json.dumps({
        'chat_id': CHAT_ID,
        'text': msg,
        'parse_mode': 'Markdown',
    }).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, headers={'Content-Type': 'application/json'}
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f'Telegram Error: {e}')


def fetch_json(url_path):
    global WORKING_BASE_URL
    if url_path.startswith('http'):
        urls = [url_path]
    else:
        urls = (
            [f'{WORKING_BASE_URL}{url_path}']
            if WORKING_BASE_URL
            else [f'{b}{url_path}' for b in BINANCE_URLS]
        )

    for full_url in urls:
        try:
            req = urllib.request.Request(
                full_url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=8) as response:
                res_data = json.loads(response.read().decode('utf-8'))
                if not url_path.startswith('http'):
                    WORKING_BASE_URL = full_url.replace(url_path, '')
                return res_data
        except urllib.error.HTTPError as e:
            if e.code in [429, 418]:
                print("⚠️ محدودیت درخواست بایننس، ۳ ثانیه مکث...")
                time.sleep(3)
            continue
        except Exception:
            continue
    return None


def is_leveraged_token(symbol):
    upper_sym = symbol.upper()
    for kw in ['DOWN', 'BEAR', 'BULL', '3L', '3S', '5L', '5S']:
        if upper_sym.endswith(kw) or f'_{kw}' in upper_sym:
            return True
    return False


def get_target_symbols():
    """استخراج ارزها به همراه دسته‌بندی آن‌ها (ترند یا پرحجم)"""
    target_symbols = {}
    binance_usdt_pairs = {}

    # 1️⃣ دریافت کل مارکت بایننس برای فیلتر حجم
    ticker_data = fetch_json('/api/v3/ticker/24hr')
    if ticker_data:
        for item in ticker_data:
            sym = item.get('symbol', '')
            if sym.endswith('USDT'):
                base = sym.replace('USDT', '')
                if base not in EXCLUDED_BASE_ASSETS and not is_leveraged_token(sym):
                    try:
                        vol = float(item.get('quoteVolume', 0))
                        binance_usdt_pairs[sym] = vol
                    except ValueError:
                        continue

    # اگر بایننس کلا قطع بود از لیست رزرو استفاده کن
    if not binance_usdt_pairs:
        print("⚠️ بایننس در دسترس نیست، استفاده از لیست رزرو.")
        return {sym: '⚠️ لیست رزرو (Fallback)' for sym in STATIC_FALLBACK}

    # 2️⃣ استخراج ارزهای ترند از CoinGecko
    print("در حال دریافت لیست ترند از CoinGecko...")
    trending_count = 0
    try:
        cg_data = fetch_json('https://api.coingecko.com/api/v3/search/trending')
        if cg_data and 'coins' in cg_data:
            for item in cg_data['coins'][:20]:
                cg_sym = item['item']['symbol'].upper()
                pair = f"{cg_sym}USDT"
                # بررسی می‌کنیم که این ارز حتماً در بایننس هم لیست شده باشد
                if pair in binance_usdt_pairs and pair not in target_symbols:
                    target_symbols[pair] = '🔥 ترند بازار (CoinGecko)'
                    trending_count += 1
    except Exception as e:
        print(f"خطا در دریافت CoinGecko: {e}")

    print(f"✅ تعداد {trending_count} ارز ترند شناسایی و اضافه شد.")

    # 3️⃣ استخراج 200 ارز اول بایننس از نظر حجم معاملات
    sorted_by_volume = sorted(binance_usdt_pairs.items(), key=lambda x: x[1], reverse=True)
    
    volume_count = 0
    for sym, vol in sorted_by_volume:
        if volume_count >= 200:
            break
        # اگر ارز قبلاً به عنوان ترند اضافه نشده بود، به عنوان پرحجم اضافه شود
        if sym not in target_symbols:
            target_symbols[sym] = '📊 پرحجم‌ترین‌ها (Binance)'
            volume_count += 1

    print(f"✅ تعداد {volume_count} ارز پرحجم بایننس اضافه شد.")
    return target_symbols


def calc_rma(src, length):
    n = len(src)
    rma = [0.0] * n
    if n < length:
        return rma
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
        if diff > 0:
            gains[i] = diff
        else:
            losses[i] = -diff
    avg_gains = calc_rma(gains, length)
    avg_losses = calc_rma(losses, length)

    rsi = [0.0] * n
    for i in range(length, n):
        if avg_losses[i] == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gains[i] / avg_losses[i]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def analyze_rsi_cycle(symbol, tf, category):
    try:
        klines = fetch_json(
            f'/api/v3/klines?symbol={symbol}&interval={tf}&limit=300'
        )
        if not klines or len(klines) < 200:
            return

        times = [k[0] for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        closes = [float(k[4]) for k in klines]
        volumes = [float(k[5]) for k in klines]

        n = len(closes)
        rsi = calc_rsi(closes, rsi_len)
        rsi_ma = calc_sma(rsi, rsi_ma_len)
        vol_ma = calc_sma(volumes, vol_ma_len)

        buy_state = 0
        buy_count = 0
        price_b1, rsi_b1 = None, None
        lowest_price_in_state, lowest_rsi_in_state = None, None

        sell_state = 0
        sell_count = 0
        price_s1, rsi_s1 = None, None
        highest_price_in_state, highest_rsi_in_state = None, None

        latest_signal = None

        for i in range(40, n):
            in_ob = rsi[i] >= ob_level
            in_os = rsi[i] <= os_level
            high_volume = volumes[i] > (vol_ma[i] * vol_multiplier)

            crossover = (rsi[i - 1] < rsi_ma[i - 1]) and (rsi[i] >= rsi_ma[i])
            crossunder = (rsi[i - 1] > rsi_ma[i - 1]) and (rsi[i] <= rsi_ma[i])

            # سیستم خرید
            if in_ob:
                buy_state = 0
                buy_count = 0

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
                            'type': f"🟢 BUY SIGNAL B{buy_count}{' (Vol+)' if high_volume else ''}{' (+Div)' if bull_div else ''}",
                            'price': closes[i], 'rsi': rsi[i], 'rsi_ma': rsi_ma[i], 'time': times[i]
                        }

            # سیستم فروش
            if in_os:
                sell_state = 0
                sell_count = 0

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
                            'type': f"🔴 SELL SIGNAL S{sell_count}{' (Vol+)' if high_volume else ''}{' (-Div)' if bear_div else ''}",
                            'price': closes[i], 'rsi': rsi[i], 'rsi_ma': rsi_ma[i], 'time': times[i]
                        }

        if latest_signal:
            key = f'{symbol}_{tf}'
            alert_id = f"{key}_{latest_signal['type']}_{latest_signal['time']}"
            if last_alerted.get(key) != alert_id:
                last_alerted[key] = alert_id
                clean_symbol = symbol.replace('USDT', '')
                msg = (
                    f"*{latest_signal['type']}*\n\n"
                    f'📌 *ارز:* `{clean_symbol}`\n'
                    f'🏷 *گروه:* `{category}`\n'
                    f'⏱ *تایم‌فریم:* `{tf}`\n'
                    f"💵 *قیمت:* `{latest_signal['price']:.4f}`\n"
                    f"📊 *RSI:* `{latest_signal['rsi']:.1f}` | *RSI MA:*"
                    f" `{latest_signal['rsi_ma']:.1f}`"
                )
                send_telegram(msg)
    except Exception:
        pass


def main():
    print(f"شروع بررسی بازار...")

    symbols_info = get_target_symbols()
    print(f"مجموعاً {len(symbols_info)} ارز برای اسکن آماده شد.")

    for tf in TIMEFRAMES:
        for sym, category in symbols_info.items():
            analyze_rsi_cycle(sym, tf, category)
            time.sleep(0.06)  # مکث بهینه برای جلوگیری از Rate Limit بایننس

    gc.collect()
    print("بررسی کامل شد.")


if __name__ == '__main__':
    main()
