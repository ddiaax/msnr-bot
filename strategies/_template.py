# -*- coding: utf-8 -*-
"""
قالب استراتيجية جديدة
────────────────────
1) انسخ هالملف باسم جديد، مثلاً: strategies/breakout.py
2) اكتب منطقك بدالة run
3) ضيف قسم بنفس الاسم بـstrategies.json:   "breakout": {"enabled": true, "tag": "BRK", ...}
البوت بيلقطها لحاله.

كل حدث (event) لازم يكون فيه:
  id    نص فريد وثابت (مثلاً وقت الشمعة + الاتجاه) — حتى ما تنبعت الرسالة مرتين
  kind  'signal' أو 'fill' أو 'close' أو 'cancel'
  t     وقت الحدث (ثواني UTC)
  وأي معلومات بدك تعرضها بالرسالة
"""

DEFAULTS = {"tag": "NEW"}

def run(m1, cfg):
    """m1 = شموع الدقيقة للذهب [(t, open, high, low, close)] من الأقدم للأحدث"""
    events = []
    # مثال بسيط جداً (مش استراتيجية حقيقية): تنبيه إذا شمعة دقيقة تحركت أكثر من 5$
    # for t, o, h, l, c in m1[-60:]:
    #     if h - l > 5:
    #         events.append(dict(id=f"big:{t}", kind='signal', t=t + 60, move=h - l, price=c))
    return events

def message(ev, cfg):
    return f"[{cfg['tag']}] حركة كبيرة {ev['move']:.2f}$ · السعر {ev['price']:.2f}"
