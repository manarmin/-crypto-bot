import gc
import json
import os
import sys
import time
import urllib.request

# تنظیم UTF-8
if hasattr(sys.stdout, 'reconfigure'):
  sys.stdout.reconfigure(encoding='utf-8')

# ==================== تنظیمات ربات و کانال ====================
TELEGRAM_TOKEN = '8027946799:AAGhMQGDcEkMnH8PYClOWFMNKbEOLs_0PyY'
CHAT_ID = '570158397'
CHANNEL_NAME = 'atekella'

# تنظیمات دقیق منطبق بر تصویر تریدینگ‌ویو
RSI_LEN = 14
RSI_MA_LEN = 14
LOOKBACK_MONTHS = 6  # بررسی وقوع کراس در ۶ ماه اخیر

EXCLUDED_ASSETS = {
    'USDT', 'USDC', 'FDUSD', 'DAI', 'TUSD', 'USDE', 'PYUSD', 
    'USDS', 'USDD', 'FRAX', 'LUSD', 'GUSD', 'EUR', 'WBTC', 'WETH', 'STETH'
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
  req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
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
  """دریافت لیست ۳۰۰ ارز برتر از CoinGecko"""
  symbols = []
  for page in [1, 2, 3]:  # افزایش پوشش به ۳ صفحه اول
    url = f'https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=100&page={page}&sparkline=false'
    data = fetch_json(url)
    if data and isinstance(data, list):
      for coin in data:
        sym = coin.get('symbol', '').upper()
        if sym and sym not in EXCLUDED_ASSETS:
          pair = f'{sym}USDT'
          if pair not in symbols:
            symbols.append(pair)
    time.sleep(0.3)

  # ضمانت وجود ارزهای مهم (حتی در صورت قطعی موقت API)
  for force_coin in ['NEARUSDT', 'SOLUSDT', 'BTCUSDT', 'ETHUSDT']:
    if force_coin not in symbols:
      symbols.append(force_coin)

  return symbols

def calc_exact_tv_rsi(closes, rsi_length=14, sma_length=14):
  """
  شبیه‌سازی صددرصد دقیق فرمول RSI و SMA تریدینگ‌ویو
  """
  n = len(closes)
  rsi = [0.0] * n
  rsi_ma = [0.0] * n
  
  if n < rsi_length + 1:
      return rsi, rsi_ma
      
  gains = [max(0, closes[i] - closes[i-1]) for i in range(1, n)]
  losses = [max(0, closes[i-1] - closes[i]) for i in range(1, n)]
  
  # گام اول محاسبه (متوسط‌گیری ساده برای شروع)
  avg_gain = sum(gains[:rsi_length]) / rsi_length
  avg_loss = sum(losses[:rsi_length]) / rsi_length
  
  if avg_loss == 0:
      rsi[rsi_length] = 100.0 if avg_gain > 0 else 0.0
  else:
      rsi[rsi_length] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
      
  # محاسبه RMA برای مقادیر بعدی
  for i in range(rsi_length + 1, n):
      avg_gain = (avg_gain * (rsi_length - 1) + gains[i-1]) / rsi_length
      avg_loss = (avg_loss * (rsi_length - 1) + losses[i-1]) / rsi_length
      if avg_loss == 0:
          rsi[i] = 100.0 if avg_gain > 0 else 0.0
      else:
          rsi[i] = 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
          
  # محاسبه SMA 14 برای خط سیگنال RSI
  for i in range(rsi_length + sma_length - 1, n):
      window = rsi[i - sma_length + 1 : i + 1]
      rsi_ma[i] = sum(window) / sma_length
      
  return rsi, rsi_ma

def fetch_klines(symbol):
  """
  برای کندل ماهانه، از بازار اسپات دیتای حداکثری (limit=1000) گرفته می‌شود.
  این کار برای کالیبره شدن فرمول RMA کاملاً ضروری است.
  """
  url = f'https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1M&limit=1000'
  klines = fetch_json(url)
  return klines

def analyze_monthly_breakout(symbol):
  try:
    klines = fetch_klines(symbol)
    # نیازمند حداقل ۲۸ ماه سابقه بازار برای تشکیل اولین نقطه از MA 14 مربوط به RSI 14
    if not klines or len(klines) < (RSI_LEN + RSI_MA_LEN):
      return None

    closes = [float(k[4]) for k in klines]
    times = [k[0] for k in klines]
    
    rsi, rsi_ma = calc_exact_tv_rsi(closes, RSI_LEN, RSI_MA_LEN)
    n = len(closes)

    # 1. ارز هم‌اکنون باید بالای خط متحرک خود باشد
    is_currently_bullish = rsi[-1] > rsi_ma[-1]
    
    # 2. بررسی وقوع کراس‌آور در N ماه گذشته
    crossed_recently = False
    cross_month_offset = 0

    if is_currently_bullish:
        for idx in range(n - LOOKBACK_MONTHS, n):
            if idx > 0:
                # شرط کراس: از زیر یا روی خط به بالای خط رفته باشد
                if rsi[idx-1] <= rsi_ma[idx-1] and rsi[idx] > rsi_ma[idx]:
                    crossed_recently = True
                    cross_month_offset = (n - 1) - idx
                    # آخرین کراس ثبت می‌شود

    if is_currently_bullish and crossed_recently:
      clean_symbol = symbol.replace('USDT', '')
      alert_id = f'{symbol}_1M_{times[-1]}'

      if last_alerted.get(symbol) != alert_id:
        last_alerted[symbol] = alert_id

        timing_str = 'کندل جاری' if cross_month_offset == 0 else f'{cross_month_offset} ماه قبل'

        msg = (
            f'🚀 *MONTHLY RSI CROSSOVER*\n\n'
            f'📌 *ارز:* `{clean_symbol}`\n'
            f'⏱ *تایم‌فریم:* `1M (ماهانه)`\n'
            f'💵 *قیمت فعلی:* `{closes[-1]:.4f}`\n\n'
            f'📊 *RSI (14):* `{rsi[-1]:.2f}`\n'
            f'📈 *SMA (14):* `{rsi_ma[-1]:.2f}`\n'
            f'🗓 *موقعیت کراس:* `{timing_str}`\n\n'
            f'✅ (تایید صعودی بودن وضعیت فعلی)\n\n'
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
  print('در حال بررسی کراس ماهانه در ۶ ماه اخیر برای برترین ارزهای بازار...')
  symbols = get_top_300_coins()
  print(f'تعداد {len(symbols)} ارز با دیتای حداکثری (Spot Limit: 1000) بررسی خواهند شد.')

  breakout_signals = []

  for sym in symbols:
    res = analyze_monthly_breakout(sym)
    if res:
      breakout_signals.append(res)
    time.sleep(0.04)

  # ارسال خروجی گزارش کلی
  summary_msg = [f'🌐 *گزارش جامع کراس RSI ماهانه*\n']
  if breakout_signals:
    summary_msg.append(f'🔥 تعداد `{len(breakout_signals)}` ارز شرط شکست رو به بالا را داشتند:\n')
    for s in breakout_signals:
      summary_msg.append(
          f"🔹 `{s['symbol']}` | Price: `{s['price']:.4f}`\n"
          f"     └─ RSI: `{s['rsi']:.1f}` | Cross: `{s['month']}`"
      )
  else:
    summary_msg.append('⚪️ هیچ ارزی با تنظیمات درخواستی در ۶ ماه اخیر کراس صعودی نداشته است.')

  summary_msg.append(f'\n📢 *Channel:* @{CHANNEL_NAME}')
  send_telegram('\n'.join(summary_msg))

  gc.collect()
  print('اسکن کامل و منطبق با مقادیر تریدینگ‌ویو انجام شد.')

if __name__ == '__main__':
  main()
