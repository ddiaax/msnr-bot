# -*- coding: utf-8 -*-
"""
نسخة بايثون من مؤشر TradingView "Sharpness Signals v3" (نسخة RVOL / الدلتا / التقييم).
تعمل على شموع مغلقة فقط، بنفس ترتيب المنطق في الكود الأصلي.
كل الأوقات الداخلة بتوقيت خادم MT5، ويتم تحويلها لـ UTC عبر utcOffsetHours.
"""
import math, bisect, datetime as dt

NAN = math.nan


def isnan(x):
    return x is None or (isinstance(x, float) and math.isnan(x))


def atr_rma(h, l, c, n=14):
    out = [NAN] * len(c)
    tr = [max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])) if i else h[0] - l[0] for i in range(len(c))]
    if len(c) >= n:
        out[n - 1] = sum(tr[:n]) / n
        for i in range(n, len(c)):
            out[i] = (out[i - 1] * (n - 1) + tr[i]) / n
    return out


def sma(x, n):
    out = [NAN] * len(x); s = 0.0; q = []
    for i, v in enumerate(x):
        q.append(v); s += 0 if isnan(v) else v
        if len(q) > n:
            old = q.pop(0); s -= 0 if isnan(old) else old
        if len(q) == n and not any(isnan(v2) for v2 in q):
            out[i] = s / n
    return out


def ema(x, n):
    out = [NAN] * len(x); a = 2.0 / (n + 1); e = NAN
    for i, v in enumerate(x):
        if i == n - 1:
            e = sum(x[:n]) / n
        elif i >= n:
            e = e + a * (v - e)
        out[i] = e
    return out


def psych(p):
    fl = math.floor(p)
    return fl + (fl % 10) * 0.11


def pivots(c, atr, P):
    """{index التأكيد: [dict(lvl, raw, isH, strong)]}"""
    pvL, pvR = P["pvL"], P["pvR"]
    ev = {}
    for i in range(pvL + pvR + 1, len(c)):
        p = i - pvR
        a0 = atr[p]
        if isnan(a0):
            continue
        left = c[p - pvL:p]; right = c[p + 1:i + 1]
        for isH in (True, False):
            if isH and not (c[p] > max(left) and c[p] > max(right)):
                continue
            if (not isH) and not (c[p] < min(left) and c[p] < min(right)):
                continue
            if isH:
                strong = (c[p] - min(left)) >= P["depthATR"] * a0 and (c[p] - min(right)) >= P["depthATR"] * a0
            else:
                strong = (max(left) - c[p]) >= P["depthATR"] * a0 and (max(right) - c[p]) >= P["depthATR"] * a0
            lo = max(i - pvR - 2, 0); hi = i - max(pvR - 2, 0)
            win = [x for x in c[lo:hi + 1] if abs(x - c[p]) <= P["clusterTol"] * a0]
            mid = (max(win) + min(win)) / 2
            ev.setdefault(i, []).append(dict(lvl=psych(mid) if P["usePsych"] else mid, raw=c[p], isH=isH, strong=strong))
    return ev


def struct_trend(c):
    """f_structTrend على كل شمعة (قيمة tr[1])"""
    out = [0] * len(c); tr = 0; h = l = NAN; prev = 0
    for i in range(len(c)):
        if i >= 5:
            p = i - 2
            if c[p] > max(c[p - 3:p]) and c[p] > max(c[p + 1:i + 1]): h = c[p]
            if c[p] < min(c[p - 3:p]) and c[p] < min(c[p + 1:i + 1]): l = c[p]
        out[i] = prev
        if not isnan(h) and c[i] > h: tr = 1
        if not isnan(l) and c[i] < l: tr = -1
        prev = tr
    return out


def in_session(t_utc, sess):
    a, b = sess.split("-")
    m = t_utc.hour * 60 + t_utc.minute
    ma = int(a[:2]) * 60 + int(a[2:]); mb = int(b[:2]) * 60 + int(b[2:])
    return ma <= m < mb if ma < mb else (m >= ma or m < mb)


