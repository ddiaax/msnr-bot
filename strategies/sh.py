# -*- coding: utf-8 -*-
"""استراتيجية SH v3 (Sharpness Signals v3 — نسخة TradingView/سكالب) على السحابة.
نفس محرك sh_bot بالضبط (strategies/sh_engine_tv.py)، بس الأسعار من Twelve Data بدل MT5.
الأوقات هون كلها UTC، فالفرق عن UTC = 0."""
import os, sys, re, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sh_engine_tv import run_engine_tv

DEFAULTS = {"tag": "SH v3", "accountSize": 10000, "riskPct": 0.5, "contract": 100.0, "historyBarsTV": 15000}

def _resample(bars, sec):
    out = []; cur = None
    for b in bars:
        k = b["time"] // sec
        if cur is None or k != cur["k"]:
            if cur: out.append({x: cur[x] for x in ("time", "open", "high", "low", "close", "volume")})
            cur = dict(k=k, time=k * sec, open=b["open"], high=b["high"], low=b["low"], close=b["close"], volume=b["volume"])
        else:
            cur["high"] = max(cur["high"], b["high"]); cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]; cur["volume"] += b["volume"]
    if cur: out.append({x: cur[x] for x in ("time", "open", "high", "low", "close", "volume")})
    return out

def run(m1, cfg):
    P = cfg["tv"]
    vol = cfg.get("_vol", {})
    bars = [dict(time=t, open=o, high=h, low=l, close=c, volume=float(vol.get(t, 0.0))) for t, o, h, l, c in m1]
    sub = P["scSubTF"] if P["scalp"] else P["subTF"]
    mainTF = P["scMainTF"] if P["scalp"] else P["mainTF"]
    n = cfg.get("historyBarsTV", 15000)
    chart = bars if sub == 1 else _resample(bars, sub * 60)
    chart = chart[-n:]
    main = _resample(bars, mainTF * 60)[-(n * sub // mainTF + 300):]
    h4 = _resample(bars, 14400)[-400:]
    evs = run_engine_tv(chart, main, h4, P, 0)
    out = []
    for ev in evs:
        tr = ev["trade"]
        ev = dict(ev)
        ev["id"] = f"{ev['kind']}|{tr['sig_time']}|{tr['dir']}|{ev['bar_time']}"
        ev["t"] = ev["bar_time"] + sub * 60          # وقت إغلاق الشمعة
        ev["kind_std"] = ev["kind"]
        out.append(ev)
    # نفس أسماء أحداث بوت MSNR: filled→fill · cancelled→cancel · closed→close · breakeven بيضل
    for ev in out:
        ev["kind"] = {"filled": "fill", "cancelled": "cancel", "closed": "close"}.get(ev["kind"], ev["kind"])
    return out

def _lots(risk, cfg):
    cash = cfg["accountSize"] * cfg["riskPct"] / 100
    return cash / (risk * cfg.get("contract", 100.0)) if risk > 0 else 0

def message(ev, cfg):
    tr = ev["trade"]; sym = "XAUUSD"; d = tr["dir"]; k = ev["kind_std"]
    tag = f"[{cfg['tag']}] "
    side = "🟢 BUY" if d == 1 else "🔴 SELL"
    t = dt.datetime.utcfromtimestamp(ev["bar_time"]).strftime("%Y-%m-%d %H:%M") + " UTC"
    if k == "signal":
        head = ("⚡ " if tr["scalp"] else "") + f"{side} {sym}  [{tr['grade']} {tr['score']}/6]" + \
               ("  —  أمر معلّق LIMIT (إعادة اختبار)" if tr["pending"] else "  —  دخول الآن")
        rv = "-" if tr["rvol"] != tr["rvol"] else f"{tr['rvol']:.1f}"
        dl = "-" if tr["delta"] != tr["delta"] else f"{tr['delta'] * 100:.0f}%"
        rm = "مفتوحة" if tr["roomR"] != tr["roomR"] else f"{tr['roomR']:.1f}R"
        return (f"{tag}{head}\n"
                f"الدخول: {tr['entry']:.2f}\n"
                f"الستوب: {tr['sl']:.2f}  ({tr['risk']:.2f}$)\n"
                f"الهدف: {tr['tp']:.2f}  (1:{tr['rr']:g})\n"
                f"اللوت المقترح: {_lots(tr['risk'], cfg):.2f}  (مخاطرة {cfg['riskPct']}%)\n"
                f"RVOL: {rv}  |  Δ: {dl}  |  مسافة: {rm}\n"
                f"المستوى المكسور: {tr['level']:.2f}\n"
                f"🕒 {t}")
    if k == "filled":
        return f"{tag}✔️ {side} {sym}: تفعّل الأمر المعلّق عند {tr['entry']:.2f}\nالستوب {tr['sl']:.2f} | الهدف {tr['tp']:.2f}"
    if k == "cancelled":
        return f"{tag}✖️ {side} {sym}: أُلغي الأمر المعلّق عند {tr['entry']:.2f} (لم يرجع السعر)"
    if k == "breakeven":
        return f"{tag}🔒 {side} {sym}: انقل الستوب إلى الدخول {tr['entry']:.2f} (Break-Even)"
    if k == "closed":
        icon = {"TP": "✅", "SL": "❌", "BE": "⚪", "TRAIL": "✅"}[ev["result"]]
        return (f"{tag}{icon} {side} {sym}: أُغلقت الصفقة — {ev['result']} ({ev['R']:+.2f}R)\n"
                f"الدخول {tr['entry']:.2f} | الستوب الأصلي {tr['sl0']:.2f} | الهدف {tr['tp']:.2f}")
    return f"{tag}{k}"
