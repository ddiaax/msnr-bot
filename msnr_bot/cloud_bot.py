# -*- coding: utf-8 -*-
"""
بوت الإشارات السحابي (GitHub Actions + Twelve Data) — بيشغّل كل الاستراتيجيات بمجلد strategies/ على أكثر من رمز
• الإعدادات بملف strategies.json بجذر المشروع — بتنقرا من GitHub كل دورة (التعديل بيطبق بدون إعادة تشغيل)
• كل قسم إله: module (ملف الاستراتيجية) · td_symbol (رمز Twelve Data) · symbol (الاسم بالرسالة) · td_hours_ny (ساعات السوق)
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
REPO = os.environ.get('GITHUB_REPOSITORY', '')
DEFAULT_TD = 'XAU/USD'

def log(m): print(f"{datetime.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z  {m}", flush=True)

# ───────────── الأسعار ─────────────
_last_call = [0.0]

def gold_closed(ts):
    """الذهب مسكّر: من الجمعة 21:00 لحد الأحد 21:00 (UTC تقريباً)"""
    w = datetime.datetime.utcfromtimestamp(ts)
    return (w.weekday() == 4 and w.hour >= 21) or w.weekday() == 5 or (w.weekday() == 6 and w.hour < 21)

def feed_open(ts, hours_ny):
    """هل في داعي نجيب أسعار هالرمز هلأ؟ (بنوفّر طلبات Twelve Data)"""
    if hours_ny is None:
        return not gold_closed(ts)
    ny = datetime.datetime.utcfromtimestamp(ts + core.nyoff(ts))
    h = ny.hour + ny.minute / 60
    return ny.weekday() < 5 and hours_ny[0] - 0.25 <= h < hours_ny[1] + 0.25

def td_bars(symbol, vol, outputsize=5000, end=None):
    wait = 8.5 - (time.time() - _last_call[0])      # الحد المجاني 8 طلبات بالدقيقة
    if wait > 0: time.sleep(wait)
    q = dict(symbol=symbol, interval='1min', outputsize=outputsize, timezone='UTC', apikey=TD_KEY, order='ASC')
    if end: q['end_date'] = end
    url = 'https://api.twelvedata.com/time_series?' + urllib.parse.urlencode(q)
    _last_call[0] = time.time()
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            d = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        try: d = json.loads(e.read().decode('utf-8'))
        except Exception: d = {'message': f'HTTP {e.code}'}
        raise RuntimeError(f"Twelve Data rejected {symbol}: {d.get('message', d)}")
    if d.get('status') != 'ok':
        raise RuntimeError(f"Twelve Data {symbol}: {d.get('message', d)}")
    out = []
    for v in d['values']:
        t = int(datetime.datetime.strptime(v['datetime'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
        out.append((t, float(v['open']), float(v['high']), float(v['low']), float(v['close'])))
        if v.get('volume') not in (None, '') and float(v['volume']) > 0:
            vol[t] = float(v['volume'])
    out.sort()
    return out

def load_history(symbol, vol, pages=10):
    bars = {}; end = None
    for _ in range(pages):
        chunk = td_bars(symbol, vol, 5000, end)
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
def strategy(module):
    if module not in _mods:
        _mods[module] = importlib.import_module(f"strategies.{module}")
    return _mods[module]

def main():
    tgcfg = dict(telegram_token=os.environ['TG_TOKEN'], chat_id=os.environ['TG_CHAT'])
    core.LOG_PATH = os.path.join(HERE, 'cloud.log')
    state = json.load(open(STATE, encoding='utf-8')) if os.path.exists(STATE) else {}
    sent = set(state.get('sent', []))
    known = set(state.get('known', []))            # استراتيجيات شافها البوت قبل (حتى ما يبعت صفقاتها القديمة)
    started = time.time()
    feeds = {}                                     # td_symbol → dict(m1, vol, ok, hours)
    first_cycle = True; last_bar = {}; last_enabled = None; loaded_any = False
    while True:
        poll = 300
        try:
            settings = read_settings()
            enabled = sorted(k for k, v in settings.items() if isinstance(v, dict) and v.get('enabled'))
            # ── تحميل/تحديث أسعار كل رمز مطلوب
            need = {}
            for n in enabled:
                s = settings[n]; need.setdefault(s.get('td_symbol', DEFAULT_TD), s.get('td_hours_ny'))
            for sym, hrs in need.items():
                F = feeds.get(sym)
                if F is None:
                    F = feeds[sym] = dict(m1=[], vol={}, ok=False, hours=hrs, tried=False)
                F['hours'] = hrs
                try:
                    if not F['tried']:
                        F['tried'] = True
                        F['m1'] = load_history(sym, F['vol']); F['ok'] = bool(F['m1'])
                        if F['ok']:
                            loaded_any = True
                            log(f"{sym}: history {len(F['m1'])} bars · {datetime.datetime.utcfromtimestamp(F['m1'][0][0])} → "
                                f"{datetime.datetime.utcfromtimestamp(F['m1'][-1][0])} · volume: {'YES' if F['vol'] else 'NO'}")
                    elif F['ok'] and feed_open(time.time(), hrs):
                        new = td_bars(sym, F['vol'], 30)
                        d = {b[0]: b for b in F['m1'][-2000:]}
                        for b in new: d[b[0]] = b
                        F['m1'] = (F['m1'][:-2000] + [d[k] for k in sorted(d)])[-60000:]
                except Exception as ex:
                    log(f"{sym}: {ex}")
                    if not F['ok']:
                        key = f"feedfail:{sym}"
                        if key not in state.setdefault('notes', []):
                            state['notes'].append(key)
                            users = ' · '.join(settings[n].get('tag', n) for n in enabled if settings[n].get('td_symbol', DEFAULT_TD) == sym)
                            core.tg_send(tgcfg, f"⚠ ما قدرت أجيب أسعار {sym} من Twelve Data:\n{ex}\n"
                                                f"الاستراتيجيات المتأثرة: {users}")
            if not loaded_any and first_cycle:
                raise SystemExit("no price feed available")
            # ── رسائل التشغيل / تغيّر الإعدادات
            if enabled != last_enabled:
                names = ' · '.join(settings[k].get('tag', k) for k in enabled) or 'ولا وحدة'
                if last_enabled is None and not state.get('hello'):
                    core.tg_send(tgcfg, f"☁️ البوت السحابي اشتغل (بدون لابتوب)\nالاستراتيجيات الشغّالة: {names}")
                    state['hello'] = True
                elif last_enabled is not None:
                    core.tg_send(tgcfg, f"⚙️ تغيّرت الإعدادات · الاستراتيجيات الشغّالة: {names}")
                last_enabled = enabled
            # ── تشغيل كل استراتيجية على رمزها وفريمها
            now = time.time(); ran = False
            for name in enabled:
                s = settings[name]
                F = feeds.get(s.get('td_symbol', DEFAULT_TD))
                if not F or not F['ok']:
                    continue
                if s.get('require_volume') and not F['vol']:
                    key = f"novol:{name}"
                    if key not in state.setdefault('notes', []):
                        state['notes'].append(key)
                        core.tg_send(tgcfg, f"ℹ️ {s.get('tag', name)}: الأسعار السحابية بدون فوليوم، "
                                            f"فهاي الاستراتيجية بتضل شغّالة على اللابتوب بس (مع MT5)")
                    continue
                tfm = int(s.get('tf_min', 5)); poll = min(poll, max(120, tfm * 60))
                tf = tfm * 60
                cut = (int(now) // tf) * tf
                done = [x for x in F['m1'] if x[0] < cut]
                if not done or done[-1][0] == last_bar.get(name):
                    continue
                last_bar[name] = done[-1][0]; ran = True
                try:
                    mod = strategy(s.get('module', name))
                    cfg = dict(getattr(mod, 'DEFAULTS', {})); cfg.update(s); cfg.update(tgcfg)
                    cfg['_vol'] = F['vol']
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
            if ran or first_cycle:
                state['sent'] = sorted(sent)[-8000:]; state['known'] = sorted(known)
                json.dump(state, open(STATE, 'w', encoding='utf-8'))
            first_cycle = False
        except SystemExit:
            raise
        except Exception as ex:
            log(f"error: {ex}")
        if (time.time() - started) / 60 > RUN_MINUTES:
            log('session done — next run continues'); break
        time.sleep(poll - (time.time() % poll) + 15)

if __name__ == '__main__':
    main()
