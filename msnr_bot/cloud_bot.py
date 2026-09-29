# -*- coding: utf-8 -*-
"""
بوت الإشارات السحابي (GitHub Actions + Twelve Data) — بيشغّل كل الاستراتيجيات بمجلد strategies/
• الإعدادات بملف strategies.json بجذر المشروع — بتنقرا من GitHub كل 5 دقائق (التعديل بيطبق بدون إعادة تشغيل)
• استراتيجية جديدة = ملف جديد بمجلد strategies/ + قسم بـstrategies.json (شوف strategies/_template.py)
المتغيرات (GitHub Secrets):  TG_TOKEN · TG_CHAT · TD_KEY
"""
import os, sys, json, time, datetime, importlib, urllib.request, urllib.parse, urllib.error, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, ROOT)
import msnr_bot as core

STATE = os.path.join(HERE, 'cloud_state.json')
RUN_MINUTES = float(os.environ.get('RUN_MINUTES', '345'))
TD_KEY = os.environ.get('TD_KEY', '')
SYMBOL = os.environ.get('TD_SYMBOL', 'XAU/USD')
REPO = os.environ.get('GITHUB_REPOSITORY', '')

def log(m): print(f"{datetime.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z  {m}", flush=True)

# ───────────── الأسعار ─────────────
VOL = {}                     # الفوليوم لكل دقيقة (إذا Twelve Data بيوفّره)
POLL_SEC = 120               # كل دقيقتين (الحد المجاني 800 طلب باليوم)
_last_call = [0.0]

def market_closed(ts):
    """الذهب مسكّر: من الجمعة 21:00 لحد الأحد 21:00 (UTC تقريباً)"""
    w = datetime.datetime.utcfromtimestamp(ts)
    return (w.weekday() == 4 and w.hour >= 21) or w.weekday() == 5 or (w.weekday() == 6 and w.hour < 21)
def td_bars(outputsize=5000, end=None):
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
        if v.get('volume') not in (None, ''):
            VOL[t] = float(v['volume'])
    out.sort()
    return out

def load_history(pages=10):
    bars = {}; end = None
    for _ in range(pages):
        chunk = td_bars(5000, end)
        if not chunk: break
        for b in chunk: bars[b[0]] = b
        end = datetime.datetime.utcfromtimestamp(chunk[0][0] - 60).strftime('%Y-%m-%d %H:%M:%S')
    return [bars[k] for k in sorted(bars)]

# ───────────── الاستراتيجيات ─────────────
def read_settings():
    """أحدث نسخة من strategies.json (من GitHub مباشرة، وإذا فشل من الملف المحلي)"""
    if REPO:
        try:
            url = f"https://raw.githubusercontent.com/{REPO}/main/strategies.json?t={int(time.time())}"
            with urllib.request.urlopen(url, timeout=20) as r:
                return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            log(f"settings from GitHub failed ({e}) — using local copy")
    return json.load(open(os.path.join(ROOT, 'strategies.json'), encoding='utf-8'))

_mods = {}
def strategy(name):
    if name not in _mods:
        _mods[name] = importlib.import_module(f"strategies.{name}")
    return _mods[name]

def base_cfg():
    return dict(telegram_token=os.environ['TG_TOKEN'], chat_id=os.environ['TG_CHAT'], symbol='XAUUSD')

