import gc
import json
import os
import shutil
import sys
import time
import urllib.request

# تنظیم UTF-8 برای پشتیبانی کامل از کاراکترهای فارسی و رموزی
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات و کانال ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

# ۱۰ ارز اختصاصی مورد نظر شما
TOP_10_WATCHLIST = [
    'SOLUSDT',
    'SUIUSDT',
    'HYPEUSDT',
    'SYRUPUSDT',
    'LINKUSDT',
    'XRPUSDT',
    'XLMUSDT',
    'KITEUSDT',
    'ONDOUSDT',
    'AAVEUSDT',
]

# تنظیمات استراتژی RSI Cycle
rsi_len = 14
rsi_ma_len = 14
ob_level = 70
os_level = 30
vol_ma_len = 20
vol_multiplier = 1.2

TIMEFRAMES = ['30m', '4h', '1d', '1w']
TOP_LIMIT = 100

BINANCE_URLS = [
    'https://data-api.binance.vision',
    'https://api1.binance.com',
    'https://api2.binance.com',
]

WORKING_BASE_URL = None
last_alerted = {}

EXCLUDED_BASE_ASSETS = {
    'USDT',
    'USDC',
    'FDUSD',
    'DAI',
    'TUSD',
    'USDE',
    'PYUSD',
    'USDS',
    'USDD',
    'FRAX',
    'LUSD',
    'GUSD',
    'EUR',
    'AEUR',
    'WBTC',
    'WETH',
    'STETH',
    'WEETH',
    'RETH',
    'CBETH',
    'BTCB',
    'WBETH',
    'WBNB',
    'WMATIC',
    'PAXG',
    'XAUT',
}


