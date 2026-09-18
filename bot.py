import gc
import json
import os
import sys
import time
import urllib.request

# تنظیم UTF-8 برای پشتیبانی از زبان فارسی
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

RSI_LEN = 14
RSI_MA_LEN = 14
LOOKBACK_MONTHS = 6  # بررسی وقوع کراس در ۶ ماه اخیر

# اندپوینت‌های جهانی بایننس برای دور زدن تحریم IP سرورهای گیت‌هاب
BINANCE_ENDPOINTS = [
    'https://data-api.binance.vision',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api.binance.com',
]

EXCLUDED_ASSETS = {
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


def fetch_binance(path):
  """ارسال درخواست به اندپوینت‌های متعدد برای رد کردن تحریم جغرافیایی IP"""
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


def get_top_300_coins():
  """استخراج ۳۰۰ ارز برتر بر اساس حجم معاملاتی ۲۴ ساعته از صرافی"""
  ticker_data = fetch_binance('/api/v3/ticker/24hr')
  symbols = []

  if ticker_data and isinstance(ticker_data, list):
    valid_tickers = []
    for item in ticker_data:
      sym = item.get('symbol', '')
      if sym.endswith('USDT'):
        base = sym.replace('USDT', '')
        if base not in EXCLUDED_ASSETS and not is_leveraged(sym):
          quote_vol = float(item.get('quoteVolume', 0))
          valid_tickers.append((sym, quote_vol))

    # مرتب‌سازی بر اساس بیشترین حجم دلاری معاملاتی
    valid_tickers.sort(key=lambda x: x[1], reverse=True)
    symbols = [t[0] for t in valid_tickers[:300]]

  # ضمانت وجود ارزهای شاخص
  for force_coin in ['NEARUSDT', 'SOLUSDT', 'BTCUSDT', 'ETHUSDT']:
    if force_coin not in symbols:
      symbols.append(force_coin)

  return symbols


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

  # محاسبه هموارسازی RMA برای سایر نقاط
  for i in range(rsi_length + 1, n):
    avg_gain = (avg_gain * (rsi_length - 1) + gains[i - 1]) / rsi_length
    avg_loss = (avg_loss * (rsi_length - 1) + losses[i - 1]) / rsi_length
    if avg_loss == 0:
      rsi[i] = 100.0 if avg_gain > 0 else 0.0
    else:
      rsi[i] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

  # محاسبه SMA 14 برای RSI
  for i in range(rsi_length + sma_length - 1, n):
    window = rsi[i - sma_length + 1 : i + 1]
    rsi_ma[i] = sum(window) / sma_length

  return rsi, rsi_ma


def analyze_monthly_breakout(symbol):
  try:
    # دریافت دیتای ماهانه اسپات با بیشترین تاریخچه ممکن (1000 کندل)
    klines = fetch_binance(
        f'/api/v3/klines?symbol={symbol}&interval=1M&limit=1000'
    )
    if not klines or len(klines) < (RSI_LEN + RSI_MA_LEN + 2):
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]

    rsi, rsi_ma = calc_exact_tv_rsi(closes, RSI_LEN, RSI_MA_LEN)
    n = len(closes)

    # وضعیت صعودی فعلی
    is_currently_bullish = rsi[-1] > rsi_ma[-1]

    crossed_recently = False
    cross_month_offset = 0

    if is_currently_bullish:
      # بررسی وقوع کراس در ۶ کندل اخیر
      start_idx = max(RSI_LEN + RSI_MA_LEN, n - LOOKBACK_MONTHS)
      for idx in range(start_idx, n):
        if idx > 0:
          # شرط تقاطع: RSI کندل قبلی <= MA و RSI کندل فعلی > MA
          if rsi[idx - 1] <= rsi_ma[idx - 1] and rsi[idx] > rsi_ma[idx]:
            crossed_recently = True
            cross_month_offset = (n - 1) - idx

    if is_currently_bullish and crossed_recently:
      clean_symbol = symbol.replace('USDT', '')
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        timing_str = (
            'کندل جاری'
            if cross_month_offset == 0
            else f'{cross_month_offset} ماه قبل'
        )

        msg = (
            f'🚀 *MONTHLY RSI CROSSOVER*\n\n'
            f'📌 *ارز:* `{clean_symbol}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت فعلی:* `{closes[-1]:.4f}`\n\n'
            f'📊 *RSI (14):* `{rsi[-1]:.2f}`\n'
            f'📈 *SMA (14):* `{rsi_ma[-1]:.2f}`\n'
            f'🗓 *زمان وقوع کراس:* `{timing_str}`\n\n'
            f'📢 *Channel:* @{CHANNEL_NAME}'
        )
        send_telegram(msg)
        return {
            'symbol': clean_symbol,
            'price': closes[-1],
            'rsi': rsi[-1],
            'rsi_ma': rsi_ma[-1],
            'month': timing_str,
        }
  except Exception as e:
    print(f'Error analyzing {symbol}: {e}')
  return None


def main():
  print('در حال استخراج ۳۰۰ ارز برتر و بررسی کراس ماهانه...')
  symbols = get_top_300_coins()
  print(f'تعداد {len(symbols)} ارز جهت بررسی شناسایی شد.')

  breakout_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      breakout_signals.append(res)
    time.sleep(0.03)

  # ساخت پیام گزارش نهایی اسکن
  summary_msg = [f'🌐 *گزارش اسکن کراس RSI ماهانه*\n']
  summary_msg.append(f'📊 تعداد ارزهای اسکن‌شده: `{len(symbols)}`\n')

  if breakout_signals:
    summary_msg.append(
        f'🔥 تعداد `{len(breakout_signals)}` ارز دارای کراس صعودی ماهانه هستند:\n'
    )
    for s in breakout_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | قیمت: `{s['price']:.4f}`\n"
          f"     └─ RSI: `{s['rsi']:.1f}` | کراس: `{s['month']}`"
      )
  else:
    summary_msg.append(
        '⚪️ هیچ ارزی با شرط کراس صعودی در ۶ ماه اخیر یافت نشد.'
    )

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن کامل شد.')


if __name__ == '__main__':
  main()
