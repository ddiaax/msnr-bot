# -*- coding: utf-8 -*-
"""
MSNR Pro — محرك موسّع (نفس منطق مؤشر MSNR Scalper Pro):
  • MSS + QM مع نقاط توافق 0-11 (اتجاه 1H/4H · Premium/Discount · افتتاح اليوم · Killzone · CRT ·
    Displacement · مستوى قوي · سحب سيولة · FVG · EMA الساعة)
  • صفقات A+ (توافق عالي + CRT بنفس الاتجاه) بهدف أكبر
  • إدارة: RR (هدف 2× أو 3× الستوب + قفل ربح) أو ثابت (قفل ربح + ستوب متحرك اختياري)
  • دخول CRT مستقل (سحب سيولة شمعة 1H/4H) · هدف EQH/EQL
كل إضافة إلها إعداد، وإذا انطفت بيرجع السلوك للنسخة الأصلية.
"""
import bisect, math, datetime
from msnr_bot import resample, pivots, fx_day, nyoff, ny_str, fmt, crt_of

PRO_DEFAULTS = {
    "tag": "MSNR", "symbol": "XAUUSD", "pip": 0.1,
    "exit_mode": "rr",            # rr | fixed
    "stop_pips": 30, "tp_min": 100, "tp_max": 300,
    "rr_b": 2.0, "rr_a": 3.0, "rr_be": 1.0,
    "fx_be": 30, "fx_lock": 10, "fx_trail": 0,
    "max_positions": 6, "max_losses_per_day": 3,
    "trend_1h_filter": True, "use_crt_target": True, "use_eqt": True,
    "min_conf": 5, "a_plus": True, "a_conf": 6, "tp_max_a": 400, "a_be": 60,
    "crt_entry": True, "crt_sl_min": 30, "crt_sl_max": 150, "crt_rr": 1.5, "crt_buf": 5,
    "crt_be_frac": 0.5, "crt_valid_min": 240,
    "auto_market_entry": False, "strong_body_atr": 1.5,
    "pending_valid_min": 180, "mss_window_min": 120,
    "pd_bars": 576, "disp_mult": 1.0, "eq_tol": 15, "ema_len": 200, "fvg_age": 48,
    "send_fills_and_closes": True,
}
LIQD = 30.0; ACTTOL = 10.0

class Level:
    __slots__ = ('price', 'role', 'kind', 't0', 'bar0', 'breaks', 'touched', 'dead', 'q', 'qHead', 'qSH', 'qLS', 'qBar',
                 'active', 'breakout', 'liq', 'opp')
    def __init__(s, price, role, kind, t0, bar0, active=False, opp=None):
        s.price = price; s.role = role; s.kind = kind; s.t0 = t0; s.bar0 = bar0
        s.breaks = 0; s.touched = False; s.dead = False; s.q = False; s.qHead = s.qSH = s.qLS = None; s.qBar = 0
        s.active = active; s.breakout = False; s.liq = 0; s.opp = opp
    def score(s): return int(s.active) + int(s.breakout) + int(s.liq > 0)

