import gc
import json
import os
import sys
import time
import urllib.request

# تنظیم UTF-8 برای رفع کامل مشکل انکودینگ
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات و کانال ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

RSI_LEN = 14
RSI_MA_LEN = 14
LOOKBACK_MONTHS = 5  # بررسی وقوع کراس در ۵ ماه اخیر

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
    urllib.request.urlopen(req, timeout=10)
  except Exception as e:
    print(f'Telegram Error: {e}')


def fetch_json(url):
  try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=12) as response:
      return json.loads(response.read().decode('utf-8'))
  except Exception:
    return None


def get_top_300_coins():
  """دریافت لیست جامع ۳۰۰ ارز برتر بازار از CoinGecko"""
  symbols = []
  for page in [1, 2]:
    url = f'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=150&page={page}&sparkline=false'
    data = fetch_json(url)
    if data and isinstance(data, list):
      for coin in data:
        sym = coin.get('symbol', '').upper()
        if sym and sym not in EXCLUDED_ASSETS:
          pair = f'{sym}USDT'
          if pair not in symbols:
            symbols.append(pair)
    time.sleep(0.2)

  # اطمینان از وجود ارزهای کلیدی
  for force_coin in ['NEARUSDT', 'SOLUSDT', 'BTCUSDT', 'ETHUSDT']:
    if force_coin not in symbols:
      symbols.append(force_coin)

  return symbols


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
  if n <= length:
    return [0.0] * n
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


def fetch_klines(symbol):
  """دریافت کندل‌های ماهانه با پشتیبان چندگانه"""
  url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1M&limit=100'
  klines = fetch_json(url)

  if not klines or not isinstance(klines, list):
    url_f = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1M&limit=100'
    klines = fetch_json(url_f)

  return klines


def analyze_monthly_breakout(symbol):
  try:
    klines = fetch_klines(symbol)
    if not klines or len(klines) < 20:
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]

    rsi = calc_rsi(closes, RSI_LEN)
    rsi_ma = calc_sma(rsi, RSI_MA_LEN)

    n = len(closes)

    # پیمایش و بررسی ۵ ماه اخیر برای یافتن هرگونه کراس رو به بالا
    # از کندل (n - LOOKBACK_MONTHS) تا کندل اخیر (n - 1)
    crossed_recently = False
    cross_month_offset = 0

    for idx in range(n - LOOKBACK_MONTHS, n):
      if idx > 0:
        rsi_prev = rsi[idx - 1]
        rsi_ma_prev = rsi_ma[idx - 1]
        rsi_curr = rsi[idx]
        rsi_ma_curr = rsi_ma[idx]

        # شرط کراس: در کندل قبل زیر MA بوده و در کندل بعدی بالای MA رفته است
        if (rsi_prev <= rsi_ma_prev) and (rsi_curr > rsi_ma_curr):
          crossed_recently = True
          cross_month_offset = (n - 1) - idx
          break

    if crossed_recently:
      clean_symbol = symbol.replace('USDT', '')
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        timing_str = (
          'ماه جاری' if cross_month_offset == 0 else f'{cross_month_offset} ماه قبل'
        )

        msg = (
            f'🚀 *MONTHLY RSI CROSSOVER (وقوع کراس ماهانه)*\n\n'
            f'📌 *ارز:* `{clean_symbol}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت فعلی:* `{closes[-1]:.4f}`\n\n'
            f'📊 *RSI ماهانه:* `{rsi[-1]:.2f}`\n'
            f'📈 *RSI MA (SMA 14):* `{rsi_ma[-1]:.2f}`\n'
            f'🗓 *زمان کراس:* `{timing_str}`\n\n'
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
  print('در حال بررسی کراس ماهانه در ۵ ماه اخیر برای ۳۰۰ ارز برتر...')
  symbols = get_top_300_coins()
  print(f'تعداد {len(symbols)} ارز آماده بررسی شدند.')

  breakout_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      breakout_signals.append(res)
    time.sleep(0.04)

  # ارسال خروجی گزارش کلی
  summary_msg = [f'🌐 *گزارش جامع کراس RSI ماهانه (۵ ماه اخیر)*\n']
  if breakout_signals:
    summary_msg.append(
        f'🔥 تعداد `{len(breakout_signals)}` ارز در ۵ ماه اخیر کراس داشته‌اند:\n'
    )
    for s in breakout_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | Price: `{s['price']:.4f}` | RSI: `{s['rsi']:.1f}`"
          f" | زمان: `{s['month']}`"
      )
  else:
    summary_msg.append(
        '⚪️ هیچ ارزی در ۵ ماه اخیر تقاطع/کراس رو به بالا نداشته است.'
    )

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن کامل شد.')


if __name__ == '__main__':
  main()
