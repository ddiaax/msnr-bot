# -*- coding: utf-8 -*-
"""
MSNR Telegram Bot — نفس شروط مؤشر MSNR Scalper (الطريقة الثابتة)
يقرأ أسعار الذهب من MetaTrader 5، ويبعت على تيليجرام:
  • صفقة جديدة (أمر معلّق أو دخول مباشر ⚡) مع الدخول والهدف والستوب
  • تنفيذ الأمر المعلّق
  • إغلاق الصفقة (هدف / ستوب) أو إلغاء الأمر
الإعدادات في config.json
"""
import json, os, sys, time, datetime, urllib.request, urllib.error, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CFG_PATH = os.path.join(HERE, 'config.json')
STATE_PATH = os.path.join(HERE, 'state.json')
LOG_PATH = os.path.join(HERE, 'bot.log')

DEFAULT_CFG = {
    "telegram_token": "",
    "chat_id": "",
    "tag": "MSNR",
    "symbol": "XAUUSD.s",
    "pip": 0.1,
    "stop_pips": 50,
    "tp_min": 100,
    "tp_max": 200,
    "max_positions": 4,
    "max_losses_per_day": 2,
    "trend_1h_filter": True,
    "use_crt_target": True,
    "auto_market_entry": True,
    "strong_body_atr": 1.5,
    "pending_valid_min": 180,
    "mss_window_min": 120,
    "send_fills_and_closes": True,
}

# ───────────────────────── أدوات ─────────────────────────
def log(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, 'a', encoding='utf-8') as f: f.write(line + '\n')
    except Exception: pass