class Engine:
    def __init__(s, bars, tag):
        s.bars = bars; s.tag = tag; s.levels = []
        s.lastSH = s.lastSL = s.lastBullGap = s.lastBearGap = None
        s.ph, s.pl = pivots([b[4] for b in bars])
        s.times = [b[0] for b in bars]; s.ptr = 0
    def advance(s, t):
        k = s.ptr
        while k + 1 < len(s.times) and s.times[k + 1] <= t: k += 1
        new = k != s.ptr; s.ptr = k
        return k if new else None
    def process(s, k, bi, P):
        if k < 2: return
        t1, o1, h1, l1, c1 = s.bars[k - 1]
        t2, o2, h2, l2, c2 = s.bars[k - 2]
        for L in s.levels:                                  # سيولة مخفية + كسر آخر نقطة معاكسة
            if not L.touched and not L.dead and t1 > L.t0:
                if L.role == 1:
                    if l1 > L.price and l1 - L.price <= LIQD * P: L.liq += 1
                    if not L.breakout and L.opp is not None and c1 > L.opp: L.breakout = True
                else:
                    if h1 < L.price and L.price - h1 <= LIQD * P: L.liq += 1
                    if not L.breakout and L.opp is not None and c1 < L.opp: L.breakout = True
        if s.lastBullGap is not None and c1 < s.lastBullGap: s.lastBullGap = None
        if s.lastBearGap is not None and c1 > s.lastBearGap: s.lastBearGap = None
        lp = None
        if c2 > o2 and c1 < o1: lp, role, kind = c2, -1, 'A'
        elif c2 < o2 and c1 > o1: lp, role, kind = c2, 1, 'V'
        elif c2 > o2 and c1 > o1: lp, role, kind = c2, 1, 'Gap'
        elif c2 < o2 and c1 < o1: lp, role, kind = c2, -1, 'Gap'
        if lp is not None:
            tol = ACTTOL * P; lo = min(l1, l2); hi = max(h1, h2)
            dup = act = False
            for L in s.levels:
                if L.dead: continue
                if L.role == role and abs(L.price - lp) <= P: dup = True
                if L.t0 < t2 and ((lo - tol <= L.price <= lp + tol) if role == 1 else (lp - tol <= L.price <= hi + tol)): act = True
            if not dup:
                opp = None
                if role == 1:
                    if s.lastSH is not None and s.lastSH > c1: opp = s.lastSH
                    if s.lastBearGap is not None and s.lastBearGap > c1 and (opp is None or s.lastBearGap < opp): opp = s.lastBearGap
                else:
                    if s.lastSL is not None and s.lastSL < c1: opp = s.lastSL
                    if s.lastBullGap is not None and s.lastBullGap < c1 and (opp is None or s.lastBullGap > opp): opp = s.lastBullGap
                s.levels.append(Level(lp, role, kind, t2, bi, act, opp))
                if kind == 'Gap':
                    if role == 1: s.lastBullGap = lp
                    else: s.lastBearGap = lp
                if len(s.levels) > 60: s.levels.pop(0)
        if s.ph[k - 1] is not None: s.lastSH = s.ph[k - 1]
        if s.pl[k - 1] is not None: s.lastSL = s.pl[k - 1]

def _ema(vals, n):
    out = []; e = None; a = 2.0 / (n + 1)
    for v in vals:
        e = v if e is None else e + a * (v - e); out.append(e)
    return out

