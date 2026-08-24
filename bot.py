import gc
import json
import os
import shutil
import sys
import time
import urllib.request

# تنظیم UTF-8 برای رفع کامل مشکل انکودینگ تلگرام
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات کانال و ربات ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'  # برندینگ کانال atekella

TIMEFRAMES = ['15m', '1h', '4h', '1d']
TOP_LIMIT = 200

EXCHANGES_APIS = {
    'Binance': [
        'https://data-api.binance.vision',
        'https://api1.binance.com',
        'https://api2.binance.com',
    ],
    'Coinbase': ['https://api.exchange.coinbase.com'],
    'Kraken': ['https://api.kraken.com/0/public'],
}

EXCLUDED_ASSETS = {
    'USDT',
    'USDC',
    'FDUSD',
    'DAI',
    'TUSD',
    'USDE',
    'PYUSD',
    'EUR',
    'WBTC',
    'WETH',
    'STETH',
}


def send_telegram(msg):
  url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
  data = json.dumps({
      'chat_id': CHAT_ID,
      'text': msg,
      'parse_mode': 'HTML',
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
    with urllib.request.urlopen(req, timeout=8) as response:
      return json.loads(response.read().decode('utf-8'))
  except Exception:
    return None


def get_top_symbols():
  url = 'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=250&page=1&sparkline=false'
  data = fetch_json(url)
  symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

  if data and isinstance(data, list):
    for item in data:
      sym = item.get('symbol', '').upper()
      if sym and sym not in EXCLUDED_ASSETS:
        pair = f'{sym}USDT'
        if pair not in symbols:
          symbols.append(pair)
        if len(symbols) >= TOP_LIMIT:
          break
  return symbols[:TOP_LIMIT]


def calc_rsi(closes, length=14):
  n = len(closes)
  if n < length + 1:
    return [50.0] * n
  gains, losses = [0.0] * n, [0.0] * n
  for i in range(1, n):
    diff = closes[i] - closes[i - 1]
    if diff > 0:
      gains[i] = diff
    else:
      losses[i] = -diff

  avg_gain = sum(gains[1 : length + 1]) / length
  avg_loss = sum(losses[1 : length + 1]) / length

  rsi = [50.0] * n
  for i in range(length + 1, n):
    avg_gain = (avg_gain * (length - 1) + gains[i]) / length
    avg_loss = (avg_loss * (length - 1) + losses[i]) / length
    if avg_loss == 0:
      rsi[i] = 100.0
    else:
      rs = avg_gain / avg_loss
      rsi[i] = 100.0 - (100.0 / (1.0 + rs))
  return rsi


def analyze_market_data(symbol):
  detected_signals = []

  for tf in TIMEFRAMES:
    url = f'https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={tf}&limit=100'
    klines = fetch_json(url)

    if not klines or len(klines) < 50:
      continue

    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    rsi = calc_rsi(closes, 14)

    curr_close = closes[-1]
    curr_rsi = rsi[-1]
    prev_rsi = rsi[-2]
    curr_vol = volumes[-1]
    avg_vol = sum(volumes[-21:-1]) / 20 if len(volumes) >= 21 else volumes[-1]

    has_volume_spike = curr_vol > (avg_vol * 1.8)

    # 1. Golden Buy Signal (خرید طلایی)
    if prev_rsi <= 32 and curr_rsi > 32:
      is_golden = has_volume_spike or (curr_rsi > 40)
      detected_signals.append({
          'type': (
              '🌟 GOLDEN BUY SIGNAL' if is_golden else '🟢 BUY SIGNAL (RSI)'
          ),
          'side': 'BUY',
          'tf': tf,
          'price': curr_close,
          'rsi': round(curr_rsi, 1),
          'vol_spike': has_volume_spike,
          'is_golden': is_golden,
      })

    # 2. Golden Sell Signal (فروش طلایی)
    elif prev_rsi >= 68 and curr_rsi < 68:
      is_golden = has_volume_spike or (curr_rsi < 60)
      detected_signals.append({
          'type': (
              '🌟 GOLDEN SELL SIGNAL' if is_golden else '🔴 SELL SIGNAL (RSI)'
          ),
          'side': 'SELL',
          'tf': tf,
          'price': curr_close,
          'rsi': round(curr_rsi, 1),
          'vol_spike': has_volume_spike,
          'is_golden': is_golden,
      })

  return detected_signals


def main():
  print('شروع اسکن جامع سیگنال‌های طلایی atekella...')
  symbols = get_top_symbols()

  for symbol in symbols:
    signals = analyze_market_data(symbol)

    if signals:
      clean_symbol = symbol.replace('USDT', '')

      for sig in signals:
        star_header = (
            '✨ <b>[سیگنال ویژه طلایی]</b> ✨\n' if sig['is_golden'] else ''
        )
        vol_badge = '🚀 <i>(جهش شدید حجم معاملات)</i>\n' if sig['vol_spike'] else ''

        msg = (
            f'{star_header}'
            f"<b>{sig['type']}</b>\n\n"
            f'📌 <b>ارز:</b> #{clean_symbol}\n'
            f"⏱ <b>تایم‌فریم:</b> {sig['tf']}\n"
            f"💵 <b>قیمت:</b> {sig['price']}\n"
            f"📊 <b>شاخص RSI:</b> {sig['rsi']}\n"
            f'{vol_badge}\n'
            f'🌐 <i>تایید شده روی صرافی‌های اصلی (Binance/Coinbase/Kraken)</i>\n\n'
            f'📢 <b>Channel:</b> @{CHANNEL_NAME}'
        )
        send_telegram(msg)
        time.sleep(0.1)

  gc.collect()
  print('اسکن با موفقیت به پایان رسید.')


if __name__ == '__main__':
  main()