def run_engine_tv(chart, main, h4, P, utc_off):
    """
    chart: شموع الفريم الفرعي (فريم الشارت) — dict(time, open, high, low, close, volume)
    main : شموع الفريم الأساسي
    h4   : شموع 4 ساعات (لفلتر الهيكل)
    يعيد قائمة أحداث: signal / filled / cancelled / breakeven / closed
    """
    sc = P["scalp"]
    rr = P["scRR"] if sc else P["rr"]
    retestBars = P["scRetest"] if sc else P["retestBars"]
    maxTrades = P["scMaxTrades"] if sc else P["maxTrades"]
    minSLp = P["scMinSLpips"] if sc else P["minSLpips"]
    mainMin = P["scMainTF"] if sc else P["mainTF"]

    T = [b["time"] for b in chart]; O = [b["open"] for b in chart]; H = [b["high"] for b in chart]
    L = [b["low"] for b in chart]; C = [b["close"] for b in chart]; V = [float(b["volume"]) for b in chart]
    N = len(chart)
    A = atr_rma(H, L, C, 14)
    atrSma = sma(A, 50)
    UT = [dt.datetime.utcfromtimestamp(t - utc_off * 3600) for t in T]

    # ── مستويات الفرعي (فريم الشارت) ومستويات الأساسي (متاحة من أول شمعة بعد إغلاق شمعة الأساسي)
    sub = pivots(C, A, P)
    mc = [b["close"] for b in main]
    ma = atr_rma([b["high"] for b in main], [b["low"] for b in main], mc, 14)
    main_raw = pivots(mc, ma, P)
    mT = [b["time"] for b in main]
    mainEv = {}
    for k, lst in main_raw.items():
        j = bisect.bisect_left(T, mT[k] + mainMin * 60)
        if j < N:
            mainEv.setdefault(j, []).extend(lst)
    # EMA الأساسي [1] و هيكل 4H [1] حسب الشمعة الحاوية
    mE = ema(mc, P["emaLen"])
    hT = [b["time"] for b in h4]; hTr = struct_trend([b["close"] for b in h4])
    def contain(times, t):
        return bisect.bisect_right(times, t) - 1

    news = set(P.get("newsDates", []))
    todAvg = {}
    alpha = 2.0 / (P["rvolLen"] + 1)

    events = []
    act = []
    lastSubH = lastSubL = lastMainH = lastMainL = NAN
    tr = None
    tradesToday = winsToday = 0
    prevDay = None

    for i in range(1, N):
        a = A[i]
        if isnan(a):
            continue
        day = UT[i].date()
        if day != prevDay:
            if prevDay is not None:
                tradesToday = winsToday = 0
            prevDay = day

        # ── RVOL نفس الوقت (قبل التحديث)
        key = UT[i].hour * 60 + UT[i].minute
        prevAvg = todAvg.get(key)
        rvolTod = V[i] / prevAvg if prevAvg else NAN
        todAvg[key] = V[i] if prevAvg is None else prevAvg + (V[i] - prevAvg) * alpha
        if P["rvolMode"] == "same_time":
            rvol = rvolTod
        else:
            w = V[max(0, i - P["rvolLen"] + 1):i + 1]
            rvol = V[i] / (sum(w) / len(w)) if len(w) == P["rvolLen"] and sum(w) > 0 else NAN

        # ── الدلتا (فريم الدلتا = فريم الشارت ⇒ من الشمعة نفسها)
        def ud(k):
            up = V[k] if C[k] > O[k] else 0.0 if C[k] < O[k] else V[k] * 0.5
            return up, V[k] - up
        upV, dnV = ud(i)
        tot = upV + dnV
        ltfOK = tot > 0
        dRatio = (upV - dnV) / tot if ltfOK else NAN
        flowSum = 0.0
        for k in range(max(0, i - P["flowLen"] + 1), i + 1):
            u, dn = ud(k); flowSum += (u - dn) if (u + dn) > 0 else 0.0

        # ── الاتجاه والتذبذب
        km = contain(mT, T[i])
        emaMain = mE[km - 1] if km >= 1 else NAN
        emaDir = 0 if isnan(emaMain) else (1 if C[i] > emaMain else -1)
        kh = contain(hT, T[i])
        htf4 = hTr[kh] if kh >= 0 else 0
        minRisk = max(P["minSLatr"] * a, minSLp * P["pipSize"])
        regimeOK = (not P["useRegime"]) or (not isnan(atrSma[i]) and a >= atrSma[i] * P["regimeMult"])
        sessOK = (not P["useSessions"]) or any(P["sessions"][s]["on"] and in_session(UT[i], P["sessions"][s]["range"])
                                               for s in ("tokyo", "london", "newyork"))
        newsDay = P["useNews"] and day.isoformat() in news

        # ── الأمر المعلّق
        skipMgmt = False
        if tr and tr["pending"]:
            d = tr["dir"]
            proj = tr["entry"] + d * rr * tr["risk"]
            ranAway = H[i] >= proj if d == 1 else L[i] <= proj
            fill = L[i] <= tr["entry"] if d == 1 else H[i] >= tr["entry"]
            if fill:
                tr["pending"] = False; tr["fill"] = i
                events.append(dict(bar_time=T[i], kind="filled", trade=dict(tr)))
            elif i - tr["bar"] >= retestBars or ranAway:
                events.append(dict(bar_time=T[i], kind="cancelled", trade=dict(tr)))
                tr = None; tradesToday = max(tradesToday - 1, 0)

        # ── الصفقة المفتوحة
        if tr and not tr["pending"] and not skipMgmt:
            d = tr["dir"]
            hitSL = L[i] <= tr["sl"] if d == 1 else H[i] >= tr["sl"]
            hitTP = tr["tp"] is not None and i > tr["fill"] and (H[i] >= tr["tp"] if d == 1 else L[i] <= tr["tp"])
            res = None
            if hitSL:
                ex = (min(tr["sl"], O[i]) if d == 1 else max(tr["sl"], O[i])) if i > tr["fill"] else tr["sl"]
                R = (ex - tr["entry"]) * d / tr["risk"]
                res = "TRAIL" if R > 0.05 else "BE" if R >= -0.05 else "SL"
            elif hitTP:
                R = rr; res = "TP"
            if res:
                events.append(dict(bar_time=T[i], kind="closed", result=res, R=R, trade=dict(tr)))
                if R > 0.05:
                    winsToday += 1
                tr = None
            else:
                if P["useBE"] and not tr["be"] and (H[i] >= tr["entry"] + P["beAtR"] * tr["risk"] if d == 1
                                                     else L[i] <= tr["entry"] - P["beAtR"] * tr["risk"]):
                    tr["be"] = True; tr["sl"] = tr["entry"]
                    events.append(dict(bar_time=T[i], kind="breakeven", trade=dict(tr)))

        # ── مستويات جديدة (أساسي ثم فرعي) + الحد لكل فريم
        for e in mainEv.get(i, []):
            if e["isH"]: lastMainH = e["raw"]
            else: lastMainL = e["raw"]
            act.append(dict(e, isMain=True))
        for e in sub.get(i, []):
            if e["isH"]: lastSubH = e["raw"]
            else: lastSubL = e["raw"]
            act.append(dict(e, isMain=False))
        for isMain, cap in ((True, P["maxMain"]), (False, P["maxSub"])):
            n = sum(1 for x in act if x["isMain"] == isMain)
            while n > cap:
                for k, x in enumerate(act):
                    if x["isMain"] == isMain:
                        act.pop(k); break
                n -= 1

        # ── الكسر
        brkHi = brkLo = NAN; brkHiS = brkLoS = False
        subUp = mainUp = subDn = mainDn = False
        for j in range(len(act) - 1, -1, -1):
            lv = act[j]
            up = lv["isH"] and C[i] > lv["lvl"]
            dn = (not lv["isH"]) and C[i] < lv["lvl"]
            if up or dn:
                fU = up and C[i - 1] <= lv["lvl"]
                fD = dn and C[i - 1] >= lv["lvl"]
                if fU:
                    subUp = subUp or not lv["isMain"]; mainUp = mainUp or lv["isMain"]
                if fD:
                    subDn = subDn or not lv["isMain"]; mainDn = mainDn or lv["isMain"]
                tm = P["trigMode"]
                tfOK = tm == "both" or (tm == "sub" and not lv["isMain"]) or (tm == "main" and lv["isMain"])
                qOK = (not P["onlyStrong"]) or lv["strong"]
                if tfOK and qOK:
                    if fU and (isnan(brkHi) or lv["lvl"] > brkHi):
                        brkHi = lv["lvl"]; brkHiS = lv["strong"]
                    if fD and (isnan(brkLo) or lv["lvl"] < brkLo):
                        brkLo = lv["lvl"]; brkLoS = lv["strong"]
                act.pop(j)

        # ── الإشارة
        dayOK = tradesToday < maxTrades and (not P["stopAfterWin"] or winsToday == 0)
        if tr is not None or not dayOK or not sessOK or newsDay:
            continue
        body = abs(C[i] - O[i]); rng = H[i] - L[i]
        for d in (1, -1):
            lvl = brkHi if d == 1 else brkLo
            if tr is not None or isnan(lvl):
                continue
            bodyOK = (not P["useBody"]) or ((C[i] - O[i]) * d > 0 and rng > 0 and body / rng >= P["bodyMin"])
            far = (C[i] - lvl) * d > P["farATR"] * a
            skip = far and P["farMode"] == "skip"
            useLim = far and P["farMode"] == "limit"
            entry = lvl if useLim else C[i]
            sRef = lastSubL if d == 1 else lastSubH
            mRef = lastMainL if d == 1 else lastMainH
            if P["slMode"] == "sub": ref = sRef
            elif P["slMode"] == "main": ref = mRef
            else:
                sv = not isnan(sRef) and (entry - sRef) * d > 0; mv = not isnan(mRef) and (entry - mRef) * d > 0
                ref = ((max(sRef, mRef) if d == 1 else min(sRef, mRef)) if sv and mv else sRef if sv else mRef if mv else NAN)
            if isnan(ref):
                continue
            sl = ref - d * P["bufPips"] * P["pipSize"]
            risk = (entry - sl) * d
            if risk > 0 and risk < minRisk:
                risk = minRisk; sl = entry - d * risk
            slOK = risk > 0 and (P["maxSLatr"] <= 0 or risk <= P["maxSLatr"] * a)
            if not (bodyOK and not skip and slOK):
                continue
            structOK = htf4 != -d; emaOK = emaDir == d
            tmode = P["trendMode"]
            trendOK = tmode == "none" or (tmode == "struct4h" and structOK) or (tmode == "ema" and emaOK) or \
                      (tmode == "both" and structOK and emaOK)
            dists = [(x["lvl"] - entry) * d for x in act if x["isH"] == (d == 1) and (x["lvl"] - entry) * d > 0]
            roomR = min(dists) / risk if dists else NAN
            roomOK = (not P["useRoom"]) or isnan(roomR) or roomR >= P["minRoomR"]
            rvolOK = (not P["useRvol"]) or isnan(rvol) or (rvol >= P["rvolMin"] and (P["rvolMax"] <= 0 or rvol <= P["rvolMax"]))
            deltaOK = (not P["useDelta"]) or not ltfOK or dRatio * d >= P["deltaMin"]
            cumOK = (not P["useFlow"]) or not ltfOK or flowSum * d > 0
            isStrong = brkHiS if d == 1 else brkLoS
            conf = (subUp and mainUp) if d == 1 else (subDn and mainDn)
            volBig = not isnan(rvol) and rvol >= P["rvolStrong"]
            dBig = ltfOK and dRatio * d >= P["deltaStrong"]
            roomBig = isnan(roomR) or roomR >= rr
            score = sum([isStrong, conf, emaOK, volBig, dBig, roomBig])
            if not (trendOK and roomOK and regimeOK and rvolOK and deltaOK and cumOK and score >= P["minScore"]):
                continue
            grade = "A" if score >= 4 else "B" if score >= 2 else "C"
            tr = dict(dir=d, entry=entry, sl=sl, sl0=sl, tp=entry + d * rr * risk, risk=risk, pending=useLim,
                      bar=i, fill=i, be=False, sig_time=T[i], level=lvl, grade=grade, score=score,
                      rvol=rvol, delta=dRatio, roomR=roomR, rr=rr, scalp=sc)
            tradesToday += 1
            events.append(dict(bar_time=T[i], kind="signal", trade=dict(tr)))
    return events