def run_engine_pro(m1, cfg):
    C = dict(PRO_DEFAULTS); C.update(cfg); cfg = C
    P = cfg['pip']; isRR = cfg['exit_mode'] == 'rr'
    chart = resample(m1, 300)
    eD = Engine(resample(m1, 86400, 7 * 3600), 'D')
    eM = Engine(resample(m1, 14400, 7 * 3600), '4H')
    eL = Engine(resample(m1, 3600), '1H')
    engines = [eD, eM, eL]
    ema1 = _ema([b[4] for b in eL.bars], cfg['ema_len'])
    H = [b[2] for b in chart]; Lw = [b[3] for b in chart]; O = [b[1] for b in chart]; Cl = [b[4] for b in chart]
    cph, cpl = pivots(Cl)
    m1t = [r[0] for r in m1]
    lastSH = lastSL = shPrevSL = slPrevSH = None
    bias = 0; shU = slU = False; bias1 = 0; sh1U = sl1U = False
    crt1 = [(0, None), (0, None)]; crt4 = [(0, None), (0, None)]
    eqH = eqL = pvH = pvL = None
    fvgB = fvgS = None           # (bar, lo/hi edge)
    atr = None; prevC = None
    qWin = max(1, round(cfg['mss_window_min'] / 5))
    trades = []; events = []; lossDay = ''; lossCnt = 0
    from collections import deque
    for bi, (t, o, h, l, c) in enumerate(chart):
        tr = (h - l) if prevC is None else max(h - l, abs(h - prevC), abs(l - prevC))
        atr = tr if atr is None else (atr * 13 + tr) / 14; prevC = c
        if cph[bi] is not None:
            lastSH = cph[bi]; shPrevSL = lastSL
            if pvH is not None and abs(cph[bi] - pvH) <= cfg['eq_tol'] * P: eqH = max(cph[bi], pvH)
            pvH = cph[bi]
        if cpl[bi] is not None:
            lastSL = cpl[bi]; slPrevSH = lastSH
            if pvL is not None and abs(cpl[bi] - pvL) <= cfg['eq_tol'] * P: eqL = min(cpl[bi], pvL)
            pvL = cpl[bi]
        if eqH is not None and c > eqH: eqH = None
        if eqL is not None and c < eqL: eqL = None
        if bi >= 2 and l > H[bi - 2] and Cl[bi - 1] > O[bi - 1]: fvgB = (bi, H[bi - 2])
        if bi >= 2 and h < Lw[bi - 2] and Cl[bi - 1] < O[bi - 1]: fvgS = (bi, Lw[bi - 2])
        if fvgB and c < fvgB[1]: fvgB = None
        if fvgS and c > fvgS[1]: fvgS = None
        pc = Cl[bi - 1] if bi > 0 else c
        # ── الفريمات الكبيرة
        kD = eD.advance(t)
        if kD is not None: eD.process(kD, bi, P)
        newL = newM = False
        kL = eL.advance(t)
        if kL is not None and kL >= 2:
            lc1 = eL.bars[kL - 1][4]
            if eL.lastSH is not None and not sh1U and lc1 > eL.lastSH: sh1U = True; bias1 = 1
            if eL.lastSL is not None and not sl1U and lc1 < eL.lastSL: sl1U = True; bias1 = -1
            eL.process(kL, bi, P)
            if eL.ph[kL - 1] is not None: sh1U = False
            if eL.pl[kL - 1] is not None: sl1U = False
            crt1 = [crt_of(eL.bars, kL - 1), crt1[0]]; newL = True
        kM = eM.advance(t)
        if kM is not None and kM >= 2:
            mc1 = eM.bars[kM - 1][4]
            if eM.lastSH is not None and not shU and mc1 > eM.lastSH: shU = True; bias = 1
            if eM.lastSL is not None and not slU and mc1 < eM.lastSL: slU = True; bias = -1
            eM.process(kM, bi, P)
            if eM.ph[kM - 1] is not None: shU = False
            if eM.pl[kM - 1] is not None: slU = False
            crt4 = [crt_of(eM.bars, kM - 1), crt4[0]]; newM = True
        crt1D, crt1T = crt1[0] if crt1[0][0] != 0 else crt1[1]
        crt4D, crt4T = crt4[0] if crt4[0][0] != 0 else crt4[1]
        # ── أدوات التوافق
        w0 = max(0, bi - cfg['pd_bars'] + 1)
        eq = (max(H[w0:bi + 1]) + min(Lw[w0:bi + 1])) / 2
        dOpen = eD.bars[eD.ptr][1] if eD.bars else c
        nyh = ((t + nyoff(t)) % 86400) / 3600
        inKZ = 2 <= nyh < 5 or 8 <= nyh < 11
        dispOk = abs(c - o) >= cfg['disp_mult'] * atr
        lo12 = min(Lw[max(0, bi - 11):bi + 1]); hi12 = max(H[max(0, bi - 11):bi + 1])
        lo48p = min(Lw[max(0, bi - 59):bi - 11]) if bi >= 13 else None
        hi48p = max(H[max(0, bi - 59):bi - 11]) if bi >= 13 else None
        sweepDn = lo48p is not None and lo12 < lo48p; sweepUp = hi48p is not None and hi12 > hi48p
        fvgBull = fvgB is not None and bi - fvgB[0] <= cfg['fvg_age']
        fvgBear = fvgS is not None and bi - fvgS[0] <= cfg['fvg_age']
        kl = eL.ptr - 1
        e1 = ema1[kl] if kl >= 0 else c
        def conf(d, sc):
            return ((d == bias1) + (d == bias) + ((c < eq) if d == 1 else (c > eq)) + ((c < dOpen) if d == 1 else (c > dOpen))
                    + inKZ + (crt1D == d or crt4D == d) + dispOk + (sc >= 2) + (sweepDn if d == 1 else sweepUp)
                    + (fvgBull if d == 1 else fvgBear) + ((c > e1) if d == 1 else (c < e1)))
        # ── إدارة الصفقات دقيقة بدقيقة
        if trades:
            j0 = bisect.bisect_left(m1t, t); j1 = bisect.bisect_left(m1t, t + 300)
            for T in trades:
                if T['state'] == 1 or (T['state'] == 2 and bi > T['bar']):
                    for j in range(j0, j1):
                        _, _, bh, bl, _ = m1[j]
                        d = T['dir']
                        if T['state'] == 1 and ((d == 1 and bl <= T['e']) or (d == -1 and bh >= T['e'])):
                            T['state'] = 2; T['bar'] = bi; T['best'] = T['e']
                            events.append(dict(id=T['id'] + ':fill', kind='fill', t=m1[j][0], T=dict(T)))
                        if T['state'] == 2:
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
                            T['best'] = max(T['best'], bh) if d == 1 else min(T['best'], bl)
                            if T['bep'] > 0 and not T['be'] and (T['best'] - T['e']) * d >= T['bep'] * P:
                                T['be'] = True
                                lk = min(T['lock'], (T['best'] - T['e']) * d / P - 3)
                                T['sl'] = T['e'] + d * lk * P
                                events.append(dict(id=T['id'] + ':be', kind='be', t=m1[j][0], T=dict(T)))
                            if T['be'] and T['trail'] > 0:
                                ns = T['best'] - d * T['trail'] * P
                                if (ns - T['sl']) * d > 0: T['sl'] = ns
                    if T['state'] == 1 and bi - T['bar'] >= T['valid']:
                        T['state'] = 0
                        events.append(dict(id=T['id'] + ':cancel', kind='cancel', t=t + 300, T=dict(T)))
            trades = [T for T in trades if T['state'] != 0]
        # ── المستويات + MSS
        mDir = 0; mE = None; mTag = ''; mStrong = False; mConf = 0
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
                        L.liq = 0; L.breakout = True; L.active = True
                    continue
                if L.q:
                    if bi - L.qBar > qWin: L.q = False
                    else:
                        L.qHead = min(L.qHead, l) if sup else max(L.qHead, h)
                        if L.qSH is not None and ((c > L.qSH) if sup else (c < L.qSH)):
                            L.q = False
                            d = L.role
                            if mDir == 0 and (not cfg['trend_1h_filter'] or d == bias1):
                                cf = conf(d, L.score())
                                if cf >= cfg['min_conf']:
                                    lsOk = L.qLS is not None and (L.qLS - L.qHead) * d > 0 and (c - L.qLS) * d > 0
                                    mE = L.qLS if lsOk else L.qHead + (c - L.qHead) * 0.5
                                    mDir = d; mConf = cf; mTag = f"{L.kind} {E.tag}" + (" · QM" if lsOk else " · 50%")
                                    mStrong = (c - o) * d >= cfg['strong_body_atr'] * atr
                if not L.touched and touch:
                    L.touched = True
                    L.q = True; L.qHead = l if sup else h; L.qBar = bi
                    L.qSH = (lastSH if (lastSH is not None and lastSH > p) else None) if sup else (lastSL if (lastSL is not None and lastSL < p) else None)
                    L.qLS = shPrevSL if sup else slPrevSH
        for E in engines: E.levels = [L for L in E.levels if not L.dead]
        # ── CRT مستقل
        crtDir = 0; crt = None
        if cfg['crt_entry']:
            for isNew, cr, bars, k, tag in ((newM, crt4, eM.bars, eM.ptr, '4H'), (newL, crt1, eL.bars, eL.ptr, '1H')):
                if not isNew or crtDir != 0: continue
                d, tg = cr[0]
                if d == 0 or tg is None: continue
                _, _, h1, l1, _ = bars[k - 1]
                mid = (h1 + l1) / 2
                e = min(c, mid) if d == 1 else max(c, mid)
                sl = l1 - cfg['crt_buf'] * P if d == 1 else h1 + cfg['crt_buf'] * P
                tp = tg - d * 5 * P
                sd = abs(e - sl) / P; td = (tp - e) * d / P
                if d == bias and (not cfg['trend_1h_filter'] or d == bias1) and cfg['crt_sl_min'] <= sd <= cfg['crt_sl_max'] and td >= sd * cfg['crt_rr']:
                    crtDir = d; crt = (e, sl, tp, tag)
        # ── إشارة جديدة
        dayStop = cfg['max_losses_per_day'] > 0 and fx_day(t) == lossDay and lossCnt >= cfg['max_losses_per_day']
        sDir = 0
        if len(trades) < cfg['max_positions'] and not dayStop:
            sDir = mDir if mDir != 0 else crtDir
        if sDir != 0:
            d = sDir; isM = mDir != 0
            if isM:
                lim = mE
                strong = cfg['auto_market_entry'] and mStrong
                now = (c - lim) * d <= 0 or strong
                eP = c if now else lim
                sld = cfg['stop_pips']
                best = None
                for E in engines:
                    for L in E.levels:
                        if L.role == -d and ((L.price > c) if d == 1 else (L.price < c)):
                            if best is None or abs(L.price - c) < abs(best - c): best = L.price
                adj = (c - lim) * d / P
                tl = cfg['tp_max'] if best is None else (best - c) * d / P - 5 + adj
                crtOn = False
                if cfg['use_crt_target']:
                    for cd, ct in ((crt1D, crt1T), (crt4D, crt4T)):
                        if cd == d and ct is not None:
                            dc = (ct - lim) * d / P - 5
                            if dc > tl: tl = dc; crtOn = True
                if cfg['use_eqt']:
                    eqT = eqH if d == 1 else eqL
                    if eqT is not None and (eqT - lim) * d > 0:
                        dq = (eqT - lim) * d / P - 5
                        if dq > tl: tl = dq
                aTier = cfg['a_plus'] and mConf >= cfg['a_conf'] and ((crt1D == d and crt1T is not None) or (crt4D == d and crt4T is not None))
                topT = cfg['tp_max_a'] if aTier else cfg['tp_max']
                tpp = round(max(cfg['tp_min'], min(topT, tl)))
                bep = cfg['a_be'] if aTier else cfg['fx_be']; lock = cfg['fx_lock']; trail = cfg['fx_trail']
                if isRR:
                    tpp = round(sld * (cfg['rr_a'] if aTier else cfg['rr_b']))
                    bep = round(sld * cfg['rr_be']) if cfg['rr_be'] > 0 else 0; lock = 10; trail = 0
                valid = max(1, round(cfg['pending_valid_min'] / 5))
                setup = 'MSS + QM' + (' + CRT' if crtOn else ''); tag = mTag
                grade = ('A+ ' if aTier else 'QM ') + f"{mConf}/11"
                sl = eP - d * sld * P
            else:
                e, slp, tpv, ctag = crt
                lim = e; strong = False
                now = (c - lim) * d <= 0
                eP = c if now else lim
                sld = abs(eP - slp) / P
                tpp = round((tpv - eP) * d / P)
                bep = round(tpp * cfg['crt_be_frac']) if cfg['crt_be_frac'] > 0 else 0; lock = 10; trail = 0
                valid = max(1, round(cfg['crt_valid_min'] / 5))
                setup = 'CRT'; tag = f"CRT {ctag}"; grade = 'CRT'; aTier = False; mConf = 0
                sl = slp
            T = dict(id=f"{t}:{d}:{round(eP, 2)}", dir=d, state=2 if now else 1, bar=bi, e=eP, sl=sl, tp=eP + d * tpp * P,
                     tpp=tpp, sld=round(sld), strong=strong, now=now, tag=tag, setup=setup, grade=grade, conf=mConf,
                     aplus=aTier, bep=bep, lock=lock, trail=trail, best=eP, be=False, valid=valid, t=t + 300, price=c)
            trades.append(T)
            events.append(dict(id=T['id'] + ':signal', kind='signal', t=t + 300, T=dict(T)))
    return events, bias1

