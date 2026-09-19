import gc
import json
import os
import sys
import time
import urllib.request

# تنظیم UTF-8 برای پشتیبانی کامل از زبان فارسی
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

RSI_LEN = 14
RSI_MA_LEN = 14
LOOKBACK_MONTHS = 12  # بررسی وقوع کراس در ۱۲ ماه اخیر

BINANCE_ENDPOINTS = [
    'https://data-api.binance.vision',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api.binance.com',
]

EXCLUDED_ASSETS = {
    'BTC',
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
    'WBTC',
    'WETH',
    'STETH',
    'BUSD',
}

last_alerted = {}


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
    urllib.request.urlopen(req, timeout=12)
  except Exception as e:
    print(f'Telegram Error: {e}')


def fetch_json(url):
  try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=10) as response:
      return json.loads(response.read().decode('utf-8'))
  except Exception:
    return None


def fetch_binance(path):
  for base in BINANCE_ENDPOINTS:
    url = f'{base}{path}'
    try:
      req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
      with urllib.request.urlopen(req, timeout=8) as response:
        res = json.loads(response.read().decode('utf-8'))
        if res and not (isinstance(res, dict) and res.get('code')):
          return res
    except Exception:
      continue
  return None


def is_leveraged(symbol):
  upper = symbol.upper()
  for kw in ['DOWN', 'BEAR', 'BULL', '3L', '3S', '5L', '5S', 'UP']:
    if upper.endswith(kw) or f'_{kw}' in upper:
      return True
  return False


def get_top_500_btc_symbols():
  """استخراج ۵۰۰ ارز برتر مارکت کپ از CoinGecko و تطبیق با جفت‌ارزهای BTC بایننس"""
  binance_btc_symbols = set()
  ticker_data = fetch_binance('/api/v3/ticker/24hr')

  if ticker_data and isinstance(ticker_data, list):
    for item in ticker_data:
      sym = item.get('symbol', '')
      if sym.endswith('BTC'):
        base = sym[:-3]
        if base not in EXCLUDED_ASSETS and not is_leveraged(sym):
          binance_btc_symbols.add(sym)

  top_500_symbols = []

  # ۵۰۰ ارز برتر از CoinGecko (صفحه ۱ و ۲)
  for page in [1, 2]:
    cg_url = f'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}&sparkline=false'
    cg_data = fetch_json(cg_url)

    if cg_data and isinstance(cg_data, list):
      for coin in cg_data:
        sym = coin.get('symbol', '').upper()
        if sym and sym not in EXCLUDED_ASSETS:
          pair = f'{sym}BTC'
          if pair in binance_btc_symbols and pair not in top_500_symbols:
            top_500_symbols.append(pair)
    time.sleep(0.3)

  # در صورت محدودیت API کوین‌گکو، کل جفت‌ارزهای BTC بایننس جایگزین می‌شوند
  if len(top_500_symbols) < 50:
    print('استفاده از تمام جفت‌ارزهای BTC بایننس به عنوان پشتیبان...')
    top_500_symbols = sorted(list(binance_btc_symbols))

  return top_500_symbols


def calc_exact_tv_rsi(closes, rsi_length=14, sma_length=14):
  """شبیه‌سازی دقیق الگوریتم RSI و SMA تریدینگ‌ویو"""
  n = len(closes)
  rsi = [0.0] * n
  rsi_ma = [0.0] * n

  if n < rsi_length + sma_length:
    return rsi, rsi_ma

  gains = [max(0.0, closes[i] - closes[i - 1]) for i in range(1, n)]
  losses = [max(0.0, closes[i - 1] - closes[i]) for i in range(1, n)]

  avg_gain = sum(gains[:rsi_length]) / rsi_length
  avg_loss = sum(losses[:rsi_length]) / rsi_length

  if avg_loss == 0:
    rsi[rsi_length] = 100.0 if avg_gain > 0 else 0.0
  else:
    rsi[rsi_length] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

  for i in range(rsi_length + 1, n):
    avg_gain = (avg_gain * (rsi_length - 1) + gains[i - 1]) / rsi_length
    avg_loss = (avg_loss * (rsi_length - 1) + losses[i - 1]) / rsi_length
    if avg_loss == 0:
      rsi[i] = 100.0 if avg_gain > 0 else 0.0
    else:
      rsi[i] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

  for i in range(rsi_length + sma_length - 1, n):
    window = rsi[i - sma_length + 1 : i + 1]
    rsi_ma[i] = sum(window) / sma_length

  return rsi, rsi_ma


