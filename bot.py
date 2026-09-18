import gc
import json
import os
import sys
import time
import urllib.request

# تنظیم UTF-8 برای پشتیبانی کامل از کاراکترهای فارسی
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات و کانال ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

# تنظیمات شاخص‌ها
RSI_LEN = 14
RSI_MA_LEN = 14
TIMEFRAME = '1M'  # تایم‌فریم ماهانه

BINANCE_SPOT_URLS = [
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
]
BINANCE_FUTURES_URLS = ['https://fapi.binance.com']

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


def get_all_target_symbols():
  """استخراج تمام نمادهای فیوچرز بایننس + ۳۰۰ ارز برتر کوین‌گکو"""
  symbols = set()

  # ۱. دریافت تمام ارزهای بایننس فیوچرز (USDT-M Futures)
  f_data = fetch_json('https://fapi.binance.com/fapi/v1/exchangeInfo')
  if f_data and 'symbols' in f_data:
    for item in f_data['symbols']:
      if (
          item.get('quoteAsset') == 'USDT'
          and item.get('status') == 'TRADING'
          and item.get('contractType') == 'PERPETUAL'
      ):
        base = item.get('baseAsset')
        if base not in EXCLUDED_ASSETS:
          symbols.add(item['symbol'])

  # ۲. دریافت ۳۰۰ ارز برتر بازار از CoinGecko (صفحه ۱ و ۲)
  for page in [1, 2]:
    cg_url = f'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page={page}&sparkline=false'
    cg_data = fetch_json(cg_url)
    if cg_data and isinstance(cg_data, list):
      for coin in cg_data:
        sym = coin.get('symbol', '').upper()
        if sym and sym not in EXCLUDED_ASSETS:
          symbols.add(f'{sym}USDT')
    time.sleep(0.2)

  return sorted(list(symbols))


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


def fetch_monthly_klines(symbol):
  """دریافت کندل‌های ماهانه (ابتدا از فیوچرز و در صورت عدم وجود از اسپات)"""
  # ۱. تلاش از Binance Futures
  url_f = f'https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1M&limit=50'
  klines = fetch_json(url_f)

  # ۲. در صورت عدم دریافت، تلاش از Binance Spot
  if not klines or isinstance(klines, dict):
    url_s = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1M&limit=50'
    klines = fetch_json(url_s)

  return klines


def analyze_monthly_breakout(symbol):
  try:
    klines = fetch_monthly_klines(symbol)
    # برای محاسبه RSI 14 و SMA 14 حداقل به ۳۰ کندل ماهانه نیاز است
    if not klines or len(klines) < 30:
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]

    rsi = calc_rsi(closes, RSI_LEN)
    rsi_ma = calc_sma(rsi, RSI_MA_LEN)

    # بررسی ۲ کندل آخر (کندل قبلی و کندل جاری/بسته شده اخیر)
    # کندل -2 (ماه قبل) و کندل -1 (ماه جاری/تازه بسته شده)
    rsi_prev, rsi_ma_prev = rsi[-2], rsi_ma[-2]
    rsi_curr, rsi_ma_curr = rsi[-1], rsi_ma[-1]

    # شرط ۱: تقاطع/شکست به بالا در کندل ماهانه
    # شرط ۲: تثبیت کندل ماهانه بالاتری بالای RSI MA
    is_confirmed_breakout = (rsi_prev > rsi_ma_prev) and (
        rsi_curr > rsi_ma_curr
    )

    if is_confirmed_breakout:
      clean_symbol = symbol.replace('USDT', '')
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        msg = (
            f'🚀 *MONTHLY RSI BREAKOUT & CONFIRMED*\n\n'
            f'📌 *ارز:* `{clean_symbol}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت فعلی:* `{closes[-1]:.4f}`\n\n'
            f'📊 *RSI ماهانه:* `{rsi_curr:.2f}`\n'
            f'📈 *RSI MA (SMA 14):* `{rsi_ma_curr:.2f}`\n\n'
            f'✅ *وضعیت:* شکست رو به بالا + تثبیت کندل ماهانه\n\n'
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
  print('در حال دریافت لیست تمام ارزهای فیوچرز بایننس و ۳۰۰ ارز برتر...')
  symbols = get_all_target_symbols()
  print(f'تعداد {len(symbols)} ارز برای تحلیل ماهانه شناسایی شدند.')

  confirmed_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      confirmed_signals.append(res)
    time.sleep(0.04)  # رعایت Rate Limit صرافی

  # ارسال پیام خلاصه به تلگرام
  summary_msg = [f'🌐 *گزارش اسکن RSI ماهانه (Monthly Breakout)*\n']
  if confirmed_signals:
    summary_msg.append(
        f'🔥 تعداد `{len(confirmed_signals)}` ارز دارای شکست و تثبیت RSI'
        ' ماهانه هستند:\n'
    )
    for s in confirmed_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | Price: `{s['price']:.4f}` | RSI:"
          f" `{s['rsi']:.1f}`"
      )
  else:
    summary_msg.append(
        '⚪️ هیچ ارزی در تایم‌فریم ماهانه شرط شکست و تثبیت RSI بالای SMA14 را'
        ' برآورده نکرد.'
    )

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن ماهانه با موفقیت به پایان رسید.')


if __name__ == '__main__':
  main()