def message_pro(ev, cfg):
    T = ev['T']; tag = f"[{cfg.get('tag', 'MSNR')}] "
    side = 'شراء' if T['dir'] == 1 else 'بيع'; icon = '🟢' if T['dir'] == 1 else '🔴'
    sym = cfg.get('symbol', 'XAUUSD')
    if ev['kind'] == 'signal':
        how = '⚡ دخول مباشر (فرصة قوية)' if T['strong'] else ('دخول فوري' if T['now'] else '⏳ أمر معلّق ' + ('Buy Limit' if T['dir'] == 1 else 'Sell Limit'))
        lines = [f"{tag}{icon} {side} {sym} · 5m  [{T['grade']}]",
                 f"{T['setup']} · {T['tag']}",
                 f"{how} {fmt(T['e'])}",
                 f"🎯 الهدف {fmt(T['tp'])}  (+{T['tpp']:.0f})",
                 f"🛑 الستوب {fmt(T['sl'])}  (-{T['sld']:.0f})",
                 f"R:R 1:{T['tpp'] / max(T['sld'], 1):.1f}"]
        if T['bep'] > 0:
            lines.append(f"🔒 بعد +{T['bep']:.0f} الستوب بينتقل لربح +{max(0, min(T['lock'], T['bep'] - 3)):.0f}")
        lines.append(f"السعر الحالي {fmt(T['price'])} · {ny_str(ev['t'])} نيويورك")
        return '\n'.join(lines)
    if ev['kind'] == 'fill':
        return f"{tag}✅ تنفّذ الأمر المعلّق · {side} {sym} @ {fmt(T['e'])}\n🎯 {fmt(T['tp'])} · 🛑 {fmt(T['sl'])}"
    if ev['kind'] == 'be':
        return f"{tag}🔒 {side} {sym}: انقل الستوب لـ {fmt(T['sl'])} (ربح مضمون)"
    if ev['kind'] == 'cancel':
        return f"{tag}⌛ انلغى الأمر المعلّق ({side} @ {fmt(T['e'])}) — السعر ما رجع"
    r = ev['res']
    res = f"✓ +{r:.0f} نقطة" if r > 0.5 else ("⚪ على الدخول" if r > -1 else f"✗ {r:.0f} نقطة")
    return f"{tag}🏁 إغلاق {side} {sym} من {fmt(T['e'])}\n{res}"
