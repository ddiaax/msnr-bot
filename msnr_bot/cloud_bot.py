# -*- coding: utf-8 -*-
"""
MSNR Bot — نسخة السحابة (GitHub Actions + Twelve Data)
بيشتغل بدون لابتوب وبدون MT5. نفس محرك msnr_bot.py بالضبط.
المتغيرات (GitHub Secrets):  TG_TOKEN · TG_CHAT · TD_KEY
"""
import os, sys, json, time, datetime, urllib.request, urllib.parse, urllib.error
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import msnr_bot as core

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, 'cloud_state.json')
RUN_MINUTES = float(os.environ.get('RUN_MINUTES', '345'))    # GitHub بيسمح بـ6 ساعات للجلسة
TD_KEY = os.environ.get('TD_KEY', '')
SYMBOL = os.environ.get('TD_SYMBOL', 'XAU/USD')

def log(m): print(f"{datetime.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z  {m}", flush=True)

_last_call = [0.0]
def td_bars(outputsize=5000, end=None):
    """شموع دقيقة من Twelve Data (UTC) → [(t,o,h,l,c)] من الأقدم للأحدث"""
    wait = 8.5 - (time.time() - _last_call[0])      # الحد المجاني 8 طلبات بالدقيقة
    if wait > 0: time.sleep(wait)
    q = dict(symbol=SYMBOL, interval='1min', outputsize=outputsize, timezone='UTC', apikey=TD_KEY, order='ASC')
    if end: q['end_date'] = end
    url = 'https://api.twelvedata.com/time_series?' + urllib.parse.urlencode(q)
    _last_call[0] = time.time()
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try: d = json.loads(e.read().decode('utf-8'))
        except Exception: d = {'message': f'HTTP {e.code}'}
        raise RuntimeError(f"Twelve Data rejected the request: {d.get('message', d)}")
    if d.get('status') != 'ok':
        raise RuntimeError(f"Twelve Data: {d.get('message', d)}")
    out = []
    for v in d['values']:
        t = int(datetime.datetime.strptime(v['datetime'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
        out.append((t, float(v['open']), float(v['high']), float(v['low']), float(v['close'])))
    out.sort()
    return out

def load_history(pages=10):
    """حوالي 7 أسابيع من شموع الدقيقة (10 طلبات × 5000)"""
    bars = {}; end = None
    for i in range(pages):
        chunk = td_bars(5000, end)
        if not chunk: break
        for b in chunk: bars[b[0]] = b
        end = datetime.datetime.utcfromtimestamp(chunk[0][0] - 60).strftime('%Y-%m-%d %H:%M:%S')
    return [bars[k] for k in sorted(bars)]

def main():
    cfg = dict(core.DEFAULT_CFG)
    cfg.update(telegram_token=os.environ['TG_TOKEN'], chat_id=os.environ['TG_CHAT'], symbol='XAUUSD')
    core.LOG_PATH = os.path.join(HERE, 'cloud.log')
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}
    sent = set(state.get('sent', []))
    started = time.time()
    try:
        m1 = load_history()
    except Exception as ex:
        log(f"FAILED to load prices: {ex}")
        core.tg_send(cfg, f"[{cfg['tag']}] ⚠ البوت السحابي ما قدر يجيب الأسعار من Twelve Data:\n{ex}\nتأكد من مفتاح TD_KEY")
        raise
    log(f"history {len(m1)} bars · {datetime.datetime.utcfromtimestamp(m1[0][0])} → {datetime.datetime.utcfromtimestamp(m1[-1][0])}")
    first_cycle = True; last_bar = None
    if not state.get('hello'):
        core.tg_send(cfg, f"[{cfg['tag']}] ☁️ بوت MSNR اشتغل على السحابة (بدون لابتوب) · XAUUSD 5m")
        state['hello'] = True
    while True:
        try:
            if not first_cycle:
                new = td_bars(30)
                d = {b[0]: b for b in m1[-2000:]}
                for b in new: d[b[0]] = b
                m1 = m1[:-2000] + [d[k] for k in sorted(d)]
                m1 = m1[-60000:]
            cut = (int(time.time()) // 300) * 300          # شموع مكتملة فقط
            done = [x for x in m1 if x[0] < cut]
            if done and done[-1][0] != last_bar:
                last_bar = done[-1][0]
                events, b1 = core.run_engine(done, cfg)
                now = time.time()
                for e in events:
                    if e['id'] in sent: continue
                    if first_cycle and now - e['t'] > 20 * 60:     # قديم: من قبل ما يشتغل البوت
                        sent.add(e['id']); continue
                    if not first_cycle and now - e['t'] > 3 * 3600:
                        sent.add(e['id']); continue
                    if e['kind'] != 'signal' and not cfg['send_fills_and_closes']:
                        sent.add(e['id']); continue
                    if core.tg_send(cfg, core.message(e, cfg).replace('XAUUSD.s', 'XAUUSD')):
                        sent.add(e['id']); log(f"sent {e['kind']} {e['id']}")
                first_cycle = False
                state['sent'] = sorted(sent)[-5000:]
                json.dump(state, open(STATE, 'w', encoding='utf-8'))
        except Exception as ex:
            log(f"error: {ex}")
        if (time.time() - started) / 60 > RUN_MINUTES:
            log('session done — next run continues'); break
        time.sleep(300 - (time.time() % 300) + 20)          # 20 ثانية بعد إغلاق شمعة الـ5 دقائق

if __name__ == '__main__':
    main()
