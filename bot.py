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
  """دریافت ۳۰۰ ارز برتر بازار از CoinGecko"""
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

  # ضمانت وجود NEAR و ارزهای اصلی در لیست
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
  # اولویت ۱: بایننس اسپات
  url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1M&limit=100'
  klines = fetch_json(url)

  # اولویت ۲: بایننس فیوچرز
  if not klines or not isinstance(klines, list):
    url_f = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1M&limit=100'
    klines = fetch_json(url_f)

  return klines


def analyze_monthly_breakout(symbol):
  try:
    klines = fetch_klines(symbol)
    # تقلیل شرط حداقل کندل به ۲۰ برای پوشش ارزهای تازه‌تر
    if not klines or len(klines) < 20:
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]

    rsi = calc_rsi(closes, RSI_LEN)
    rsi_ma = calc_sma(rsi, RSI_MA_LEN)

    rsi_prev, rsi_ma_prev = rsi[-2], rsi_ma[-2]
    rsi_curr, rsi_ma_curr = rsi[-1], rsi_ma[-1]

    # **فقط شرط شکست رو به بالا (CROSSOVER):**
    # در کندل قبلی RSI زیر MA بوده و در کندل ماه جاری به بالای MA آمده است
    is_breakout = (rsi_prev <= rsi_ma_prev) and (rsi_curr > rsi_ma_curr)

    if is_breakout:
      clean_symbol = symbol.replace('USDT', '')
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        msg = (
            f'🚀 *MONTHLY RSI BREAKOUT (شکست ماهانه)*\n\n'
            f'📌 *ارز:* `{clean_symbol}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت:* `{closes[-1]:.4f}`\n\n'
            f'📊 *RSI ماهانه:* `{rsi_curr:.2f}`\n'
            f'📈 *RSI MA (SMA 14):* `{rsi_ma_curr:.2f}`\n'
            f'🔻 *RSI ماه قبل:* `{rsi_prev:.2f}` (زیر MA)\n\n'
            f'⚡️ *وضعیت:* وقوع شکست تازه رو به بالا\n\n'
            f'📢 *Channel:* @{CHANNEL_NAME}'
        )
        send_telegram(msg)
        return {
            'symbol': clean_symbol,
            'price': closes[-1],
            'rsi': rsi_curr,
            'rsi_ma': rsi_ma_curr,
        }
  except Exception as e:
    print(f'Error analyzing {symbol}: {e}')
  return None


def main():
  print('در حال دریافت لیست ۳۰۰ ارز برتر از CoinGecko...')
  symbols = get_top_300_coins()
  print(f'تعداد {len(symbols)} ارز برای بررسی کندل ماهانه آماده شد.')

  breakout_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      breakout_signals.append(res)
    time.sleep(0.04)

  # ارسال خروجی کلی اسکن
  summary_msg = [f'🌐 *گزارش اسکن شکست RSI ماهانه*\n']
  if breakout_signals:
    summary_msg.append(
        f'🔥 تعداد `{len(breakout_signals)}` ارز دارای شکست RSI ماهانه بودند:\n'
    )
    for s in breakout_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | Price: `{s['price']:.4f}` | RSI:"
          f" `{s['rsi']:.1f}`"
      )
  else:
    summary_msg.append(
        '⚪️ هیچ ارزی در کندل ماه جاری تقاطع/شکست رو به بالا (Crossover) نداشت.'
    )

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن با موفقیت به پایان رسید.')


if __name__ == '__main__':
  main()