def send_telegram(msg):
  url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
  data = json.dumps({
      'chat_id': CHAT_ID,
      'text': msg,
      'parse_mode': 'Markdown',
      'disable_web_page_preview': True,
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
          full_url, headers={'User-Agent': 'Mozilla/5.0'}
      )
      with urllib.request.urlopen(req, timeout=10) as response:
        res_data = json.loads(response.read().decode('utf-8'))
        if not url_path.startswith('http'):
          WORKING_BASE_URL = full_url.replace(url_path, '')
        return res_data
    except Exception:
      continue
  return None


def is_leveraged_token(symbol):
  upper_sym = symbol.upper()
  for kw in ['DOWN', 'BEAR', 'BULL', '3L', '3S', '5L', '5S']:
    if upper_sym.endswith(kw) or f'_{kw}' in upper_sym:
      return True
  return False


def get_top_100_market_cap_symbols():
  """دریافت ۱۰۰ ارز برتر بازار بر اساس حجم ۲۴ ساعته صرافی + ۱۰ ارز اختصاصی"""
  ticker_data = fetch_json('/api/v3/ticker/24hr')
  volume_sorted_pairs = []

  if ticker_data and isinstance(ticker_data, list):
    valid_tickers = []
    for item in ticker_data:
      sym = item.get('symbol', '')
      if sym.endswith('USDT'):
        base = sym.replace('USDT', '')
        if base not in EXCLUDED_BASE_ASSETS and not is_leveraged_token(sym):
          quote_volume = float(item.get('quoteVolume', 0))
          valid_tickers.append((sym, quote_volume))

    valid_tickers.sort(key=lambda x: x[1], reverse=True)
    volume_sorted_pairs = [t[0] for t in valid_tickers]

  coingecko_pairs = []
  coingecko_data = fetch_json(
      'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false'
  )

  if coingecko_data and isinstance(coingecko_data, list):
    for coin in coingecko_data:
      symbol = coin.get('symbol', '').upper()
      if symbol not in EXCLUDED_BASE_ASSETS:
        coingecko_pairs.append(f'{symbol}USDT')

  ranked_symbols = []

  # همیشه ۱۰ ارز اختصاصی در اولویت اول لیست قرار می‌گیرند
  for forced_sym in TOP_10_WATCHLIST:
    if forced_sym not in ranked_symbols:
      ranked_symbols.append(forced_sym)

  for pair in volume_sorted_pairs:
    if pair in coingecko_pairs and pair not in ranked_symbols:
      ranked_symbols.append(pair)
      if len(ranked_symbols) >= TOP_LIMIT:
        break

  if len(ranked_symbols) < TOP_LIMIT:
    for pair in volume_sorted_pairs:
      if pair not in ranked_symbols:
        ranked_symbols.append(pair)
        if len(ranked_symbols) >= TOP_LIMIT:
          break

  return ranked_symbols[:TOP_LIMIT]


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


def analyze_rsi_cycle(symbol, tf):
  try:
    klines = fetch_json(
        f'/api/v3/klines?symbol={symbol}&interval={tf}&limit=300'
    )
    if not klines or len(klines) < 200:
      return None

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
    price_b1 = None
    rsi_b1 = None
    lowest_price_in_state = None
    lowest_rsi_in_state = None

    sell_state = 0
    sell_count = 0
    price_s1 = None
    rsi_s1 = None
    highest_price_in_state = None
    highest_rsi_in_state = None

    latest_signal = None

    for i in range(40, n):
      in_ob = rsi[i] >= ob_level
      in_os = rsi[i] <= os_level
      high_volume = volumes[i] > (vol_ma[i] * vol_multiplier)

      crossover = (rsi[i - 1] < rsi_ma[i - 1]) and (rsi[i] >= rsi_ma[i])
      crossunder = (rsi[i - 1] > rsi_ma[i - 1]) and (rsi[i] <= rsi_ma[i])

      # BUY Cycle
      if in_ob:
        buy_state = 0
        buy_count = 0

      if buy_state == 0:
        if rsi[i] < rsi_ma[i] and in_os:
          buy_state = 1
          lowest_price_in_state = lows[i]
          lowest_rsi_in_state = rsi[i]
      elif buy_state == 1:
        lowest_price_in_state = min(lowest_price_in_state, lows[i])
        lowest_rsi_in_state = min(lowest_rsi_in_state, rsi[i])
        if crossover:
          price_b1 = lowest_price_in_state
          rsi_b1 = lowest_rsi_in_state
          buy_state = 2
      elif buy_state == 2:
        if rsi[i] < rsi_ma[i] and in_os:
          buy_state = 3
          lowest_price_in_state = lows[i]
          lowest_rsi_in_state = rsi[i]
      elif buy_state == 3:
        lowest_price_in_state = min(lowest_price_in_state, lows[i])
        lowest_rsi_in_state = min(lowest_rsi_in_state, rsi[i])
        if crossover:
          buy_state = 0
          buy_count += 1
          bull_div = (
              False
              if (price_b1 is None or rsi_b1 is None)
              else (
                  (lowest_price_in_state < price_b1)
                  and (lowest_rsi_in_state > rsi_b1)
              )
          )

          if i == n - 1 or i == n - 2:
            vol_str = ' 🚀 (Vol+)' if high_volume else ''
            div_str = ' 📈 (+Div)' if bull_div else ''
            latest_signal = {
                'type': f'🟢 BUY SIGNAL B{buy_count}{vol_str}{div_str}',
                'price': closes[i],
                'rsi': rsi[i],
                'rsi_ma': rsi_ma[i],
                'time': times[i],
            }

      # SELL Cycle
      if in_os:
        sell_state = 0
        sell_count = 0

      if sell_state == 0:
        if rsi[i] > rsi_ma[i] and in_ob:
          sell_state = 1
          highest_price_in_state = highs[i]
          highest_rsi_in_state = rsi[i]
      elif sell_state == 1:
        highest_price_in_state = max(highest_price_in_state, highs[i])
        highest_rsi_in_state = max(highest_rsi_in_state, rsi[i])
        if crossunder:
          price_s1 = highest_price_in_state
          rsi_s1 = highest_rsi_in_state
          sell_state = 2
      elif sell_state == 2:
        if rsi[i] > rsi_ma[i] and in_ob:
          sell_state = 3
          highest_price_in_state = highs[i]
          highest_rsi_in_state = rsi[i]
      elif sell_state == 3:
        highest_price_in_state = max(highest_price_in_state, highs[i])
        highest_rsi_in_state = max(highest_rsi_in_state, rsi[i])
        if crossunder:
          sell_state = 0
          sell_count += 1
          bear_div = (
              False
              if (price_s1 is None or rsi_s1 is None)
              else (
                  (highest_price_in_state > price_s1)
                  and (highest_rsi_in_state < rsi_s1)
              )
          )

          if i == n - 1 or i == n - 2:
            vol_str = ' 🚀 (Vol+)' if high_volume else ''
            div_str = ' 📉 (-Div)' if bear_div else ''
            latest_signal = {
                'type': f'🔴 SELL SIGNAL S{sell_count}{vol_str}{div_str}',
                'price': closes[i],
                'rsi': rsi[i],
                'rsi_ma': rsi_ma[i],
                'time': times[i],
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
            f'⏱ *تایم‌فریم:* `{tf}`\n'
            f"💵 *قیمت:* `{latest_signal['price']:.4f}`\n"
            f"📊 *RSI:* `{latest_signal['rsi']:.1f}` | *RSI MA:*"
            f" `{latest_signal['rsi_ma']:.1f}`\n\n"
            f'📢 *Channel:* @{CHANNEL_NAME}'
        )
        send_telegram(msg)
        return latest_signal
  except Exception as e:
    print(f'Error analyzing {symbol} {tf}: {e}')
  return None


def main():
  print('شروع اسکن ترکیبی ۱۰۰ ارز برتر + ۱۰ ارز اختصاصی...')
  symbols = get_top_100_market_cap_symbols()

  # ساخت دیکشنری برای پیگیری وضعیت ۱۰ ارز اختصاصی
  top_10_status = {sym.replace('USDT', ''): [] for sym in TOP_10_WATCHLIST}

  for tf in TIMEFRAMES:
    for sym in symbols:
      sig = analyze_rsi_cycle(sym, tf)
      clean_sym = sym.replace('USDT', '')

      if sym in TOP_10_WATCHLIST and sig:
        top_10_status[clean_sym].append(f"{tf}: {sig['type']}")

      time.sleep(0.05)

  # ساخت گزارش اختصاصی برای ۱۰ ارز تاپ
  report_lines = ['📋 *گزارش وضعیت ۱۰ ارز اختصاصی (Top Watchlist):*\n']
  for coin, sigs in top_10_status.items():
    if sigs:
      signals_text = ' | '.join(sigs)
      report_lines.append(f'✅ `{coin}`: {signals_text}')
    else:
      report_lines.append(f'⚪️ `{coin}`: هیچ سیگنالی یافت نشد.')

  report_lines.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')

  # ارسال گزارش تاپ تن به تلگرام
  send_telegram('\n'.join(report_lines))

  gc.collect()
  print('اسکن و ارسال گزارش با موفقیت کامل شد.')


if __name__ == '__main__':
  main()