def main():
    tgcfg = base_cfg()
    core.LOG_PATH = os.path.join(HERE, 'cloud.log')
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}
    sent = set(state.get('sent', []))
    started = time.time()
    try:
        m1 = load_history()
    except Exception as ex:
        log(f"FAILED to load prices: {ex}")
        core.tg_send(tgcfg, f"⚠ البوت السحابي ما قدر يجيب الأسعار من Twelve Data:\n{ex}\nتأكد من مفتاح TD_KEY")
        raise
    log(f"history {len(m1)} bars · {datetime.datetime.utcfromtimestamp(m1[0][0])} → {datetime.datetime.utcfromtimestamp(m1[-1][0])}")
    known = set(state.get('known', []))            # استراتيجيات شافها البوت قبل (حتى ما يبعت صفقاتها القديمة)
    first_cycle = True; last_bar = {}; last_enabled = None
    while True:
        try:
            if not first_cycle:
                if market_closed(time.time()):
                    if (time.time() - started) / 60 > RUN_MINUTES:
                        log('session done — next run continues'); break
                    time.sleep(600); continue                     # السوق مسكّر: ما في داعي نصرف طلبات
                new = td_bars(30)
                d = {b[0]: b for b in m1[-2000:]}
                for b in new: d[b[0]] = b
                m1 = (m1[:-2000] + [d[k] for k in sorted(d)])[-60000:]
            settings = read_settings()
            enabled = sorted(k for k, v in settings.items() if isinstance(v, dict) and v.get('enabled'))
            if enabled != last_enabled:
                names = ' · '.join(settings[k].get('tag', k) for k in enabled) or 'ولا وحدة'
                if last_enabled is None and not state.get('hello'):
                    core.tg_send(tgcfg, f"☁️ البوت السحابي اشتغل (بدون لابتوب) · XAUUSD 5m\nالاستراتيجيات الشغّالة: {names}")
                    state['hello'] = True
                elif last_enabled is not None:
                    core.tg_send(tgcfg, f"⚙️ تغيّرت الإعدادات · الاستراتيجيات الشغّالة: {names}")
                last_enabled = enabled
            now = time.time(); ran = False
            for name in enabled:
                    if settings[name].get('require_volume') and not VOL:
                        if name not in state.setdefault('novol_note', []):
                            state['novol_note'].append(name)
                            core.tg_send(tgcfg, f"ℹ️ {settings[name].get('tag', name)}: الأسعار السحابية بدون فوليوم، "
                                                f"فهاي الاستراتيجية بتضل شغّالة على اللابتوب بس (مع MT5)")
                        continue
                    tf = int(settings[name].get('tf_min', 5)) * 60      # كل استراتيجية على فريمها
                    cut = (int(now) // tf) * tf
                    done = [x for x in m1 if x[0] < cut]
                    if not done or done[-1][0] == last_bar.get(name):
                        continue
                    last_bar[name] = done[-1][0]; ran = True
                    try:
                        mod = strategy(name)
                        cfg = dict(getattr(mod, 'DEFAULTS', {})); cfg.update(settings[name]); cfg.update(tgcfg)
                        cfg['_vol'] = VOL
                        events = mod.run(done, cfg)
                        fresh = name not in known
                        for e in events:
                            eid = f"{name}:{e['id']}"
                            if eid in sent: continue
                            if fresh or (first_cycle and now - e['t'] > 20 * 60) or now - e['t'] > 3 * 3600:
                                sent.add(eid); continue          # قديم: من قبل تشغيل الاستراتيجية
                            if e['kind'] != 'signal' and not cfg.get('send_fills_and_closes', True):
                                sent.add(eid); continue
                            if core.tg_send(cfg, mod.message(e, cfg)):
                                sent.add(eid); log(f"sent {eid}")
                        known.add(name)
                    except Exception:
                        log(f"strategy {name} failed:\n{traceback.format_exc()}")
            if first_cycle:
                log(f"volume from Twelve Data: {'YES, ' + str(len(VOL)) + ' bars' if VOL else 'NO'}")
            if ran:
                state['sent'] = sorted(sent)[-8000:]; state['known'] = sorted(known)
                json.dump(state, open(STATE, 'w', encoding='utf-8'))
            first_cycle = False
        except Exception as ex:
            log(f"error: {ex}")
        if (time.time() - started) / 60 > RUN_MINUTES:
            log('session done — next run continues'); break
        time.sleep(POLL_SEC - (time.time() % POLL_SEC) + 15)

if __name__ == '__main__':
    main()