def analyze_monthly_breakout(symbol):
  try:
    klines = fetch_binance(
        f'/api/v3/klines?symbol={symbol}&interval=1M&limit=1000'
    )
    if not klines or len(klines) < (RSI_LEN + RSI_MA_LEN + 2):
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]

    rsi, rsi_ma = calc_exact_tv_rsi(closes, RSI_LEN, RSI_MA_LEN)
    n = len(closes)

    is_currently_bullish = rsi[-1] > rsi_ma[-1]

    crossed_recently = False
    cross_month_offset = 0

    if is_currently_bullish:
      start_idx = max(RSI_LEN + RSI_MA_LEN, n - LOOKBACK_MONTHS)
      for idx in range(start_idx, n):
        if idx > 0:
          if rsi[idx - 1] <= rsi_ma[idx - 1] and rsi[idx] > rsi_ma[idx]:
            crossed_recently = True
            cross_month_offset = (n - 1) - idx

    if is_currently_bullish and crossed_recently:
      base_asset = symbol[:-3]
      display_pair = f'{base_asset}/BTC'
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        timing_str = (
            'کندل جاری'
            if cross_month_offset == 0
            else f'{cross_month_offset} ماه قبل'
        )

        msg = (
            f'🚀 *MONTHLY RSI CROSSOVER (TOP 500 MARKETCAP)*\n\n'
            f'📌 *جفت‌ارز:* `{display_pair}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت فعلی:* `{closes[-1]:.8f} BTC`\n\n'
            f'📊 *RSI (14):* `{rsi[-1]:.2f}`\n'
            f'📈 *SMA (14):* `{rsi_ma[-1]:.2f}`\n'
            f'🗓 *زمان وقوع کراس:* `{timing_str}`\n\n'
            f'📢 *Channel:* @{CHANNEL_NAME}'
        )
        send_telegram(msg)
        return {
            'symbol': display_pair,
            'price': closes[-1],
            'rsi': rsi[-1],
            'rsi_ma': rsi_ma[-1],
            'month': timing_str,
        }
  except Exception as e:
    print(f'Error analyzing {symbol}: {e}')
  return None


def main():
  print('در حال استخراج ۵۰۰ ارز برتر مارکت کپ و بررسی کراس ۱۲ ماه اخیر...')
  symbols = get_top_500_btc_symbols()
  print(f'تعداد {len(symbols)} جفت‌ارز BTC آماده اسکن گردید.')

  breakout_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      breakout_signals.append(res)
    time.sleep(0.03)

  summary_msg = [
      f'🌐 *گزارش اسکن ۵۰۰ ارز برتر مارکت (جفت‌ارزهای BTC)*\n',
      f'📊 تعداد ارزهای اسکن‌شده: `{len(symbols)}`\n',
  ]

  if breakout_signals:
    summary_msg.append(
        f'🔥 تعداد `{len(breakout_signals)}` جفت‌ارز دارای کراس صعودی ماهانه (در ۱۲ ماه اخیر) هستند:\n'
    )
    for s in breakout_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | قیمت: `{s['price']:.8f} BTC`\n"
          f"     └─ RSI: `{s['rsi']:.1f}` | کراس: `{s['month']}`"
      )
  else:
    summary_msg.append(
        '⚪️ هیچ جفت‌ارزی با شرط کراس صعودی نسبت به BTC در ۱۲ ماه اخیر یافت نشد.'
    )

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن با موفقیت به پایان رسید.')


if __name__ == '__main__':
  main()