def load_cfg():
    if not os.path.exists(CFG_PATH):
        json.dump(DEFAULT_CFG, open(CFG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    cfg = dict(DEFAULT_CFG); cfg.update(json.load(open(CFG_PATH, encoding='utf-8')))
    return cfg

def save_cfg(cfg):
    json.dump(cfg, open(CFG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

def tg_call(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload or {}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode('utf-8'))

def tg_send(cfg, text):
    for attempt in range(3):
        try:
            tg_call(cfg['telegram_token'], 'sendMessage', {'chat_id': cfg['chat_id'], 'text': text})
            return True
        except Exception as e:
            log(f"Telegram error: {e}"); time.sleep(3)
    return False

def discover_chat_id(cfg):
    """أول تشغيل: يلقط الـChat ID من آخر رسالة انبعتت للبوت"""
    try:
        r = tg_call(cfg['telegram_token'], 'getUpdates', {'limit': 20})
    except urllib.error.HTTPError as e:
        log(f"getUpdates failed ({e.code}). إذا الرقم 409 يعني في برنامج ثاني بيستخدم نفس البوت — حط chat_id بإيدك بـconfig.json")
        return None
    for u in reversed(r.get('result', [])):
        m = u.get('message') or u.get('channel_post') or u.get('my_chat_member') or {}
        chat = m.get('chat')
        if chat: return str(chat['id'])
    return None

# ───────────────────────── الوقت ─────────────────────────
def _nth_sunday(y, m, n):
    d = datetime.date(y, m, 1); d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)
_dst = {}
def nyoff(t):
    y = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).year
    if y not in _dst:
        a = _nth_sunday(y, 3, 2); b = _nth_sunday(y, 11, 1)
        ts = lambda d, h: int(datetime.datetime(d.year, d.month, d.day, h, tzinfo=datetime.timezone.utc).timestamp())
        _dst[y] = (ts(a, 7), ts(b, 6))
    a, b = _dst[y]
    return -4 * 3600 if a <= t < b else -5 * 3600

def fx_day(t):   # يوم التداول يبدأ 17:00 نيويورك
    return datetime.datetime.fromtimestamp(t + nyoff(t) + 7 * 3600, datetime.timezone.utc).strftime('%Y-%m-%d')

def ny_str(t):
    return datetime.datetime.fromtimestamp(t + nyoff(t), datetime.timezone.utc).strftime('%m-%d %H:%M')

def resample(m1, sec, shift=0):
    out = []; cur = None
    for t, o, h, l, c in m1:
        off = nyoff(t); k = (t + off + shift) // sec
        if cur is None or k != cur[0]:
            if cur: out.append(tuple(cur[1:]))
            cur = [k, k * sec - off - shift, o, h, l, c]
        else:
            if h > cur[3]: cur[3] = h
            if l < cur[4]: cur[4] = l
            cur[5] = c
    if cur: out.append(tuple(cur[1:]))
    return out

def pivots(closes, n=2):
    ph = [None] * len(closes); pl = [None] * len(closes)
    for i in range(2 * n, len(closes)):
        c = closes[i - n]; L = closes[i - 2 * n:i - n]; R = closes[i - n + 1:i + 1]
        if all(c > x for x in L) and all(c > x for x in R): ph[i] = c
        if all(c < x for x in L) and all(c < x for x in R): pl[i] = c
    return ph, pl

# ───────────────────────── المحرك ─────────────────────────
class Level:
    __slots__ = ('price', 'role', 'kind', 't0', 'bar0', 'breaks', 'touched', 'dead', 'q', 'qHead', 'qSH', 'qLS', 'qBar')
    def __init__(s, price, role, kind, t0, bar0):
        s.price = price; s.role = role; s.kind = kind; s.t0 = t0; s.bar0 = bar0
        s.breaks = 0; s.touched = False; s.dead = False; s.q = False; s.qHead = s.qSH = s.qLS = None; s.qBar = 0

class Engine:
    def __init__(s, bars, tag):
        s.bars = bars; s.tag = tag; s.levels = []
        s.lastSH = s.lastSL = None
        s.ph, s.pl = pivots([b[4] for b in bars])
        s.times = [b[0] for b in bars]; s.ptr = 0
    def advance(s, t):
        k = s.ptr
        while k + 1 < len(s.times) and s.times[k + 1] <= t: k += 1
        new = k != s.ptr; s.ptr = k
        return k if new else None
    def process(s, k, bi, pip):
        if k < 2: return
        t1, o1, h1, l1, c1 = s.bars[k - 1]
        t2, o2, h2, l2, c2 = s.bars[k - 2]
        lp = None
        if c2 > o2 and c1 < o1: lp, role, kind = c2, -1, 'A'
        elif c2 < o2 and c1 > o1: lp, role, kind = c2, 1, 'V'
        elif c2 > o2 and c1 > o1: lp, role, kind = c2, 1, 'Gap'
        elif c2 < o2 and c1 < o1: lp, role, kind = c2, -1, 'Gap'
        if lp is not None and not any((not L.dead) and L.role == role and abs(L.price - lp) <= pip for L in s.levels):
            s.levels.append(Level(lp, role, kind, t2, bi))
            if len(s.levels) > 60: s.levels.pop(0)
        if s.ph[k - 1] is not None: s.lastSH = s.ph[k - 1]
        if s.pl[k - 1] is not None: s.lastSL = s.pl[k - 1]

def crt_of(bars, k):
    """CRT للشمعة k (مكتملة) مقارنة بالشمعة k-1"""
    if k < 1: return 0, None
    _, o2, h2, l2, c2 = bars[k - 1]; _, o1, h1, l1, c1 = bars[k]
    if l1 < l2 and l2 < c1 < h2: return 1, h2
    if h1 > h2 and l2 < c1 < h2: return -1, l2
    return 0, None

def run_engine(m1, cfg):
    """يعيد تشغيل الاستراتيجية على كل التاريخ ويرجّع قائمة الأحداث (إشارة/تنفيذ/إغلاق/إلغاء)"""
    P = cfg['pip']
    chart = resample(m1, 300)
    eD = Engine(resample(m1, 86400, 7 * 3600), 'D')
    eM = Engine(resample(m1, 14400, 7 * 3600), '4H')
    eL = Engine(resample(m1, 3600), '1H')
    engines = [eD, eM, eL]
    closes = [b[4] for b in chart]
    cph, cpl = pivots(closes)
    m1t = [r[0] for r in m1]; import bisect
    lastSH = lastSL = shPrevSL = slPrevSH = None
    bias1 = 0; sh1Used = sl1Used = False
    crt1 = [(0, None), (0, None)]; crt4 = [(0, None), (0, None)]   # [cur, prev]
    atr = None; prevC = None
    qWin = max(1, round(cfg['mss_window_min'] / 5)); valid = max(1, round(cfg['pending_valid_min'] / 5))
    trades = []; events = []; lossDay = ''; lossCnt = 0
    for bi, (t, o, h, l, c) in enumerate(chart):
        tr = (h - l) if prevC is None else max(h - l, abs(h - prevC), abs(l - prevC))
        atr = tr if atr is None else (atr * 13 + tr) / 14; prevC = c
        if cph[bi] is not None: lastSH = cph[bi]; shPrevSL = lastSL
        if cpl[bi] is not None: lastSL = cpl[bi]; slPrevSH = lastSH
        pc = chart[bi - 1][4] if bi > 0 else c
        # ── تحديث الفريمات الكبيرة (آخر شمعات مكتملة فقط)
        kD = eD.advance(t)
        if kD is not None: eD.process(kD, bi, P)
        kL = eL.advance(t)
        if kL is not None and kL >= 2:
            lc1 = eL.bars[kL - 1][4]
            if eL.lastSH is not None and not sh1Used and lc1 > eL.lastSH: sh1Used = True; bias1 = 1
            if eL.lastSL is not None and not sl1Used and lc1 < eL.lastSL: sl1Used = True; bias1 = -1
            eL.process(kL, bi, P)
            if eL.ph[kL - 1] is not None: sh1Used = False
            if eL.pl[kL - 1] is not None: sl1Used = False
            crt1 = [crt_of(eL.bars, kL - 1), crt1[0]]
        kM = eM.advance(t)
        if kM is not None and kM >= 2:
            eM.process(kM, bi, P)
            crt4 = [crt_of(eM.bars, kM - 1), crt4[0]]
        # ── إدارة الصفقات دقيقة بدقيقة (شمعات الدقيقة داخل شمعة الـ5 دقائق)
        if trades:
            j0 = bisect.bisect_left(m1t, t); j1 = bisect.bisect_left(m1t, t + 300)
            for T in trades:
                if T['state'] == 1 or (T['state'] == 2 and bi > T['bar']):
                    for j in range(j0, j1):
                        _, _, bh, bl, _ = m1[j]
                        if T['state'] == 1 and ((T['dir'] == 1 and bl <= T['e']) or (T['dir'] == -1 and bh >= T['e'])):
                            T['state'] = 2; T['bar'] = bi
                            events.append(dict(id=T['id'] + ':fill', kind='fill', t=m1[j][0], T=dict(T)))
                        if T['state'] == 2:
                            d = T['dir']
                            if (bl <= T['sl']) if d == 1 else (bh >= T['sl']):
                                r = (T['sl'] - T['e']) * d / P; T['state'] = 0
                                events.append(dict(id=T['id'] + ':close', kind='close', t=m1[j][0], res=r, T=dict(T)))
                                if r <= -1:
                                    dk = fx_day(m1[j][0])
                                    if dk != lossDay: lossDay = dk; lossCnt = 0
                                    lossCnt += 1
                                break
                            if (bh >= T['tp']) if d == 1 else (bl <= T['tp']):
                                T['state'] = 0
                                events.append(dict(id=T['id'] + ':close', kind='close', t=m1[j][0], res=T['tpp'], T=dict(T)))
                                break
                    if T['state'] == 1 and bi - T['bar'] >= valid:
                        T['state'] = 0
                        events.append(dict(id=T['id'] + ':cancel', kind='cancel', t=t + 300, T=dict(T)))
            trades = [T for T in trades if T['state'] != 0]
        # ── المستويات + MSS
        mDir = 0; mE = None; mTag = ''; mStrong = False
        for E in engines:
            for L in E.levels:
                if L.dead: continue
                sup = L.role == 1; p = L.price
                touch = l <= p if sup else h >= p
                beyond = c < p if sup else c > p
                prevBeyond = bi > L.bar0 and ((pc < p) if sup else (pc > p))
                if beyond and prevBeyond:
                    L.q = False; L.breaks += 1
                    if L.breaks >= 3: L.dead = True
                    else:
                        L.kind = ('SBR' if sup else 'RBS') if L.breaks == 1 else 'QM'
                        L.role = -L.role; L.touched = False; L.bar0 = bi; L.t0 = t
                    continue
                if L.q:
                    if bi - L.qBar > qWin: L.q = False
                    else:
                        L.qHead = min(L.qHead, l) if sup else max(L.qHead, h)
                        if L.qSH is not None and ((c > L.qSH) if sup else (c < L.qSH)):
                            L.q = False
                            if mDir == 0 and (not cfg['trend_1h_filter'] or L.role == bias1):
                                d = L.role
                                lsOk = L.qLS is not None and (L.qLS - L.qHead) * d > 0 and (c - L.qLS) * d > 0
                                mE = L.qLS if lsOk else L.qHead + (c - L.qHead) * 0.5
                                mDir = d; mTag = f"{L.kind} {E.tag}" + (" · QM" if lsOk else " · 50%")
                                mStrong = (c - o) * d / P >= cfg['strong_body_atr'] * atr / P
                if not L.touched and touch:
                    L.touched = True
                    L.q = True; L.qHead = l if sup else h; L.qBar = bi
                    L.qSH = (lastSH if (lastSH is not None and lastSH > p) else None) if sup else (lastSL if (lastSL is not None and lastSL < p) else None)
                    L.qLS = shPrevSL if sup else slPrevSH
        for E in engines: E.levels = [L for L in E.levels if not L.dead]
        # ── إشارة جديدة
        dayStop = cfg['max_losses_per_day'] > 0 and fx_day(t) == lossDay and lossCnt >= cfg['max_losses_per_day']
        if mDir != 0 and len(trades) < cfg['max_positions'] and not dayStop:
            d = mDir; lim = mE
            strong = cfg['auto_market_entry'] and mStrong
            now = (c - lim) * d <= 0 or strong
            eP = c if now else lim
            best = None
            for E in engines:
                for L in E.levels:
                    if L.role == -d and ((L.price > c) if d == 1 else (L.price < c)):
                        if best is None or abs(L.price - c) < abs(best - c): best = L.price
            adj = 0 if now else (c - lim) * d / P
            tl = cfg['tp_max'] if best is None else (best - c) * d / P - 5 + adj
            crtOn = False
            if cfg['use_crt_target']:
                for cr in (crt1, crt4):
                    cd, ct = cr[0] if cr[0][0] != 0 else cr[1]
                    if cd == d and ct is not None:
                        dc = (ct - eP) * d / P - 5
                        if dc > tl: tl = dc; crtOn = True
            tpp = round(max(cfg['tp_min'], min(cfg['tp_max'], tl)))
            sld = cfg['stop_pips']
            T = dict(id=f"{t}:{d}:{round(eP, 2)}", dir=d, state=2 if now else 1, bar=bi, e=eP,
                     sl=eP - d * sld * P, tp=eP + d * tpp * P, tpp=tpp, sld=sld, strong=strong, now=now,
                     tag=mTag, crt=crtOn, t=t + 300, price=c)
            trades.append(T)
            events.append(dict(id=T['id'] + ':signal', kind='signal', t=t + 300, T=dict(T)))
    return events, bias1

# ───────────────────────── الرسائل ─────────────────────────
def fmt(x): return f"{x:.2f}"

def message(ev, cfg):
    T = ev['T']; tag = f"[{cfg['tag']}] " if cfg['tag'] else ''
    side = 'شراء' if T['dir'] == 1 else 'بيع'
    icon = '🟢' if T['dir'] == 1 else '🔴'
    sym = cfg['symbol']
    if ev['kind'] == 'signal':
        how = '⚡ دخول مباشر (فرصة قوية)' if T['strong'] else ('دخول فوري' if T['now'] else '⏳ أمر معلّق ' + ('Buy Limit' if T['dir'] == 1 else 'Sell Limit'))
        return (f"{tag}{icon} {side} {sym} · 5m\n"
                f"MSS + QM{' + CRT' if T['crt'] else ''} · {T['tag']}\n"
                f"{how} {fmt(T['e'])}\n"
                f"🎯 الهدف {fmt(T['tp'])}  (+{T['tpp']:.0f})\n"
                f"🛑 الستوب {fmt(T['sl'])}  (-{T['sld']:.0f})\n"
                f"R:R 1:{T['tpp'] / T['sld']:.1f}\n"
                f"السعر الحالي {fmt(T['price'])} · {ny_str(ev['t'])} نيويورك")
    if ev['kind'] == 'fill':
        return f"{tag}✅ تنفّذ الأمر المعلّق · {side} {sym} @ {fmt(T['e'])}\n🎯 {fmt(T['tp'])} · 🛑 {fmt(T['sl'])}"
    if ev['kind'] == 'cancel':
        return f"{tag}⌛ انلغى الأمر المعلّق ({side} @ {fmt(T['e'])}) — السعر ما رجع خلال {cfg['pending_valid_min']} دقيقة"
    r = ev['res']
    res = f"✓ ضرب الهدف +{r:.0f} نقطة" if r > 0 else f"✗ ضرب الستوب {r:.0f} نقطة"
    return f"{tag}🏁 إغلاق {side} {sym} من {fmt(T['e'])}\n{res}"

# ───────────────────────── التشغيل ─────────────────────────
def get_m1(mt5, symbol, n=50000):
    r = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, n)
    if r is None: raise RuntimeError(f"copy_rates failed: {mt5.last_error()}")
    return r

def main():
    cfg = load_cfg()
    if not cfg['telegram_token']:
        log("⚠ حط توكن البوت بملف config.json بخانة telegram_token وشغّل البرنامج من جديد")
        input("اضغط Enter للإغلاق..."); return
    if not cfg['chat_id']:
        cid = discover_chat_id(cfg)
        if not cid:
            log("⚠ ما لقيت Chat ID. افتح البوت على تيليجرام، اضغط Start وابعتله أي رسالة، وبعدين شغّل البرنامج من جديد")
            input("اضغط Enter للإغلاق..."); return
        cfg['chat_id'] = cid; save_cfg(cfg); log(f"Chat ID = {cid} (انحفظ بـconfig.json)")
    import MetaTrader5 as mt5
    if not mt5.initialize():
        log(f"⚠ ما قدرت أتصل بـMetaTrader 5: {mt5.last_error()} — تأكد إن MT5 مفتوح ومسجّل دخول"); input("Enter..."); return
    if mt5.symbol_info(cfg['symbol']) is None:
        cands = [s.name for s in mt5.symbols_get() if s.name.upper().startswith('XAUUSD')]
        if not cands: log("⚠ ما لقيت رمز الذهب XAUUSD بـMT5"); return
        cfg['symbol'] = cands[0]; save_cfg(cfg)
    mt5.symbol_select(cfg['symbol'], True)
    tick = mt5.symbol_info_tick(cfg['symbol'])
    srv_off = round((tick.time - time.time()) / 3600) * 3600 if tick else 0   # فرق توقيت سيرفر الوسيط عن UTC
    log(f"MT5 متصل · {cfg['symbol']} · توقيت السيرفر UTC{srv_off // 3600:+d}")

    state = json.load(open(STATE_PATH, encoding='utf-8')) if os.path.exists(STATE_PATH) else {}
    sent = set(state.get('sent', []))
    first = not sent
    tg_send(cfg, f"[{cfg['tag']}] 🤖 بوت MSNR اشتغل · {cfg['symbol']} 5m\nستوب {cfg['stop_pips']} · هدف {cfg['tp_min']}-{cfg['tp_max']} · حتى {cfg['max_positions']} صفقات")
    last_bar = None
    while True:
        try:
            r = get_m1(mt5, cfg['symbol'])
            m1 = [(int(x['time']) - srv_off, float(x['open']), float(x['high']), float(x['low']), float(x['close'])) for x in r]
            now_utc = time.time()
            # نشتغل بس على شموع مكتملة: نشيل شمعة الدقيقة الحالية وشمعة الـ5 دقائق اللي لسا مفتوحة
            cut = (int(now_utc) // 300) * 300
            m1 = [x for x in m1 if x[0] < cut]
            if m1 and m1[-1][0] != last_bar:
                last_bar = m1[-1][0]
                events, b1 = run_engine(m1, cfg)
                new = [e for e in events if e['id'] not in sent]
                if first:
                    sent.update(e['id'] for e in events); first = False
                    log(f"أول تشغيل: {len(events)} حدث قديم انحفظ بدون إرسال · اتجاه 1H = {'صاعد' if b1 == 1 else 'هابط' if b1 == -1 else 'محايد'}")
                else:
                    for e in new:
                        if e['kind'] != 'signal' and not cfg['send_fills_and_closes']: sent.add(e['id']); continue
                        if now_utc - e['t'] > 3 * 3600: sent.add(e['id']); continue   # أحداث قديمة جداً
                        if tg_send(cfg, message(e, cfg)):
                            sent.add(e['id']); log(f"انبعت: {e['kind']} {e['id']}")
                json.dump({'sent': sorted(sent)[-5000:]}, open(STATE_PATH, 'w', encoding='utf-8'))
        except Exception:
            log("خطأ:\n" + traceback.format_exc())
            try:
                mt5.shutdown(); time.sleep(5); mt5.initialize()
            except Exception: pass
        try:   # نبضة: بتخلّي مهمة المراقبة تعرف إن البوت شغّال (حتى بالويكند)
            with open(os.path.join(HERE, 'heartbeat'), 'w') as f: f.write(str(int(time.time())))
        except Exception: pass
        # استنى لبداية شمعة الـ5 دقائق الجاية + 5 ثواني
        wait = 300 - (time.time() % 300) + 5
        time.sleep(wait)

def test():
    """وضع التجربة: يتأكد من التوكن والـChat ID وMT5، ويبعت رسالة صفقة تجريبية"""
    cfg = load_cfg()
    if not cfg['telegram_token']:
        print("✗ التوكن فاضي: حطه بملف config.json بخانة telegram_token"); return
    try:
        me = tg_call(cfg['telegram_token'], 'getMe')
        print(f"✓ التوكن صحيح · البوت @{me['result']['username']}")
    except Exception as e:
        print(f"✗ التوكن غلط أو في مشكلة إنترنت: {e}"); return
    if not cfg['chat_id']:
        cid = discover_chat_id(cfg)
        if not cid:
            print("✗ ما لقيت Chat ID: افتح البوت على تيليجرام، اضغط Start، ابعتله رسالة، وجرّب من جديد"); return
        cfg['chat_id'] = cid; save_cfg(cfg); print(f"✓ Chat ID = {cid} (انحفظ)")
    price = None
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            t = mt5.symbol_info_tick(cfg['symbol']); price = t.bid if t else None
            print(f"✓ MT5 متصل · {cfg['symbol']} = {price}")
        else:
            print(f"⚠ MT5 مش متصل: {mt5.last_error()} (افتح MT5 قبل تشغيل البوت)")
    except Exception as e:
        print(f"⚠ MT5: {e}")
    p = price or 4280.00; P = cfg['pip']
    demo = dict(kind='signal', t=time.time(), T=dict(dir=1, strong=False, now=False, e=p - 3.0, tp=p - 3.0 + 150 * P, sl=p - 3.0 - 50 * P,
                tpp=150, sld=50, crt=True, tag='V 4H · QM', price=p))
    ok = tg_send(cfg, "🧪 رسالة تجربة — مش صفقة حقيقية\n\n" + message(demo, cfg))
    print("✓ انبعتت رسالة التجربة، شوف تيليجرام" if ok else "✗ ما انبعتت الرسالة — شوف bot.log")

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        test(); input("\nاضغط Enter للإغلاق...")
    else:
        main()
